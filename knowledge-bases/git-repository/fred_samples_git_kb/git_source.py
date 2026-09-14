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
A repository read over HTTPS, through a local mirror.

Each run fetches the branch tip and no history at all. The previous run's
trees are still in the mirror, so two consecutive tips are enough to diff
against each other, and nothing that has not changed is downloaded twice. The
mirror is therefore a cache and never state: losing it costs one fetch and one
full pass, never correctness.

The token is passed as a parameter. It is never written into the remote URL,
never placed on a command line, and never stored in the mirror's
configuration — so nothing puts it on disk and no other process on the machine
can read it out of a process listing.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from dulwich.client import GitClient, get_transport_and_path
from dulwich.diff_tree import (
    CHANGE_ADD,
    CHANGE_COPY,
    CHANGE_DELETE,
    CHANGE_MODIFY,
    CHANGE_RENAME,
    RenameDetector,
    tree_changes,
)
from dulwich.object_store import iter_tree_contents
from dulwich.objects import Blob, Commit, ObjectID
from dulwich.refs import Ref
from dulwich.repo import Repo

from fred_samples_git_kb.source import (
    ChangeKind,
    EntryKind,
    SourceChange,
    SourceFile,
    SourceUnavailable,
    UnknownBranch,
)

logger = logging.getLogger(__name__)

# One snapshot per fetch. Deeper would download history nobody reads; shallower
# is not a thing.
_DEPTH = 1

# Where the mirror records what it has. It must live under `refs/heads/`, even
# though it tracks a synchronization rather than a branch: that is the only
# namespace the graph walker offers the remote as "already held", and anywhere
# else the whole snapshot is sent again every run (measured: 201 KB against
# 1.4 KB).
_MIRRORED = Ref(b"refs/heads/fred-kb-mirror")

# A deployed pod points this at a volume. Anywhere else a cache directory does,
# which is all a mirror ever is.
MIRROR_DIR_ENV = "FRED_SAMPLES_GIT_MIRROR_DIR"
_MIRRORS = "fred-samples-git-kb"

_CHANGE_KINDS = {
    CHANGE_ADD: ChangeKind.added,
    CHANGE_COPY: ChangeKind.added,
    CHANGE_MODIFY: ChangeKind.modified,
    CHANGE_DELETE: ChangeKind.deleted,
    CHANGE_RENAME: ChangeKind.renamed,
}


class GitRepositorySource:
    """One branch of one repository, mirrored locally and read from there."""

    def __init__(
        self,
        *,
        url: str,
        branch: str = "",
        mirror: Path,
        username: str | None = None,
        token: str | None = None,
    ) -> None:
        self._url = url
        self._branch = branch
        self._mirror = mirror
        self._credentials = {"username": username, "password": token} if token else {}
        self._repo: Repo | None = None

    # ── the remote ────────────────────────────────────────────────────────────

    def head(self) -> str:
        """Resolve the branch, listing the remote's refs and fetching nothing."""
        client, path = self._transport()
        with _wrapped("listing the repository"):
            listed = client.get_refs(_encode(path))
        # No branch configured means the one the repository itself points HEAD
        # at, which is what an operator who did not think about it meant.
        wanted = Ref(f"refs/heads/{self._branch}".encode() if self._branch else b"HEAD")
        revision = listed.refs.get(wanted)
        if revision is None:
            named = self._branch or "the default branch"
            raise UnknownBranch(f"{named} is not on {self._url}")
        return revision.decode()

    def fetch(self, revision: str) -> None:
        """Bring one revision's snapshot into the mirror, and nothing else."""
        if self.holds(revision):
            return
        client, path = self._transport()

        def wants(refs: Mapping[Ref, ObjectID], depth: int | None = None):
            """One revision, named: not the branch, not the tags, not the
            other branches a busy repository advertises."""
            return [_oid(revision)]

        with _wrapped("fetching the repository"):
            client.fetch(
                _encode(path), self._open(), determine_wants=wants, depth=_DEPTH
            )
        if not self.holds(revision):
            raise SourceUnavailable(f"{revision[:8]} was not served by the remote")
        # What the mirror holds has to be reachable from a ref the graph walker
        # reads, or the next fetch offers the remote nothing and is served the
        # whole snapshot again. The previous revision stays readable — nothing
        # here prunes — so the difference this run needs is still there.
        self._open().refs[_MIRRORED] = _oid(revision)

    # ── the mirror ────────────────────────────────────────────────────────────

    def holds(self, revision: str) -> bool:
        """Whether the mirror can still read that revision.

        A rewritten branch does not make it false: comparing two snapshots
        needs no ancestry between them, so a force-push is still answered by a
        difference. What makes it false is a mirror this pod does not have —
        a first run, a cleared cache, a pod rescheduled elsewhere.
        """
        try:
            return _oid(revision) in self._open().object_store
        except (SourceUnavailable, ValueError):
            return False

    def inventory(self, revision: str) -> list[SourceFile]:
        repository = self._open()
        with _wrapped(f"reading {revision[:8]}"):
            tree = self._commit(revision).tree
            return [
                self._entry(path, mode, sha)
                for path, mode, sha in iter_tree_contents(repository.object_store, tree)
            ]

    def changes(self, base: str, revision: str) -> list[SourceChange]:
        store = self._open().object_store
        with _wrapped(f"comparing {base[:8]} and {revision[:8]}"):
            differences = tree_changes(
                store,
                self._commit(base).tree,
                self._commit(revision).tree,
                rename_detector=RenameDetector(store),
            )
            return [
                change
                for change in (self._change(difference) for difference in differences)
                if change is not None
            ]

    def read(self, file: SourceFile) -> bytes:
        with _wrapped(f"reading {file.path}"):
            stored = self._open().object_store[_oid(file.blob_id)]
            if not isinstance(stored, Blob):
                raise SourceUnavailable(f"{file.path} is not a file")
            return stored.data

    def close(self) -> None:
        if self._repo is not None:
            self._repo.close()
            self._repo = None

    # ── internals ─────────────────────────────────────────────────────────────

    def _open(self) -> Repo:
        if self._repo is None:
            self._mirror.mkdir(parents=True, exist_ok=True)
            with _wrapped(f"opening the mirror at {self._mirror}"):
                # Bare: the mirror holds objects to read, never a checkout —
                # nothing here ever needs a file on disk at its own path.
                self._repo = (
                    Repo(str(self._mirror))
                    if (self._mirror / "objects").is_dir()
                    else Repo.init_bare(str(self._mirror))
                )
        return self._repo

    def _transport(self) -> tuple[GitClient, str | bytes]:
        with _wrapped("reaching the repository"):
            return get_transport_and_path(self._url, **self._credentials)

    def _commit(self, revision: str) -> Commit:
        stored = self._open()[_oid(revision)]
        if not isinstance(stored, Commit):
            raise SourceUnavailable(f"{revision[:8]} is not a revision")
        return stored

    def _entry(self, raw: bytes, mode: int, sha: ObjectID) -> SourceFile:
        # Git stores a path as bytes and does not require them to be text. One
        # such name in a repository must cost that one entry, not the run: the
        # name still has to be shown, so it is decoded loosely for the report
        # and the entry is marked as one nothing can be addressed by.
        named = _decode(raw)
        kind = EntryKind.unnameable if named is None else _kind(mode)
        return SourceFile(
            path=named if named is not None else raw.decode(errors="replace"),
            blob_id=sha.decode(),
            size_bytes=self._size(sha) if kind is EntryKind.file else 0,
            kind=kind,
        )

    def _size(self, sha: ObjectID) -> int:
        """The blob's length, which a shallow fetch always brought along."""
        try:
            return self._open().object_store[sha].raw_length()
        except KeyError:
            return 0

    def _change(self, difference) -> SourceChange | None:
        kind = _CHANGE_KINDS.get(difference.type)
        if kind is None:  # unchanged, which a diff reports for a tree's own sake
            return None
        previous = difference.old.path if difference.old else None
        # A name that is not text was never written under a key, so there is
        # nothing to take back out under one either.
        vacated = _decode(previous) if previous is not None else None
        if kind is ChangeKind.deleted:
            return SourceChange(kind=kind, previous_path=vacated)
        new = difference.new
        return SourceChange(
            kind=kind,
            file=self._entry(new.path, new.mode, new.sha),
            previous_path=vacated if kind is ChangeKind.renamed else None,
        )


