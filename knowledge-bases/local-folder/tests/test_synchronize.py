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
Offline tests for one synchronization run: the counters an operator reads, and
the refusals that keep a run inside its configured folder.

Every run here goes through `kb.resolve_handler()` — the same call the runtime
adapter will make — against a temporary folder and an isolated ledger.
"""

from __future__ import annotations

import json
from pathlib import Path

from fred_sdk.knowledge_base import KnowledgeBaseIssue, KnowledgeBaseRunOutcome

from tests.conftest import SyncRunner


def _codes(issues: list[KnowledgeBaseIssue]) -> set[str]:
    return {issue.code for issue in issues}


def test_first_run_creates_every_discovered_document(sync: SyncRunner, notes: Path):
    result = sync(notes)

    assert result.outcome is KnowledgeBaseRunOutcome.succeeded
    assert result.reconciliation_complete is True
    assert result.discovered == 3
    assert result.created == 3
    assert result.unchanged == 0
    assert result.updated == result.removed == 0
    assert result.errors == []


def test_second_identical_run_changes_nothing(sync: SyncRunner, notes: Path):
    sync(notes)

    result = sync(notes)

    assert result.discovered == 3
    assert result.unchanged == 3
    assert result.created == result.updated == result.removed == 0


def test_editing_one_file_reports_exactly_one_update(sync: SyncRunner, notes: Path):
    sync(notes)
    (notes / "two.md").write_text("two, revised", encoding="utf-8")

    result = sync(notes)

    assert result.updated == 1
    assert result.unchanged == 2
    assert result.created == result.removed == 0


def test_deleting_one_file_reports_exactly_one_removal(sync: SyncRunner, notes: Path):
    sync(notes)
    (notes / "nested" / "three.md").unlink()

    result = sync(notes)

    assert result.discovered == 2
    assert result.removed == 1
    assert result.unchanged == 2
    assert result.created == result.updated == 0


def test_max_files_bounds_discovery(sync: SyncRunner, notes: Path):
    result = sync(notes, max_files=2)

    assert result.discovered == 2
    assert result.created == 2
    # A bounded run has not seen the rest of the folder, so it must not claim
    # the files it never reached were removed — and must say so in the result.
    assert result.reconciliation_complete is False
    assert result.outcome is KnowledgeBaseRunOutcome.succeeded
    assert result.removed == 0
    assert "max_files_reached" in _codes(result.warnings)


def test_a_file_resolving_outside_the_root_is_refused(
    sync: SyncRunner, notes: Path, tmp_path: Path
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.md").write_text("not yours", encoding="utf-8")
    (notes / "escape.md").symlink_to(outside / "private.md")

    result = sync(notes)

    assert result.discovered == 3
    assert result.created == 3
    refused = [issue for issue in result.warnings if issue.code == "path_escapes_root"]
    assert [issue.subject for issue in refused] == ["escape.md"]


def test_a_glob_leaving_the_root_is_refused(sync: SyncRunner, notes: Path):
    result = sync(notes, glob="../**/*.md")

    assert result.outcome is KnowledgeBaseRunOutcome.failed
    assert _codes(result.errors) == {"glob_invalid"}


def test_a_missing_root_path_fails_with_a_clear_error(sync: SyncRunner, tmp_path: Path):
    result = sync(tmp_path / "does-not-exist")

    assert result.outcome is KnowledgeBaseRunOutcome.failed
    assert result.reconciliation_complete is False
    assert _codes(result.errors) == {"root_path_missing"}
    assert "does-not-exist" in result.summary


def test_the_result_is_json_safe(sync: SyncRunner, notes: Path):
    result = sync(notes, max_files=2)

    payload = result.model_dump(mode="json")

    assert json.loads(json.dumps(payload))["outcome"] == "succeeded"


def test_a_bounded_run_never_retracts_what_it_did_not_reach(
    sync: SyncRunner, notes: Path
):
    sync(notes)

    bounded = sync(notes, max_files=2)

    assert bounded.discovered == 2
    assert bounded.removed == 0
    assert bounded.unchanged == 2
    # The file the bound never reached is still in the ledger, so the next
    # unbounded run recognises it instead of publishing it a second time.
    assert sync(notes).unchanged == 3


def test_max_files_refuses_a_string_shaped_number(sync: SyncRunner, notes: Path):
    # Fred validates configured values against the declared fields and refuses
    # "2" for an integer field, so the handler never coerces one.
    result = sync(notes, max_files="2")

    assert result.outcome is KnowledgeBaseRunOutcome.failed
    assert _codes(result.errors) == {"max_files_invalid"}


def test_an_empty_glob_falls_back_to_the_declared_default(
    sync: SyncRunner, notes: Path
):
    assert sync(notes, glob="").discovered == 3


def test_a_malformed_glob_fails_instead_of_raising(sync: SyncRunner, notes: Path):
    result = sync(notes, glob="**notes/*.md")

    assert result.outcome is KnowledgeBaseRunOutcome.failed
    assert _codes(result.errors) == {"glob_invalid"}


def test_hidden_paths_inside_the_root_are_skipped(sync: SyncRunner, notes: Path):
    (notes / ".git").mkdir()
    (notes / ".git" / "COMMIT_EDITMSG.md").write_text("wip", encoding="utf-8")
    (notes / ".draft.md").write_text("unfinished", encoding="utf-8")

    assert sync(notes).discovered == 3


def test_a_symlinked_alias_inside_the_root_is_its_own_document(
    sync: SyncRunner, notes: Path
):
    (notes / "alias.md").symlink_to(notes / "nested" / "three.md")

    result = sync(notes)

    # Two paths, two documents: the alias must not collide with its target.
    assert result.discovered == 4
    assert result.created == 4
    assert sync(notes).unchanged == 4
