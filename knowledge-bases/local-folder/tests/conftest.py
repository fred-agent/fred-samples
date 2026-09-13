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
"""Shared fixtures: run the handler the way the runtime adapter will."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from fred_sdk.contracts.models import TuningValue
from fred_sdk.knowledge_base import KnowledgeBaseRunContext, KnowledgeBaseSyncResult

from fred_samples_local_folder_kb.knowledge_base import kb
from fred_samples_local_folder_kb.ledger import STATE_DIR_ENV

SyncRunner = Callable[..., KnowledgeBaseSyncResult]


@pytest.fixture
def sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SyncRunner:
    """Run one synchronization against a ledger isolated in the test's tmp dir."""
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "state"))

    def run(root: Path | str, **configuration: TuningValue) -> KnowledgeBaseSyncResult:
        context = KnowledgeBaseRunContext(
            definition_id=kb.id,
            instance_id="instance-under-test",
            team_id="team-under-test",
            run_id="run-under-test",
            library_id="library-under-test",
            configuration={"root_path": str(root), **configuration},
        )
        return asyncio.run(kb.resolve_handler()(context))

    return run


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    """A folder with three Markdown documents and one file the glob ignores."""
    folder = tmp_path / "notes"
    (folder / "nested").mkdir(parents=True)
    (folder / "one.md").write_text("one", encoding="utf-8")
    (folder / "two.md").write_text("two", encoding="utf-8")
    (folder / "nested" / "three.md").write_text("three", encoding="utf-8")
    (folder / "ignored.txt").write_text("not markdown", encoding="utf-8")
    return folder
