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
What one run should do, decided before anything is written.

Two questions, both answered without touching a repository or Fred: which
shape of pass this run is, and which writes and removals that pass implies.
Keeping them pure is what lets every rule below be tested on its own — above
all what a rename means, which is where a synchronizer usually goes wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from fred_samples_git_kb.cursor import Cursor
from fred_samples_git_kb.matching import Selection
from fred_samples_git_kb.source import EntryKind, SourceChange, SourceFile

# Bounds the platform enforces on what addresses a document. Refusing here
# turns a write rejected mid-run into a named skip decided before the run
# starts.
MAX_SOURCE_KEY_LENGTH = 512
MAX_PATH_SEGMENT_LENGTH = 255


class PassKind(StrEnum):
    up_to_date = "up_to_date"
    full = "full"
    incremental = "incremental"


@dataclass(frozen=True, slots=True)
class PassChoice:
    kind: PassKind
    base: str | None
    reason: str


def choose_pass(
    *,
    cursor: Cursor | None,
    head_revision: str,
    selection_digest: str,
    base_available: bool,
) -> PassChoice:
    """Decide what this run is, from what the last one recorded.

    A caller may ask before fetching anything, to learn cheaply that there is
    nothing to do; only `up_to_date` is trustworthy then, because the remaining
    answers depend on what the local mirror actually holds.
    """
    if cursor is None:
        return PassChoice(PassKind.full, None, "first_run")
    if cursor.selection != selection_digest:
        # A widened pattern reaches files that changed long ago, which no diff
        # between two revisions would ever mention.
        return PassChoice(PassKind.full, None, "selection_changed")
    if cursor.revision == head_revision:
        return PassChoice(PassKind.up_to_date, cursor.revision, "no_change")
    if not base_available:
        # Nothing to compare against: this pod does not have the snapshot the
        # last run left behind. A rewritten branch does not land here — two
        # snapshots are comparable whether or not one descends from the other.
        return PassChoice(PassKind.full, None, "base_unavailable")
    return PassChoice(PassKind.incremental, cursor.revision, "since_last_run")


@dataclass(frozen=True, slots=True)
class Write:
    """One document to put in the library.

    The key is the repository's own path and never changes meaning; the path
    only says where it sits. The version is the content's identity, so Fred can
    be told what it is holding without anyone hashing anything twice.
    """

    source_key: str
    library_path: str
    document_version: str
    file: SourceFile


@dataclass(frozen=True, slots=True)
class Removal:
    """One document to take out of the library.

    `replaced_by` names the key taking its place when this removal is the old
    half of a rename. Removing it before that one is written would leave the
    library holding neither, so a run that could not write the replacement must
    not carry out the removal either.
    """

    source_key: str
    replaced_by: str | None = None


@dataclass(frozen=True, slots=True)
class Skip:
    source_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class Plan:
    """Everything this run intends, before any of it has happened.

    Writes go out before removals: a renamed document then never has a moment
    where the library holds neither the old name nor the new one.
    """

    writes: tuple[Write, ...] = ()
    removals: tuple[Removal, ...] = ()
    skips: tuple[Skip, ...] = ()
    exhaustive: bool = False


class LibraryTooLarge(Exception):
    """More files match than this Knowledge Base will put in one library.

    Refusing beats synchronizing part of the repository: a partial pass has no
    revision it could honestly record, so it would either claim a state the
    library does not have or repeat itself for ever.
    """

    def __init__(self, selected: int, bound: int) -> None:
        super().__init__(
            f"{selected} files match, which is past the bound of {bound}. "
            "Narrow the folder or the patterns, or raise the bound."
        )
        self.selected = selected
        self.bound = bound


def plan_full(
    files: list[SourceFile],
    *,
    selection: Selection,
    max_files: int | None,
    max_file_bytes: int,
) -> Plan:
    """Plan a pass over everything the revision holds.

    It issues no removals. Fred offers no way to ask what a library already
    contains, so a document the repository dropped while this pod was away
    stays until a later diff mentions it. The alternative — deducing removals
    from absence — is exactly what Fred refuses to do, and for the same reason.
    """
    selected = [entry for entry in files if selection.selects(entry.path)]
    if max_files is not None and len(selected) > max_files:
        raise LibraryTooLarge(len(selected), max_files)

    writes: list[Write] = []
    skips: list[Skip] = []
    for entry in selected:
        _record(entry, selection, max_file_bytes, writes, skips)
    return Plan(writes=tuple(writes), skips=tuple(skips), exhaustive=True)


