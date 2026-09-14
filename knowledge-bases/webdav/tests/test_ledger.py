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
"""The cache a run leaves behind, and what it refuses to read back."""

from __future__ import annotations

import pytest

from fred_samples_webdav_kb.ledger import (
    STATE_DIR_ENV,
    LedgerError,
    ledger_path_for,
    load_ledger,
    save_ledger,
    state_dir,
)


def test_a_missing_ledger_simply_means_a_first_run(tmp_path):
    assert load_ledger(tmp_path / "absent.json") == {}


def test_what_was_saved_is_what_is_read_back(tmp_path):
    path = tmp_path / "ledger.json"
    save_ledger(path, {"a.md": 'etag:"v1"', "docs/b.md": "mtime:x:3"})

    assert load_ledger(path) == {"a.md": 'etag:"v1"', "docs/b.md": "mtime:x:3"}


def test_saving_replaces_the_file_rather_than_editing_it(tmp_path):
    """A run must never read the half-written ledger of an overlapping one."""
    path = tmp_path / "ledger.json"
    save_ledger(path, {"a.md": "v1"})
    save_ledger(path, {"b.md": "v2"})

    assert load_ledger(path) == {"b.md": "v2"}
    assert [item.name for item in tmp_path.iterdir()] == ["ledger.json"]


@pytest.mark.parametrize("content", ["not json", "[]", '{"a.md": 3}'])
def test_a_ledger_that_is_not_one_is_refused_rather_than_half_read(
    tmp_path, content: str
):
    path = tmp_path / "ledger.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(LedgerError):
        load_ledger(path)


def test_each_instance_gets_its_own_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))

    assert ledger_path_for("one") != ledger_path_for("two")
    assert ledger_path_for("one").parent == tmp_path


def test_an_instance_id_that_is_not_a_file_name_still_gets_its_own_ledger(
    monkeypatch, tmp_path
):
    """Folding is lossy, so two ids that fold alike must not share one file."""
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))

    first = ledger_path_for("team/one")
    second = ledger_path_for("team:one")

    assert first != second
    assert "/" not in first.name and ":" not in second.name


def test_the_state_directory_is_redirected_by_its_variable(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "elsewhere"))
    assert state_dir() == tmp_path / "elsewhere"


def test_without_the_variable_it_falls_back_to_the_xdg_state_home(
    monkeypatch, tmp_path
):
    monkeypatch.delenv(STATE_DIR_ENV, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert state_dir() == tmp_path / "fred-samples-webdav-kb"
