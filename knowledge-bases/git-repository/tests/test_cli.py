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
"""The developer tool's output, which a person and a script both read."""

from __future__ import annotations

import json

from fred_samples_git_kb.cli import as_json, main
from fred_samples_git_kb.plan import PassKind
from fred_samples_git_kb.report import RunReport


def test_a_run_serializes_whole_including_its_issues():
    report = RunReport(pass_kind=PassKind.full, revision="a" * 40, exhaustive=True)
    report.wrote(created=True)
    report.skip("big.md", "too_large")
    report.fail("write_failed", "refused", subject="b.md")

    rendered = json.loads(json.dumps(as_json(report)))

    assert rendered["created"] == 1
    assert rendered["succeeded"] is False
    assert rendered["warnings"][0]["subject"] == "big.md"
    assert rendered["errors"][0]["code"] == "write_failed"


def test_a_configuration_that_cannot_be_read_stops_before_any_network(capsys):
    exit_code = main(["sync", "--repository", "not-a-repository"])

    assert exit_code == 2
    assert "repository_invalid" in capsys.readouterr().out


def test_the_developer_tool_prints_the_declaration_the_image_publishes(capsys):
    assert main(["declaration"]) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["id"] == "fred.samples.git-repository"
