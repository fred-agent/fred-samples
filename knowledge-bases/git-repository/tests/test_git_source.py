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
The adapter, against real repositories and no network.

These are the tests that would have to be mocked if this Knowledge Base read
its repositories through a forge's REST API. Here the repository is real, the
fetch is real, and the only thing missing is the wire.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fred_samples_git_kb.git_source import (
    MIRROR_DIR_ENV,
    GitRepositorySource,
    default_mirror_root,
    mirror_for,
)
from fred_samples_git_kb.source import (
    ChangeKind,
    EntryKind,
    SourceUnavailable,
    UnknownBranch,
)
from tests.conftest import SUBMODULE, SYMLINK, Origin


def opened(origin: Origin, mirror: Path, branch: str = "main") -> GitRepositorySource:
    return GitRepositorySource(url=origin.url, branch=branch, mirror=mirror)


def test_the_branch_is_resolved_without_fetching_anything(origin: Origin, mirror: Path):
    revision = origin.commit({"a.md": b"one"})
    source = opened(origin, mirror)

    assert source.head() == revision
    assert not source.holds(revision), "nothing should have been downloaded yet"


def test_an_empty_branch_means_the_repository_s_own_default(
    origin: Origin, mirror: Path
):
    revision = origin.commit({"a.md": b"one"})

    assert opened(origin, mirror, branch="").head() == revision


def test_a_branch_that_is_not_there_says_so(origin: Origin, mirror: Path):
    origin.commit({"a.md": b"one"})

    with pytest.raises(UnknownBranch):
        opened(origin, mirror, branch="release").head()


def test_a_repository_that_is_not_there_fails_as_a_run_can_report(tmp_path: Path):
    source = GitRepositorySource(
        url=str(tmp_path / "absent"), branch="main", mirror=tmp_path / "mirror"
    )

    with pytest.raises(SourceUnavailable):
        source.head()


def test_an_inventory_names_every_entry_with_its_content_identity(
    origin: Origin, mirror: Path
):
    revision = origin.commit({"a.md": b"one", "docs/b.md": b"two"})
    source = opened(origin, mirror)
    source.fetch(revision)

    held = {entry.path: entry for entry in source.inventory(revision)}
    assert set(held) == {"a.md", "docs/b.md"}
    assert held["a.md"].size_bytes == 3
    assert held["a.md"].kind is EntryKind.file
    assert len(held["a.md"].blob_id) == 40
    assert source.read(held["docs/b.md"]) == b"two"
    source.close()


def test_what_is_not_a_file_is_reported_as_what_it_is(origin: Origin, mirror: Path):
    revision = origin.commit(
        {
            "a.md": b"one",
            "link.md": (SYMLINK, b"a.md"),
            "vendored": (SUBMODULE, b"0" * 40),
        }
    )
    source = opened(origin, mirror)
    source.fetch(revision)

    kinds = {entry.path: entry.kind for entry in source.inventory(revision)}
    assert kinds == {
        "a.md": EntryKind.file,
        "link.md": EntryKind.symlink,
        "vendored": EntryKind.submodule,
    }
    source.close()


def test_two_snapshots_are_compared_with_renames_detected(origin: Origin, mirror: Path):
    long_enough_to_match = b"# a document with enough content to be recognised\n" * 4
    first = origin.commit(
        {
            "keep.md": b"unchanged",
            "edit.md": b"before",
            "gone.md": b"bye",
            "old/name.md": long_enough_to_match,
        }
    )
    second = origin.commit(
        {
            "keep.md": b"unchanged",
            "edit.md": b"after",
            "added.md": b"new",
            "new/name.md": long_enough_to_match,
        }
    )
    source = opened(origin, mirror)
    source.fetch(first)
    source.fetch(second)

    changes = {
        (change.file.path if change.file else change.previous_path): change
        for change in source.changes(first, second)
    }
    assert changes["edit.md"].kind is ChangeKind.modified
    assert changes["added.md"].kind is ChangeKind.added
    assert changes["gone.md"].kind is ChangeKind.deleted
    assert changes["new/name.md"].kind is ChangeKind.renamed
    assert changes["new/name.md"].previous_path == "old/name.md"
    assert "keep.md" not in changes
    source.close()


def test_a_second_run_fetches_only_what_changed(origin: Origin, mirror: Path):
    """No history is downloaded, so each run costs one snapshot's difference."""
    first = origin.commit({"a.md": b"one", "b.md": b"two"})
    source = opened(origin, mirror)
    source.fetch(first)
    after_first = _object_count(mirror)

    second = origin.commit({"a.md": b"one", "b.md": b"two", "c.md": b"three"})
    source.fetch(second)

    assert _object_count(mirror) > after_first
    assert source.holds(first), "the previous snapshot is what the next diff needs"
    source.close()


