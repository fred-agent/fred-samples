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

from fred_samples_webdav_kb.knowledge_base import _result, kb, synchronize
from fred_samples_webdav_kb.report import RunReport
from fred_samples_webdav_kb.settings import read_settings


def test_the_declaration_publishes_as_json():
    payload = KnowledgeBaseDeclaration.of(kb).to_payload()

    assert payload["id"] == "fred.samples.webdav"
    assert json.loads(json.dumps(payload)) == payload


def test_no_declared_field_takes_a_key_fred_declares():
    """The form has two zones sharing one key space; only one of them is ours."""
    assert not any(is_platform_field(field.key) for field in kb.configuration_fields)


def test_every_declared_field_is_one_the_run_actually_reads():
    """A field in the form that nothing reads is a promise to a team we break."""
    filled: dict[str, object] = {field.key: "" for field in kb.configuration_fields}
    filled["url"] = "https://share.example.com/documents/"
    filled["username"] = "reader"
    filled["password"] = "secret"  # pragma: allowlist secret
    filled["include"] = "**/*.pdf"
    filled["exclude"] = "drafts/**"
    filled["max_files"] = 10
    filled["profile"] = "rich"
    filled["trust_any_certificate"] = True

    settings = read_settings(filled)

    assert settings.where.url == "https://share.example.com/documents/"
    assert settings.username == "reader"
    assert settings.max_files == 10
    assert settings.profile == "rich"
    assert settings.trust_any_certificate is True
    assert settings.selection.selects("a.pdf")
    assert not settings.selection.selects("drafts/a.pdf")


def test_the_address_is_the_only_thing_a_team_must_fill_in():
    required = {field.key for field in kb.configuration_fields if field.required}

    assert required == {"url"}


def test_the_certificate_field_is_off_by_default_in_the_form():
    """A dangerous default is one nobody ever notices they accepted."""
    field = next(
        item for item in kb.configuration_fields if item.key == "trust_any_certificate"
    )
    assert field.default is False


def test_a_run_reports_fred_s_counters_from_what_this_implementation_counted():
    report = RunReport(exhaustive=True, discovered=4, unchanged=1)
    report.wrote(created=True, size_bytes=10)
    report.wrote(created=False, size_bytes=20)
    report.removed = 1
    report.skip("big.md", "too_large")

    result = _result(report)

    assert result.outcome is KnowledgeBaseRunOutcome.succeeded
    assert result.reconciliation_complete is True
    assert (
        result.discovered,
        result.created,
        result.updated,
        result.removed,
        result.unchanged,
    ) == (4, 1, 1, 1, 1)
    assert [issue.code for issue in result.warnings] == ["too_large"]


def test_a_bounded_run_is_reported_as_incomplete_even_though_it_succeeded():
    """The two are orthogonal, and only one of them licenses a deletion."""
    result = _result(RunReport(exhaustive=False))

    assert result.outcome is KnowledgeBaseRunOutcome.succeeded
    assert result.reconciliation_complete is False


def test_a_failed_run_is_reported_as_one():
    report = RunReport(exhaustive=True)
    report.fail("write_failed", "refused", subject="a.md")

    result = _result(report)

    assert result.outcome is KnowledgeBaseRunOutcome.failed
    assert [issue.subject for issue in result.errors] == ["a.md"]


def test_a_configuration_that_cannot_be_read_never_reaches_a_share():
    """A handler owes Fred a result, not an exception — and no request at all."""
    result = asyncio.run(
        synchronize(
            KnowledgeBaseRunContext(
                definition_id="fred.samples.webdav",
                instance_id="instance",
                team_id="team",
                run_id="run",
                library_id="library",
                configuration={"url": "not-an-address"},
            )
        )
    )

    assert result.outcome is KnowledgeBaseRunOutcome.failed
    assert result.reconciliation_complete is False
    assert [issue.code for issue in result.errors] == ["url_invalid"]


def test_the_profile_field_declares_the_supported_choices_and_default():
    field = next(item for item in kb.configuration_fields if item.key == "profile")
    assert field.type == "select"
    assert field.enum == ["fast", "medium", "rich"]
    assert field.default == "medium"
