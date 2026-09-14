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
What this Knowledge Base needs from a repository, and nothing else.

Five questions: where is the branch now, make that revision readable, what does
it hold, what changed since another revision, and what are one file's bytes.
GitHub and GitLab answer them identically, which is why they cost one adapter
between them rather than two implementations.

Every call blocks. The port says so rather than pretending otherwise; the
handler above it is async and keeps them off the event loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class EntryKind(StrEnum):
    """What a repository entry is, since not all of them are documents."""

    file = "file"
    symlink = "symlink"
    submodule = "submodule"
    # Git stores a path as bytes and does not require them to be text; a name
    # that is not text cannot be a key anything is addressed by.
    unnameable = "unnameable"


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One file as the repository holds it at one revision.

    `blob_id` is Git's own content identity — equal ids mean equal bytes, which
    is exactly what a document version has to say, and it costs nothing to
    obtain.
    """

    path: str
    blob_id: str
    size_bytes: int
    kind: EntryKind = EntryKind.file


class ChangeKind(StrEnum):
    added = "added"
    modified = "modified"
    deleted = "deleted"
    renamed = "renamed"


@dataclass(frozen=True, slots=True)
class SourceChange:
    """One difference between two revisions.

    `file` says where the content is now and is absent for a deletion;
    `previous_path` says where it was, and is set for a deletion and a rename —
    the only changes that name a path the new revision does not have.
    """

    kind: ChangeKind
    file: SourceFile | None = None
    previous_path: str | None = None


class SourceUnavailable(RuntimeError):
    """The repository could not be reached, authenticated against, or read."""


class UnknownBranch(SourceUnavailable):
    """The configured branch is not on the remote."""


class RepositorySource(Protocol):
    """A repository this Knowledge Base can synchronize from."""

    def head(self) -> str:
        """The revision the configured branch points at, without fetching it."""
        ...

    def fetch(self, revision: str) -> None:
        """Make `revision` readable locally, before anything below is called."""
        ...

    def holds(self, revision: str) -> bool:
        """Whether `revision` can still be read, and so diffed against."""
        ...

    def inventory(self, revision: str) -> list[SourceFile]:
        """Every entry the revision holds, in no particular order."""
        ...

    def changes(self, base: str, revision: str) -> list[SourceChange]:
        """What `revision` changed relative to `base`, renames detected."""
        ...

    def read(self, file: SourceFile) -> bytes:
        """One file's bytes."""
        ...
