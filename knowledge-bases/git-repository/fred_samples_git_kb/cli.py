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
An offline developer tool: one synchronization run against a real repository,
with no Fred and no deployment.

It builds exactly the configuration a team's form will produce and calls
exactly the code a dispatched run will call. The only thing standing in for
Fred is the library, which logs what it would write and remembers the cursor
for the length of the process — so a second run in the same process is
incremental, and a second invocation starts over.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from fred_samples_git_kb.git_source import (
    MIRROR_DIR_ENV,
    GitRepositorySource,
    default_mirror_root,
    mirror_for,
)
from fred_samples_git_kb.library import LoggingLibrary
from fred_samples_git_kb.report import RunReport
from fred_samples_git_kb.settings import ConfigurationError, Settings, read_settings
from fred_samples_git_kb.synchronize import synchronize

# Never a command-line flag: an argument is visible to every process on the
# machine for as long as the run lasts. What follows is the variable's name,
# which the leak scanner cannot tell from the thing it names.
TOKEN_ENV = "FRED_SAMPLES_GIT_TOKEN"  # nosec B105


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fred-samples-git-kb-dev",
        description="Try this Knowledge Base without a Fred deployment.",
        epilog=f"A private repository needs a token in ${TOKEN_ENV}.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("declaration", help="print what `publish` would send to Fred")

    sync = commands.add_parser("sync", help="synchronize a repository once, locally")
    sync.add_argument(
        "--repository",
        required=True,
        help="owner/name, group/project, or the address you copied from the browser",
    )
    sync.add_argument("--provider", default="github", help="github or gitlab")
    sync.add_argument("--host", default="", help="for an on-premises forge")
    sync.add_argument("--branch", default="", help="default: the repository's own")
    sync.add_argument("--subdirectory", default="", help="only synchronize this")
    sync.add_argument("--include", default="", help="default: **/*.md")
    sync.add_argument("--exclude", default="")
    sync.add_argument(
        "--mirror",
        default=os.getenv(MIRROR_DIR_ENV, ""),
        help=f"where to keep the local mirror (default: ${MIRROR_DIR_ENV}, else a cache dir)",
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if arguments.command == "declaration":
        # The same projection the image publishes, so what a team will be shown
        # can be read before anything is deployed.
        from fred_sdk.knowledge_base import KnowledgeBaseDeclaration

        from fred_samples_git_kb.knowledge_base import kb

        print(json.dumps(KnowledgeBaseDeclaration.of(kb).to_payload(), indent=2))
        return 0

    try:
        settings = read_settings(
            {
                "provider": arguments.provider,
                "host": arguments.host,
                "repository": arguments.repository,
                "branch": arguments.branch,
                "subdirectory": arguments.subdirectory,
                "include": arguments.include,
                "exclude": arguments.exclude,
                "token": os.getenv(TOKEN_ENV, ""),
            }
        )
    except ConfigurationError as error:
        print(f"{error.code}: {error}")
        return 2

    report = asyncio.run(
        _run(settings, Path(arguments.mirror) if arguments.mirror else None)
    )
    print(json.dumps(as_json(report), indent=2))
    return 0 if report.succeeded else 1


def as_json(report: RunReport) -> dict[str, object]:
    """The run, in the shape a person reading a terminal wants it."""
    return {
        "summary": report.summary(),
        "succeeded": report.succeeded,
        "exhaustive": report.exhaustive,
        "created": report.written_new,
        "updated": report.written_existing,
        "removed": report.retracted,
        "unchanged": report.unchanged,
        "skipped": report.skipped,
        "warnings": [asdict(issue) for issue in report.warnings],
        "errors": [asdict(issue) for issue in report.errors],
        "metrics": report.metrics(),
    }


async def _run(settings: Settings, mirror_root: Path | None):
    root = default_mirror_root(str(mirror_root) if mirror_root else "")
    source = GitRepositorySource(
        url=settings.forge.clone_url,
        branch=settings.branch,
        mirror=mirror_for(root, url=settings.forge.clone_url, branch=settings.branch),
        username=settings.forge.username,
        token=settings.token,
    )
    try:
        return await synchronize(
            settings=settings, source=source, library=LoggingLibrary()
        )
    finally:
        source.close()
