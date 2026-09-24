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
The rules one run applies, tested without a repository and without Fred.

The rename cases are the ones worth reading: a file moving into, out of or
across the selection is three different outcomes, and getting any of them
wrong leaves a library that quietly disagrees with its source.
"""

from __future__ import annotations

import pytest

from fred_samples_git_kb.cursor import Cursor
from fred_samples_git_kb.matching import Selection
from fred_samples_git_kb.plan import (
    MAX_SOURCE_KEY_LENGTH,
    LibraryTooLarge,
    PassKind,
    choose_pass,
    is_lfs_pointer,
    plan_full,
    plan_incremental,
)
from fred_samples_git_kb.source import ChangeKind, EntryKind, SourceChange, SourceFile

BIG = 20 * 1024 * 1024


def file(path: str, *, blob: str = "abc123", size: int = 10, kind=EntryKind.file):
    return SourceFile(path=path, blob_id=blob, size_bytes=size, kind=kind)


def markdown() -> Selection:
    return Selection()


# ── choosing the shape of a pass ──────────────────────────────────────────────


def test_a_first_run_reads_everything():
    choice = choose_pass(
        cursor=None, head_revision="aa", selection_digest="d", base_available=False
    )

    assert choice.kind is PassKind.full
    assert choice.reason == "first_run"


def test_an_unmoved_branch_is_nothing_to_do():
    choice = choose_pass(
        cursor=Cursor("aa", "d"),
        head_revision="aa",
        selection_digest="d",
        base_available=True,
    )

    assert choice.kind is PassKind.up_to_date


def test_a_changed_selection_forces_a_full_pass_even_on_an_unmoved_branch():
    """A widened pattern reaches files no diff between revisions mentions."""
    choice = choose_pass(
        cursor=Cursor("aa", "old"),
        head_revision="aa",
        selection_digest="new",
        base_available=True,
    )

    assert choice.kind is PassKind.full
    assert choice.reason == "selection_changed"


def test_a_base_the_mirror_lost_forces_a_full_pass():
    choice = choose_pass(
        cursor=Cursor("aa", "d"),
        head_revision="bb",
        selection_digest="d",
        base_available=False,
    )

    assert choice.kind is PassKind.full
    assert choice.reason == "base_unavailable"


def test_a_moved_branch_is_read_as_a_difference():
    choice = choose_pass(
        cursor=Cursor("aa", "d"),
        head_revision="bb",
        selection_digest="d",
        base_available=True,
    )

    assert choice.kind is PassKind.incremental
    assert choice.base == "aa"


# ── a full pass ───────────────────────────────────────────────────────────────


def test_a_full_pass_writes_what_the_selection_chooses_and_says_it_saw_everything():
    plan = plan_full(
        [file("README.md"), file("notes.txt"), file("docs/guide.md")],
        held={},
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert {write.source_key for write in plan.writes} == {"README.md", "docs/guide.md"}
    assert plan.exhaustive is True


def test_a_full_pass_leaves_alone_what_the_library_already_holds_at_that_version():
    """Losing the cursor costs a comparison, not re-ingesting the repository."""
    plan = plan_full(
        [file("a.md", blob="same"), file("b.md", blob="new")],
        held={"a.md": "same", "b.md": "old"},
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert [write.source_key for write in plan.writes] == ["b.md"]
    assert plan.unchanged == 1
    assert plan.removals == ()


def test_a_full_pass_removes_what_the_library_holds_and_the_revision_does_not():
    """Every file was seen, so an absence is proven rather than inferred."""
    plan = plan_full(
        [file("a.md"), file("big.md", size=BIG + 1), file("notes.txt")],
        held={"a.md": "abc123", "big.md": "v1", "gone.md": "v1", "notes.txt": "v1"},
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    # big.md is skipped yet still present; notes.txt is no longer selected.
    assert [removal.source_key for removal in plan.removals] == [
        "gone.md",
        "notes.txt",
    ]


def test_the_key_is_the_path_relative_to_the_configured_folder():
    plan = plan_full(
        [file("docs/swift/README.md")],
        held={"old.md": "v1"},
        selection=Selection(subdirectory="docs"),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert plan.writes[0].source_key == "swift/README.md"
    assert [removal.source_key for removal in plan.removals] == ["old.md"]


def test_an_incremental_removal_uses_the_same_key_as_the_write():
    plan = plan_incremental(
        [SourceChange(ChangeKind.deleted, previous_path="docs/gone.md")],
        selection=Selection(subdirectory="docs"),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert [removal.source_key for removal in plan.removals] == ["gone.md"]


def test_the_document_version_is_the_content_identity():
    plan = plan_full(
        [file("a.md", blob="cafe")],
        held={},
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert plan.writes[0].document_version == "cafe"


def test_too_many_files_is_refused_rather_than_half_synchronized():
    """A partial pass has no revision it could honestly record."""
    with pytest.raises(LibraryTooLarge) as refused:
        plan_full(
            [file(f"{index}.md") for index in range(5)],
            held={},
            selection=markdown(),
            max_files=3,
            max_file_bytes=BIG,
        )

    assert refused.value.selected == 5
    assert refused.value.bound == 3


# ── what cannot be carried ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        (file("a.md", kind=EntryKind.symlink), "symlink"),
        (file("a.md", kind=EntryKind.submodule), "submodule"),
        (file("a.md", kind=EntryKind.unnameable), "path_not_text"),
        (file("a.md", size=BIG + 1), "too_large"),
        (file("x" * (MAX_SOURCE_KEY_LENGTH + 1) + ".md"), "path_too_long"),
    ],
)
def test_what_cannot_be_ingested_is_skipped_and_named(entry: SourceFile, reason: str):
    plan = plan_full(
        [entry], held={}, selection=markdown(), max_files=None, max_file_bytes=BIG
    )

    assert plan.writes == ()
    assert [skip.reason for skip in plan.skips] == [reason]


def test_a_skip_never_removes_the_document_already_there():
    """Our own limit is not a statement about the repository."""
    plan = plan_incremental(
        [SourceChange(ChangeKind.modified, file=file("a.md", size=BIG + 1))],
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert plan.removals == ()
    assert len(plan.skips) == 1


# ── a difference between two revisions ────────────────────────────────────────


def test_an_addition_and_a_modification_are_both_written():
    plan = plan_incremental(
        [
            SourceChange(ChangeKind.added, file=file("new.md")),
            SourceChange(ChangeKind.modified, file=file("old.md")),
        ],
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert {write.source_key for write in plan.writes} == {"new.md", "old.md"}


def test_a_deletion_removes_the_key_it_was_written_under():
    plan = plan_incremental(
        [SourceChange(ChangeKind.deleted, previous_path="gone.md")],
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert [removal.source_key for removal in plan.removals] == ["gone.md"]


def test_a_deletion_outside_the_selection_removes_nothing():
    plan = plan_incremental(
        [SourceChange(ChangeKind.deleted, previous_path="gone.txt")],
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert plan.removals == ()


def test_a_rename_inside_the_selection_writes_the_new_name_and_drops_the_old():
    plan = plan_incremental(
        [SourceChange(ChangeKind.renamed, file=file("b.md"), previous_path="a.md")],
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert [write.source_key for write in plan.writes] == ["b.md"]
    assert [removal.source_key for removal in plan.removals] == ["a.md"]


def test_a_rename_out_of_the_selection_is_a_removal():
    plan = plan_incremental(
        [SourceChange(ChangeKind.renamed, file=file("a.txt"), previous_path="a.md")],
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert plan.writes == ()
    assert [removal.source_key for removal in plan.removals] == ["a.md"]


def test_a_rename_into_the_selection_is_an_addition():
    plan = plan_incremental(
        [SourceChange(ChangeKind.renamed, file=file("a.md"), previous_path="a.txt")],
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert [write.source_key for write in plan.writes] == ["a.md"]
    assert plan.removals == ()


def test_a_rename_this_run_cannot_carry_leaves_the_old_document_alone():
    """Removing it would lose the document: the new name is not going in."""
    plan = plan_incremental(
        [
            SourceChange(
                ChangeKind.renamed,
                file=file("b.md", size=BIG + 1),
                previous_path="a.md",
            )
        ],
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert plan.writes == ()
    assert plan.removals == ()
    assert [skip.reason for skip in plan.skips] == ["too_large"]


def test_a_rename_names_the_write_its_removal_waits_for():
    plan = plan_incremental(
        [SourceChange(ChangeKind.renamed, file=file("b.md"), previous_path="a.md")],
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert plan.removals[0].replaced_by == "b.md"


def test_a_plain_deletion_waits_for_nothing():
    plan = plan_incremental(
        [SourceChange(ChangeKind.deleted, previous_path="gone.md")],
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert plan.removals[0].replaced_by is None


def test_a_rename_outside_the_selection_entirely_does_nothing():
    plan = plan_incremental(
        [SourceChange(ChangeKind.renamed, file=file("b.txt"), previous_path="a.txt")],
        selection=markdown(),
        max_files=None,
        max_file_bytes=BIG,
    )

    assert plan.writes == ()
    assert plan.removals == ()


def test_a_difference_never_claims_to_have_seen_the_whole_repository():
    plan = plan_incremental(
        [], selection=markdown(), max_files=None, max_file_bytes=BIG
    )

    assert plan.exhaustive is False


def test_one_commit_adding_a_whole_tree_is_refused_like_a_full_pass():
    """Otherwise the library grows past a bound the next full pass enforces."""
    with pytest.raises(LibraryTooLarge):
        plan_incremental(
            [
                SourceChange(ChangeKind.added, file=file(f"{index}.md"))
                for index in range(5)
            ],
            selection=markdown(),
            max_files=3,
            max_file_bytes=BIG,
        )


def test_removals_do_not_count_against_the_bound():
    plan = plan_incremental(
        [
            SourceChange(ChangeKind.deleted, previous_path=f"{index}.md")
            for index in range(5)
        ],
        selection=markdown(),
        max_files=3,
        max_file_bytes=BIG,
    )

    assert len(plan.removals) == 5


# ── content stored elsewhere ──────────────────────────────────────────────────


def test_a_large_file_pointer_is_recognised_for_what_it_is():
    pointer = (
        b"version https://git-lfs.github.com/spec/v1\noid sha256:4d7a\nsize 12345\n"
    )

    assert is_lfs_pointer(pointer) is True
    assert is_lfs_pointer(b"# an ordinary document\n") is False
