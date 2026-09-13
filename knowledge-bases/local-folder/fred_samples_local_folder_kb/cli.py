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
An offline developer tool, and deliberately not part of the contract.

The contract's entry point is `knowledge_base_main` (see `__main__.py`), and it
needs a deployment: `publish` posts to Control Plane, `run` polls Temporal.
These two commands exercise the same declaration and the same handler with
neither, so you can try the sample on your own folder.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Sequence

from fred_sdk.contracts.models import TuningValue
from fred_sdk.knowledge_base import (
    KnowledgeBaseDeclaration,
    KnowledgeBaseRunContext,
    KnowledgeBaseRunOutcome,
)

from fred_samples_local_folder_kb.knowledge_base import kb

# Fred supplies these per run. Locally they are placeholders, so a hand-made
# run is recognisable in a log next to a real one.
LOCAL_TEAM_ID = "local-team"
DEFAULT_INSTANCE_ID = "local"
# Fred creates the library with the instance and puts its id in the run context.
# Until that exists, the developer names it — the handler cannot tell the
# difference, which is what keeps this tool from being throwaway.
LIBRARY_ID_ENV = "FRED_KB_LIBRARY_ID"
DEFAULT_INTERVAL_SECONDS = 60


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fred-samples-local-folder-kb-dev",
        description="Try the sample Knowledge Base without a Fred deployment.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("declaration", help="print what `publish` would send to Fred")

    for name, help_text in (
        ("sync", "synchronize a folder once, locally"),
        ("watch", "synchronize a folder over and over, until stopped"),
    ):
        run = commands.add_parser(name, help=help_text)
        run.add_argument("--root-path", required=True, help="folder to synchronize")
        run.add_argument("--glob", help="which files to pick up (default: **/*.md)")
        run.add_argument("--max-files", type=int, help="stop after this many files")
        run.add_argument(
            "--instance-id",
            default=DEFAULT_INSTANCE_ID,
            help="which instance's ledger to use (default: local)",
        )
        run.add_argument(
            "--library-id",
            default=os.getenv(LIBRARY_ID_ENV, ""),
            help=f"library to write into (default: ${LIBRARY_ID_ENV})",
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
        print(json.dumps(KnowledgeBaseDeclaration.of(kb).to_payload(), indent=2))
        return 0
    if arguments.command == "watch":
        return _watch(arguments)
    return _run_sync(arguments)


def _context(arguments: argparse.Namespace) -> KnowledgeBaseRunContext:
    """Fred builds this per run; here it is built by hand, and that is the only
    difference the handler could ever notice."""
    configuration: dict[str, TuningValue] = {"root_path": arguments.root_path}
    if arguments.glob:
        configuration["glob"] = arguments.glob
    if arguments.max_files is not None:
        configuration["max_files"] = arguments.max_files
    return KnowledgeBaseRunContext(
        definition_id=kb.id,
        instance_id=arguments.instance_id,
        team_id=LOCAL_TEAM_ID,
        run_id=uuid.uuid4().hex,
        library_id=arguments.library_id or "local-library",
        configuration=configuration,
    )


def _run_sync(arguments: argparse.Namespace) -> int:
    result = asyncio.run(kb.resolve_handler()(_context(arguments)))
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    return 0 if result.outcome is KnowledgeBaseRunOutcome.succeeded else 1


def _watch(arguments: argparse.Namespace) -> int:
    """What Fred's schedule will do, until Fred's schedule exists.

    The handler is called exactly as a dispatched run calls it, so replacing
    this loop with a real cadence changes nothing on the other side of it.
    """
    logger = logging.getLogger("watch")
    logger.info(
        "watching %s every %ss — Ctrl-C to stop",
        arguments.root_path,
        arguments.interval,
    )
    while True:
        result = asyncio.run(kb.resolve_handler()(_context(arguments)))
        logger.info("%s — %s", result.outcome.value, result.summary)
        for issue in result.errors + result.warnings:
            logger.warning("  %s %s", issue.code, issue.subject or "")
        try:
            time.sleep(arguments.interval)
        except KeyboardInterrupt:
            logger.info("stopped")
            return 0
