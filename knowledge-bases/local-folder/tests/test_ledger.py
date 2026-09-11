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
"""Offline tests for the state this implementation owns and Fred never sees."""

from __future__ import annotations

from pathlib import Path

import pytest

from fred_samples_local_folder_kb.ledger import STATE_DIR_ENV, ledger_path_for
from tests.conftest import SyncRunner


def test_distinct_instances_never_share_a_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))

    # Both fold to the same safe filename; only the digest keeps them apart.
    assert ledger_path_for("team/alpha") != ledger_path_for("team_alpha")


def test_an_unreadable_ledger_is_reported_and_the_run_continues(
    sync: SyncRunner, notes: Path
):
    sync(notes)
    ledger_path_for("instance-under-test").write_text("{not json", encoding="utf-8")

    result = sync(notes)

    assert {issue.code for issue in result.warnings} == {"ledger_unreadable"}
    assert result.created == 3
