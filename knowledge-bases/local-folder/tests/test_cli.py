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
"""Offline tests for the developer tool that runs the sample without Fred."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fred_sdk.knowledge_base import KnowledgeBaseDeclaration

from fred_samples_local_folder_kb.cli import main
from fred_samples_local_folder_kb.knowledge_base import kb
from fred_samples_local_folder_kb.ledger import STATE_DIR_ENV


def test_declaration_command_prints_what_publish_would_send(
    capsys: pytest.CaptureFixture[str],
):
    assert main(["declaration"]) == 0

    payload = json.loads(capsys.readouterr().out)

    assert KnowledgeBaseDeclaration.model_validate(
        payload
    ) == KnowledgeBaseDeclaration.of(kb)
    assert all("ui" not in field for field in payload["configuration_fields"])


def test_sync_command_reports_success_and_failure_through_its_exit_code(
    tmp_path: Path, notes: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "state"))

    assert main(["sync", "--root-path", str(notes)]) == 0
    assert main(["sync", "--root-path", str(tmp_path / "absent")]) == 1
