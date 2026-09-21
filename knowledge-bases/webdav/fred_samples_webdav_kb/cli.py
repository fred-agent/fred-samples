# Copyright Thales 2026
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
A developer tool: one synchronization run against a real share, without waiting
for a deployment to dispatch it.

It builds exactly the configuration a team's form will produce and calls exactly
the code a dispatched run will call. By default the only thing standing in for
Fred is the boundary, which logs what it would write; name a library with
`--library` and the documents really go there — and a second run against that
library is genuinely incremental, because the library itself is what says what
it already holds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import ssl
import time
from collections.abc import Sequence
from dataclasses import asdict

from fred_samples_webdav_kb.document_boundary import LoggingBoundary, open_boundary
from fred_samples_webdav_kb.report import RunReport
from fred_samples_webdav_kb.settings import (
    DEFAULT_PROFILE,
    INGESTION_PROFILES,
    ConfigurationError,
    Settings,
    read_settings,
)
from fred_samples_webdav_kb.synchronize import synchronize
from fred_samples_webdav_kb.webdav import (
    CA_FILE_ENV,
    TrustStoreError,
    WebDavSource,
    configured_ca_file,
    tls_policy,
)

# Never a command-line flag: an argument is visible to every process on the
# machine for as long as the run lasts. What follows is the variable's name,
# which the leak scanner cannot tell from the thing it names.
PASSWORD_ENV = "FRED_SAMPLES_WEBDAV_PASSWORD"  # nosec B105  # pragma: allowlist secret

# What a real cadence will do once Fred dispatches one. Shorter than any
# cadence Fred offers on purpose: this exists to watch a change land, not to
# stand in for a schedule.
DEFAULT_INTERVAL_SECONDS = 60


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fred-samples-webdav-kb-dev",
        description="Try this Knowledge Base without a Fred deployment.",
        epilog=(
            f"A share needing a password reads it from ${PASSWORD_ENV}. A "
            f"private certificate authority is added through ${CA_FILE_ENV}."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("declaration", help="print what `publish` would send to Fred")

    for name, help_text in (
        ("sync", "synchronize a share once, locally"),
        ("watch", "synchronize a share over and over, until stopped"),
    ):
        run = commands.add_parser(name, help=help_text)
        run.add_argument("--url", required=True, help="the folder, as in a browser")
        run.add_argument("--username", default="", help="for a share needing a sign-in")
        run.add_argument("--include", default="", help="default: **/*.md")
        run.add_argument("--exclude", default="")
        run.add_argument("--max-files", type=int, default=None)
        run.add_argument(
            "--profile",
            choices=INGESTION_PROFILES,
            default=DEFAULT_PROFILE,
            help="ingestion profile (default: medium)",
        )
        run.add_argument(
            "--trust-any-certificate",
            action="store_true",
            help=f"accept any certificate; prefer ${CA_FILE_ENV}",
        )
        run.add_argument(
            "--library",
            default="",
            help=(
                "write into this Fred library for real, instead of logging what "
                "would be written. Needs the pod configuration (config/.env)."
            ),
        )
        if name == "watch":
            run.add_argument(
                "--interval",
                type=int,
                default=DEFAULT_INTERVAL_SECONDS,
                help=f"seconds between passes (default: {DEFAULT_INTERVAL_SECONDS})",
            )
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if arguments.command == "declaration":
        # The same projection the image publishes, so what a team will be shown
        # can be read before anything is deployed.
        from fred_sdk.knowledge_base import KnowledgeBaseDeclaration

        from fred_samples_webdav_kb.knowledge_base import kb

        print(json.dumps(KnowledgeBaseDeclaration.of(kb).to_payload(), indent=2))
        return 0

    configuration: dict[str, object] = {
        "url": arguments.url,
        "username": arguments.username,
        "password": os.getenv(PASSWORD_ENV, ""),
        "include": arguments.include,
        "exclude": arguments.exclude,
        "profile": arguments.profile,
        "trust_any_certificate": bool(arguments.trust_any_certificate),
    }
    if arguments.max_files is not None:
        configuration["max_files"] = arguments.max_files

    try:
        settings = read_settings(configuration)
        verify = tls_policy(
            trust_any_certificate=settings.trust_any_certificate,
            ca_file=configured_ca_file(),
        )
    except ConfigurationError as error:
        print(f"{error.code}: {error}")
        return 2
    except TrustStoreError as error:
        print(f"ca_file_unreadable: {error}")
        return 2

    if arguments.command == "watch":
        return _watch(arguments, settings, verify)

    report = asyncio.run(_run(settings, verify, arguments.library))
    print(json.dumps(as_json(report), indent=2))
    return 0 if report.succeeded else 1


def _watch(
    arguments: argparse.Namespace,
    settings: Settings,
    verify: ssl.SSLContext | bool,
) -> int:
    """What Fred's schedule will do, until Fred's schedule exists.

    The handler is called exactly as a dispatched run calls it, so replacing
    this loop with a real cadence changes nothing on the other side of it.
    """
    watcher = logging.getLogger("watch")
    watcher.info(
        "watching %s every %ss — Ctrl-C to stop",
        settings.where.url,
        arguments.interval,
    )
    while True:
        report = asyncio.run(_run(settings, verify, arguments.library))
        watcher.info("%s", report.summary())
        for issue in report.errors:
            watcher.warning("  %s %s", issue.code, issue.subject or "")
        try:
            time.sleep(arguments.interval)
        except KeyboardInterrupt:
            watcher.info("stopped")
            return 0


def as_json(report: RunReport) -> dict[str, object]:
    """The run, in the shape a person reading a terminal wants it."""
    return {
        "summary": report.summary(),
        "succeeded": report.succeeded,
        "exhaustive": report.exhaustive,
        "discovered": report.discovered,
        "created": report.created,
        "updated": report.updated,
        "removed": report.removed,
        "unchanged": report.unchanged,
        "skipped": report.skipped,
        "warnings": [asdict(issue) for issue in report.warnings],
        "errors": [asdict(issue) for issue in report.errors],
        "metrics": report.metrics(),
    }


async def _run(
    settings: Settings,
    verify: ssl.SSLContext | bool,
    library_id: str,
) -> RunReport:
    source = WebDavSource(
        where=settings.where,
        username=settings.username,
        password=settings.password,
        verify=verify,
        max_file_bytes=settings.max_file_bytes,
    )
    # Named a library, and the pod environment is there: the documents really go
    # to Fred, through the same boundary a dispatched run uses. Without one this
    # stays a dry run, which is what makes the tool useful with no Fred at all.
    boundary = (
        open_boundary(library_id=library_id, profile=settings.profile)
        if library_id
        else LoggingBoundary(profile=settings.profile)
    )
    try:
        return await synchronize(
            settings=settings,
            source=source,
            boundary=boundary,
        )
    finally:
        await boundary.aclose()
        await source.aclose()
