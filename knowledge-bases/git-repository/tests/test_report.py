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
"""What a run says it did, and what earns it the right to move the cursor."""

from __future__ import annotations

from fred_samples_git_kb.plan import PassKind
from fred_samples_git_kb.report import MAX_ISSUES, RunReport


def report() -> RunReport:
    return RunReport(pass_kind=PassKind.full, revision="a" * 40)


def test_writes_are_counted_by_whether_the_library_already_held_the_key():
    counted = report()
    counted.wrote(created=True)
    counted.wrote(created=False)
    counted.wrote(created=False)

    assert (counted.written_new, counted.written_existing) == (1, 2)


def test_unchanged_documents_are_said_so_only_when_there_are_some():
    counted = report()
    assert "unchanged" not in counted.summary()

    counted.unchanged = 4
    assert "4 unchanged" in counted.summary()


def test_a_skip_is_a_warning_and_never_stops_the_cursor():
    counted = report()
    counted.skip("a.md", "too_large")

    assert counted.skipped == 1
    assert counted.succeeded
    assert counted.may_advance_cursor


def test_one_failure_is_enough_to_hold_the_cursor_still():
    counted = report()
    counted.wrote(created=True)
    counted.fail("write_failed", "refused", subject="b.md")

    assert not counted.succeeded
    assert not counted.may_advance_cursor


def test_a_flood_of_issues_is_bounded_and_says_that_it_was():
    counted = report()
    for index in range(MAX_ISSUES + 10):
        counted.warn("skipped", subject=f"{index}.md")

    assert len(counted.warnings) == MAX_ISSUES + 1
    assert counted.warnings[-1].code == "issues_truncated"


def test_the_metrics_say_which_shape_of_pass_this_was():
    counted = RunReport(
        pass_kind=PassKind.incremental, revision="b" * 40, base="a" * 40
    )

    assert counted.metrics()["pass"] == "incremental"
    assert counted.metrics()["base"] == "a" * 40
    assert counted.metrics()["cursor_advanced"] is True
