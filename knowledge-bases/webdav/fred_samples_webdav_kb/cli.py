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
`--library` and the documents really go there, through the same boundary a
dispatched run uses. The ledger is the real one either way, so a second run is
genuinely incremental.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import ssl
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from fred_samples_webdav_kb.document_boundary import LoggingBoundary, open_boundary
from fred_samples_webdav_kb.ledger import STATE_DIR_ENV, ledger_path_for
from fred_samples_webdav_kb.report import RunReport
from fred_samples_webdav_kb.settings import ConfigurationError, Settings, read_settings
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

    sync = commands.add_parser("sync", help="synchronize a share once, locally")
    sync.add_argument("--url", required=True, help="the folder, as in a browser")
    sync.add_argument("--username", default="", help="for a share needing a sign-in")
    sync.add_argument("--include", default="", help="default: **/*.md")
    sync.add_argument("--exclude", default="")
    sync.add_argument("--max-files", type=int, default=None)
    sync.add_argument(
        "--instance",
        default="dev",
        help="which ledger to use, so two shares can be tried side by side",
    )
    sync.add_argument(
        "--trust-any-certificate",
        action="store_true",
        help=f"accept any certificate; prefer ${CA_FILE_ENV}",
    )
    sync.add_argument(
        "--library",
        default="",
        help=(
            "write into this Fred library for real, instead of logging what "
            "would be written. Needs the pod environment (config/.env)."
        ),
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

    ledger_path = ledger_path_for(arguments.instance)
    print(f"ledger: {ledger_path}  (${STATE_DIR_ENV} moves it)")
    report = asyncio.run(_run(settings, verify, ledger_path, arguments.library))
    print(json.dumps(as_json(report), indent=2))
    return 0 if report.succeeded else 1


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
    ledger_path: Path,
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
    boundary = open_boundary(library_id=library_id) if library_id else LoggingBoundary()
    try:
        return await synchronize(
            settings=settings,
            source=source,
            boundary=boundary,
            ledger_path=ledger_path,
        )
    finally:
        await boundary.aclose()
        await source.aclose()