def plan_incremental(
    changes: list[SourceChange],
    *,
    selection: Selection,
    max_files: int | None,
    max_file_bytes: int,
) -> Plan:
    """Plan a pass over what changed between two revisions.

    A rename is the case worth reading twice: what matters is not that the file
    moved but whether each of its two paths belongs in the library, and the
    four answers are four different outcomes.
    """
    arriving = [
        change.file
        for change in changes
        if change.file is not None and selection.selects(change.file.path)
    ]
    if max_files is not None and len(arriving) > max_files:
        # One commit that adds a whole tree is how a library actually blows
        # past its bound. Refusing here keeps the bound meaning the same thing
        # on both paths, rather than leaving a library a later full pass will
        # refuse to read.
        raise LibraryTooLarge(len(arriving), max_files)

    writes: list[Write] = []
    removals: list[Removal] = []
    skips: list[Skip] = []

    for change in changes:
        previous = change.previous_path
        arriving = change.file is not None and selection.selects(change.file.path)
        written = (
            _record(change.file, selection, max_file_bytes, writes, skips)
            if change.file is not None and arriving
            else None
        )
        # A key the library holds, which this revision no longer has under that
        # name: a deletion, or the old half of a rename.
        vacated = (
            previous is not None
            and selection.selects(previous)
            and (change.file is None or change.file.path != previous)
        )
        if not vacated or previous is None:
            continue
        if arriving and written is None:
            # The name it moved to cannot be carried, so the library keeps what
            # it already has under the old one. A skip never removes.
            continue
        removals.append(
            Removal(previous, replaced_by=written.source_key if written else None)
        )

    return Plan(
        writes=tuple(writes),
        removals=tuple(removals),
        skips=tuple(skips),
        exhaustive=False,
    )


def _record(
    entry: SourceFile,
    selection: Selection,
    max_file_bytes: int,
    writes: list[Write],
    skips: list[Skip],
) -> Write | None:
    """Write this file, or say why it cannot be one — and which it was.

    A skip never removes. The library loses a document because the repository
    dropped it, never because this implementation could not carry it — deleting
    a team's document over our own size bound would be the worse mistake by far.
    """
    reason = _unusable(entry, max_file_bytes)
    if reason is not None:
        skips.append(Skip(entry.path, reason))
        return None
    write = Write(
        source_key=entry.path,
        library_path=selection.library_path(entry.path),
        document_version=entry.blob_id,
        file=entry,
    )
    writes.append(write)
    return write


def _unusable(entry: SourceFile, max_file_bytes: int) -> str | None:
    if entry.kind is EntryKind.unnameable:
        return "path_not_text"
    if entry.kind is EntryKind.submodule:
        return "submodule"
    if entry.kind is EntryKind.symlink:
        # Its content is the path it points at, which is not a document, and
        # following it would read a file the selection never chose.
        return "symlink"
    if entry.size_bytes > max_file_bytes:
        return "too_large"
    if len(entry.path) > MAX_SOURCE_KEY_LENGTH:
        return "path_too_long"
    if any(len(part) > MAX_PATH_SEGMENT_LENGTH for part in entry.path.split("/")):
        return "path_too_long"
    return None


def is_lfs_pointer(content: bytes) -> bool:
    """Whether these bytes are a Git LFS stand-in rather than the file itself.

    Ingesting one would put a 130-byte text file in the library under the name
    of the document a reader expects to find there.
    """
    return content[:60].startswith(b"version https://git-lfs.github.com/spec/")


__all__ = [
    "MAX_PATH_SEGMENT_LENGTH",
    "MAX_SOURCE_KEY_LENGTH",
    "LibraryTooLarge",
    "PassChoice",
    "PassKind",
    "Plan",
    "Removal",
    "Skip",
    "Write",
    "choose_pass",
    "is_lfs_pointer",
    "plan_full",
    "plan_incremental",
]
