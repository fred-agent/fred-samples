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
"""Which files belong in the library, and how a change of mind is noticed."""

from __future__ import annotations

import pytest

from fred_samples_git_kb.matching import PatternError, Selection, patterns


def test_the_default_takes_markdown_at_any_depth():
    selection = Selection()

    assert selection.selects("README.md")
    assert selection.selects("docs/deep/guide.md")
    assert not selection.selects("docs/notes.txt")


def test_a_subdirectory_keeps_everything_outside_it_out():
    selection = Selection(subdirectory="docs")

    assert selection.selects("docs/guide.md")
    assert not selection.selects("README.md")
    assert not selection.selects("docsets/guide.md")


def test_exclusion_wins_over_inclusion():
    selection = Selection(include=["**/*.md"], exclude=["**/vendor/**"])

    assert selection.selects("docs/guide.md")
    assert not selection.selects("docs/vendor/copied.md")


def test_several_patterns_are_read_from_one_field():
    assert patterns("**/*.md, **/*.rst\n**/*.txt") == (
        "**/*.md",
        "**/*.rst",
        "**/*.txt",
    )
    assert patterns("  ") == ()


def test_a_negated_pattern_is_refused_rather_than_silently_reordered():
    with pytest.raises(PatternError):
        Selection(include=["**/*.md", "!docs/private/**"])


def test_a_subdirectory_leaving_the_repository_is_refused():
    with pytest.raises(PatternError):
        Selection(subdirectory="../elsewhere")


def test_the_digest_ignores_the_order_patterns_were_typed_in():
    one = Selection(include=["**/*.md", "**/*.rst"])
    other = Selection(include=["**/*.rst", "**/*.md"])

    assert one.digest == other.digest


def test_the_digest_changes_when_the_meaning_does():
    narrow = Selection(include=["**/*.md"])
    wide = Selection(include=["**/*.md", "**/*.txt"])
    nested = Selection(include=["**/*.md"], subdirectory="docs")

    assert len({narrow.digest, wide.digest, nested.digest}) == 3
