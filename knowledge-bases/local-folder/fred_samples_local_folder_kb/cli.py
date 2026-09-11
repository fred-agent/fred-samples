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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fred-samples-local-folder-kb-dev",
        description="Try the sample Knowledge Base without a Fred deployment.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("declaration", help="print what `publish` would send to Fred")

    run = commands.add_parser("sync", help="synchronize a folder once, locally")
    run.add_argument("--root-path", required=True, help="folder to synchronize")
    run.add_argument("--glob", help="which files to pick up (default: **/*.md)")
    run.add_argument("--max-files", type=int, help="stop after this many files")
    run.add_argument(
        "--instance-id",
        default=DEFAULT_INSTANCE_ID,
        help="which instance's ledger to use (default: local)",
    )

    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    if arguments.command == "declaration":
        print(json.dumps(KnowledgeBaseDeclaration.of(kb).to_payload(), indent=2))
        return 0
    return _run_sync(arguments)


def _run_sync(arguments: argparse.Namespace) -> int:
    configuration: dict[str, TuningValue] = {"root_path": arguments.root_path}
    if arguments.glob:
        configuration["glob"] = arguments.glob
    if arguments.max_files is not None:
        configuration["max_files"] = arguments.max_files

    # Fred fetches this context per run and hands it to the same handler; here
    # it is built by hand, which is the only difference.
    context = KnowledgeBaseRunContext(
        definition_id=kb.id,
        instance_id=arguments.instance_id,
        team_id=LOCAL_TEAM_ID,
        run_id=uuid.uuid4().hex,
        configuration=configuration,
    )
    result = asyncio.run(kb.resolve_handler()(context))
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    return 0 if result.outcome is KnowledgeBaseRunOutcome.succeeded else 1