def default_mirror_root(configured: str = "") -> Path:
    """Where mirrors live, resolved without ever failing.

    A pod may have neither a home directory nor a cache one, and resolving
    those raises. A run that cannot say where its cache goes should still
    synchronize: a temporary directory is a poor mirror, not a broken run —
    it costs one full pass after every restart.
    """
    for candidate in (
        configured,
        os.getenv(MIRROR_DIR_ENV, ""),
        os.getenv("XDG_CACHE_HOME", ""),
    ):
        if candidate.strip():
            return Path(candidate) / _MIRRORS
    try:
        return Path.home() / ".cache" / _MIRRORS
    except RuntimeError:
        return Path(tempfile.gettempdir()) / _MIRRORS


def mirror_for(root: Path, *, url: str, branch: str, instance: str = "") -> Path:
    """Where one branch of one repository is mirrored, under a chosen root.

    Named by a digest rather than by the repository, so no folder on the
    machine spells out what is being synchronized and two branches of one
    repository never share a mirror. The instance is part of the name too:
    two teams synchronizing the same branch would otherwise write one mirror
    from two runs at once.
    """
    key = hashlib.sha256(f"{instance}\n{url}\n{branch}".encode()).hexdigest()[:16]
    return root / key


def _kind(mode: int | None) -> EntryKind:
    if mode is not None and stat.S_IFMT(mode) == 0o160000:
        return EntryKind.submodule
    if mode is not None and stat.S_ISLNK(mode):
        return EntryKind.symlink
    return EntryKind.file


def _oid(revision: str) -> ObjectID:
    return ObjectID(revision.encode())


def _encode(path: str | bytes) -> bytes:
    return path if isinstance(path, bytes) else path.encode()


def _decode(path: bytes) -> str | None:
    """The path as text, or None when Git is holding bytes that are not text."""
    try:
        return path.decode()
    except UnicodeDecodeError:
        return None


@contextmanager
def _wrapped(what: str) -> Iterator[None]:
    """Turn any failure below into one this Knowledge Base reports honestly.

    Dulwich, urllib3 and the object store each raise their own vocabulary and a
    run can act on none of it differently. What it can do is name the step that
    failed and stay a run rather than become a traceback.
    """
    try:
        yield
    except SourceUnavailable:
        raise
    except Exception as error:
        raise SourceUnavailable(f"{what}: {type(error).__name__}: {error}") from error