def test_a_mirror_that_was_never_built_holds_nothing(origin: Origin, mirror: Path):
    revision = origin.commit({"a.md": b"one"})

    assert opened(origin, mirror).holds(revision) is False


def test_a_rewritten_branch_is_still_comparable(origin: Origin, mirror: Path):
    """A force-push needs no special case: two snapshots need no ancestry."""
    first = origin.commit({"a.md": b"one"})
    source = opened(origin, mirror)
    source.fetch(first)
    rewritten = origin.rewrite({"a.md": b"one", "b.md": b"two"})
    source.fetch(rewritten)

    assert source.holds(first)
    changes = source.changes(first, rewritten)
    assert [change.kind for change in changes] == [ChangeKind.added]
    source.close()


def test_a_name_that_is_not_text_costs_that_entry_and_not_the_run(
    origin: Origin, mirror: Path
):
    """Git does not require a path to be text; one such name is not an outage."""
    revision = origin.commit({"a.md": b"one", b"docs/\xff\xfe.md": b"two"})
    source = opened(origin, mirror)
    source.fetch(revision)

    kinds = {entry.path: entry.kind for entry in source.inventory(revision)}
    assert kinds["a.md"] is EntryKind.file
    assert EntryKind.unnameable in kinds.values()
    source.close()


def test_the_mirror_offers_what_it_holds_under_the_one_namespace_that_counts(
    origin: Origin, mirror: Path
):
    """This is what a remote is told the mirror already has.

    The saving itself cannot be observed here — dulwich's local transport
    sends the whole snapshot whatever it is offered — so this asserts the
    precondition, and `test_a_real_remote_sends_only_the_difference` below
    measures the effect against a real Git server.
    """
    revision = origin.commit({"a.md": b"one"})
    source = opened(origin, mirror)
    source.fetch(revision)

    from dulwich.refs import Ref
    from dulwich.repo import Repo

    repository = Repo(str(mirror))
    try:
        offered = repository.refs.as_dict(Ref(b"refs/heads"))
    finally:
        repository.close()
    assert revision.encode() in offered.values(), (
        "a ref outside refs/heads is invisible to the graph walker, and the "
        "remote then re-sends everything on every run"
    )
    source.close()


@pytest.mark.integration
def test_a_real_remote_sends_only_the_difference(origin: Origin, mirror: Path):
    """Measured against `git upload-pack`, which is what a forge runs.

    Needs the Git binary and a subprocess, so it is not part of the offline
    suite: run it with `pytest -m integration`.
    """
    from dulwich.client import SubprocessGitClient
    from dulwich.objects import ObjectID

    from fred_samples_git_kb.git_source import _MIRRORED

    bulky = os.urandom(200_000)  # incompressible: a re-send cannot hide in a pack
    first = origin.commit({"big.bin": bulky, "small.md": b"one"})
    source = opened(origin, mirror)
    client = SubprocessGitClient()

    def fetch(revision: str) -> None:
        client.fetch(
            str(origin.path).encode(),
            source._open(),
            determine_wants=lambda refs, depth=None: [ObjectID(revision.encode())],
            depth=1,
        )
        source._open().refs[_MIRRORED] = ObjectID(revision.encode())

    fetch(first)
    after_first = _fetched_bytes(mirror)
    fetch(origin.commit({"big.bin": bulky, "small.md": b"two"}))

    assert _fetched_bytes(mirror) - after_first < len(bulky) // 10
    source.close()


def test_a_mirror_root_is_always_resolvable(monkeypatch, tmp_path: Path):
    """A pod with no home and no cache still has to be able to synchronize."""
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv(MIRROR_DIR_ENV, raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(_no_home))

    assert default_mirror_root().is_absolute()
    assert default_mirror_root(str(tmp_path)).is_relative_to(tmp_path)


def _no_home() -> Path:
    raise RuntimeError("no home directory for this user")


def test_two_branches_of_one_repository_never_share_a_mirror(tmp_path: Path):
    root = tmp_path / "mirrors"
    main = mirror_for(root, url="https://host/a/b.git", branch="main")
    release = mirror_for(root, url="https://host/a/b.git", branch="release")

    assert main != release
    assert "b.git" not in str(main), "a folder name should not spell out the source"


def _fetched_bytes(mirror: Path) -> int:
    """How much the mirror has been sent, packs and loose objects together."""
    return sum(
        path.stat().st_size
        for path in (mirror / "objects").rglob("*")
        if path.is_file()
    )


def _object_count(mirror: Path) -> int:
    from dulwich.repo import Repo

    repository = Repo(str(mirror))
    try:
        return len(list(repository.object_store))
    finally:
        repository.close()
