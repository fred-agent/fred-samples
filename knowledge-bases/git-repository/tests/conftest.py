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
Real repositories, built object by object, with no network and no Git binary.

Each commit is given as the complete set of files that revision holds, which
is how anyone describes a snapshot — and writing trees directly is the only
way to produce the entries a worktree cannot easily make: a symlink, a
submodule, a file at a mode nobody would choose by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dulwich.objects import Blob, Commit, ObjectID, Tree
from dulwich.refs import Ref
from dulwich.repo import Repo

BRANCH = Ref(b"refs/heads/main")
HEAD = Ref(b"HEAD")
REGULAR = 0o100644
SYMLINK = 0o120000
SUBMODULE = 0o160000
DIRECTORY = 0o040000

Entry = bytes | tuple[int, bytes]
Named = str | bytes
Held = tuple[int, ObjectID]


class Origin:
    """A repository other code can fetch from, one snapshot at a time."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.repo = Repo.init_bare(str(path), mkdir=True)
        self.repo.refs.set_symbolic_ref(HEAD, BRANCH)
        self._parents: list[ObjectID] = []

    @property
    def url(self) -> str:
        return str(self.path)

    def commit(self, files: dict[Named, Entry], *, message: str = "snapshot") -> str:
        """Record a revision holding exactly `files`, and return its id.

        A name may be given as bytes, because Git does not require a path to be
        text and a repository holding one is a case this has to survive.
        """
        entries = {
            (path if isinstance(path, bytes) else path.encode()): self._blob(entry)
            for path, entry in files.items()
        }
        commit = Commit()
        commit.tree = self._tree(entries)
        commit.parents = list(self._parents)
        commit.author = commit.committer = b"Test <test@example.com>"
        commit.commit_time = commit.author_time = 1_700_000_000 + len(self._parents)
        commit.commit_timezone = commit.author_timezone = 0
        commit.message = message.encode()
        self.repo.object_store.add_object(commit)
        self.repo.refs[BRANCH] = commit.id
        self._parents = [commit.id]
        return commit.id.decode()

    def rewrite(self, files: dict[Named, Entry], *, message: str = "rewritten") -> str:
        """Force-push: a revision with no relation to what the branch held."""
        self._parents = []
        return self.commit(files, message=message)

    def close(self) -> None:
        self.repo.close()

    def _blob(self, entry: Entry) -> Held:
        mode, content = entry if isinstance(entry, tuple) else (REGULAR, entry)
        if mode == SUBMODULE:
            # A gitlink names a commit in another repository, which this one
            # does not hold — that absence is the point of the test.
            return mode, ObjectID(content)
        blob = Blob.from_string(content)
        self.repo.object_store.add_object(blob)
        return mode, blob.id

    def _tree(self, entries: dict[bytes, Held]) -> ObjectID:
        tree = Tree()
        folders: dict[bytes, dict[bytes, Held]] = {}
        for path, (mode, sha) in entries.items():
            name, _, remainder = path.partition(b"/")
            if remainder:
                folders.setdefault(name, {})[remainder] = (mode, sha)
            else:
                tree.add(name, mode, sha)
        for name, nested in folders.items():
            tree.add(name, DIRECTORY, self._tree(nested))
        self.repo.object_store.add_object(tree)
        return tree.id


@pytest.fixture
def origin(tmp_path: Path):
    """An empty repository to commit snapshots into."""
    repository = Origin(tmp_path / "origin")
    yield repository
    repository.close()


@pytest.fixture
def mirror(tmp_path: Path) -> Path:
    return tmp_path / "mirror"


@pytest.fixture(autouse=True)
def _no_ambient_fred_configuration(monkeypatch: pytest.MonkeyPatch):
    """No test may pick up the configuration a developer happens to have.

    A pod resolves `./config/configuration.yaml` by default, so a suite run
    from this directory would otherwise reach the real Control Plane and
    Knowledge Flow of whoever ran it — passing or failing on their machine's
    state rather than on the code.
    """
    monkeypatch.setenv("CONFIG_FILE", "/nonexistent/configuration.yaml")
    monkeypatch.setenv("ENV_FILE", "/nonexistent/.env")
