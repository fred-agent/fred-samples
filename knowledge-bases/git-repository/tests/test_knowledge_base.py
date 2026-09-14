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
What the image declares to Fred, and what it reports back.

The declaration is the only thing a team ever sees of this Knowledge Base, and
it is published by a deployment hook nobody watches — so what it contains is
worth pinning down here rather than discovering in a form.
"""

from __future__ import annotations

import asyncio
import json

from fred_sdk.knowledge_base import (
    KnowledgeBaseDeclaration,
    KnowledgeBaseRunContext,
    KnowledgeBaseRunOutcome,
)
from fred_sdk.knowledge_base.schedule import is_platform_field

from fred_samples_git_kb.knowledge_base import _result, kb, synchronize
from fred_samples_git_kb.plan import PassKind
from fred_samples_git_kb.report import RunReport
from fred_samples_git_kb.settings import read_settings


def test_the_declaration_publishes_as_json():
    payload = KnowledgeBaseDeclaration.of(kb).to_payload()

    assert payload["id"] == "fred.samples.git-repository"
    assert json.loads(json.dumps(payload)) == payload


def test_no_declared_field_takes_a_key_fred_declares():
    """The form has two zones sharing one key space; only one of them is ours."""
    assert not any(is_platform_field(field.key) for field in kb.configuration_fields)


def test_every_declared_field_is_one_the_run_actually_reads():
    """A field in the form that nothing reads is a promise to a team we break."""
    filled: dict[str, object] = {
        field.key: "owner/name" if field.key == "repository" else ""
        for field in kb.configuration_fields
    }
    filled["max_files"] = 10

    settings = read_settings(filled)

    assert settings.forge.repository == "owner/name"
    assert settings.max_files == 10


def test_the_repository_is_the_only_thing_a_team_must_fill_in():
    required = {field.key for field in kb.configuration_fields if field.required}

    assert required == {"provider", "repository"}


def test_a_run_reports_fred_s_counters_from_what_this_implementation_counted():
    report = RunReport(pass_kind=PassKind.full, revision="a" * 40, exhaustive=True)
    report.considered = 3
    report.wrote(created=True)
    report.wrote(created=False)
    report.removed(existed=True)
    report.skip("big.md", "too_large")

    result = _result(report)

    assert result.outcome is KnowledgeBaseRunOutcome.succeeded
    assert result.reconciliation_complete is True
    assert (result.discovered, result.created, result.updated, result.removed) == (
        3,
        1,
        1,
        1,
    )
    assert [issue.code for issue in result.warnings] == ["too_large"]


def test_a_failed_run_is_reported_as_one():
    report = RunReport(pass_kind=PassKind.incremental, revision="b" * 40)
    report.fail("write_failed", "refused", subject="a.md")

    result = _result(report)

    assert result.outcome is KnowledgeBaseRunOutcome.failed
    assert result.reconciliation_complete is False


def test_a_configuration_that_cannot_be_read_never_reaches_a_repository(monkeypatch):
    """A handler owes Fred a result, not an exception — and touches nothing."""
    monkeypatch.delenv("FRED_CONTROL_PLANE_URL", raising=False)
    context = KnowledgeBaseRunContext(
        definition_id=kb.id,
        instance_id="instance-under-test",
        team_id="team-under-test",
        run_id="run-under-test",
        library_id="library-under-test",
        configuration={"repository": "not-a-repository"},
    )

    result = asyncio.run(synchronize(context))

    assert result.outcome is KnowledgeBaseRunOutcome.failed
    assert [issue.code for issue in result.errors] == ["repository_invalid"]
