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
"""The one thing kept between runs, and what happens when it cannot be read."""

from __future__ import annotations

import pytest

from fred_samples_git_kb.cursor import MAX_LENGTH, Cursor


def test_a_cursor_survives_being_stored_and_read_back():
    original = Cursor(revision="a" * 40, selection="0123456789ab")

    assert Cursor.parse(original.render()) == original


def test_a_cursor_fits_well_inside_what_the_platform_stores():
    rendered = Cursor(revision="a" * 40, selection="0123456789ab").render()

    assert len(rendered) < MAX_LENGTH // 2


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "",
        "not json at all",
        '{"v":999,"rev":"aa","sel":"bb"}',
        '{"v":1,"rev":"aa"}',
        '{"v":1,"rev":"","sel":"bb"}',
        '{"v":1,"rev":42,"sel":"bb"}',
        "[]",
    ],
)
def test_anything_unreadable_reads_as_no_cursor_at_all(stored: str | None):
    """The cost is one full pass; refusing to run would be no synchronization."""
    assert Cursor.parse(stored) is None
