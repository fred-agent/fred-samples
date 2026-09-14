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
Whole runs, against real repositories, with a library that records instead of
writing to Fred.

What every test here is really checking is that the pod keeps nothing: the
library below holds the cursor, and losing everything else still leaves the
next run able to do the right thing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from fred_samples_git_kb.git_source import GitRepositorySource
from fred_samples_git_kb.library import LibraryError
from fred_samples_git_kb.matching import Selection
from fred_samples_git_kb.plan import PassKind
from fred_samples_git_kb.providers import Provider, forge
from fred_samples_git_kb.report import RunReport
from fred_samples_git_kb.settings import Settings
from fred_samples_git_kb.synchronize import synchronize
from tests.conftest import Origin

BIG = 20 * 1024 * 1024


class RecordingLibrary:
    """A library that remembers, so a test can look at what a run decided."""

    def __init__(self, *, refuse: set[str] | None = None) -> None:
        self.cursor: str | None = None
        self.documents: dict[str, tuple[str, bytes]] = {}
        self.retracted: list[str] = []
        self.refuse = refuse or set()

    async def read_cursor(self) -> str | None:
        return self.cursor

    async def record_cursor(self, value: str) -> None:
        self.cursor = value

    async def write(
        self, *, source_key: str, path: str, document_version: str, content: bytes
    ) -> bool:
        if source_key in self.refuse:
            raise LibraryError(f"refusing {source_key}")
        created = source_key not in self.documents
        self.documents[source_key] = (path, content)
        return created

    async def remove(self, *, source_key: str) -> bool:
        self.retracted.append(source_key)
        return self.documents.pop(source_key, None) is not None

    async def aclose(self) -> None:
        return None


def settings(**overrides) -> Settings:
    return Settings(
        forge=forge(provider=Provider.github, repository="owner/name"),
        branch="main",
        token=None,
        selection=overrides.pop("selection", Selection()),
        max_files=overrides.pop("max_files", None),
        max_file_bytes=overrides.pop("max_file_bytes", BIG),
    )


def run(origin: Origin, mirror: Path, library, **overrides) -> RunReport:
    source = GitRepositorySource(url=origin.url, branch="main", mirror=mirror)
    try:
        return asyncio.run(
            synchronize(settings=settings(**overrides), source=source, library=library)
        )
    finally:
        source.close()


def test_a_first_run_writes_the_whole_selection_and_records_where_it_got_to(
    origin: Origin, mirror: Path
):
    origin.commit({"a.md": b"one", "docs/b.md": b"two", "ignored.txt": b"three"})
    library = RecordingLibrary()

    report = run(origin, mirror, library)

    assert report.succeeded
    assert report.pass_kind is PassKind.full
    assert report.exhaustive is True
    assert report.written_new == 2
    assert set(library.documents) == {"a.md", "docs/b.md"}
    assert library.cursor is not None


def test_a_second_run_on_an_unmoved_branch_does_nothing_at_all(
    origin: Origin, mirror: Path
):
    origin.commit({"a.md": b"one"})
    library = RecordingLibrary()
    run(origin, mirror, library)
    recorded = library.cursor

    report = run(origin, mirror, library)

    assert report.pass_kind is PassKind.up_to_date
    assert report.written_new == report.written_existing == 0
    assert library.cursor == recorded


def test_editing_one_document_updates_that_one(origin: Origin, mirror: Path):
    origin.commit({"a.md": b"one", "b.md": b"two"})
    library = RecordingLibrary()
    run(origin, mirror, library)
    origin.commit({"a.md": b"one", "b.md": b"revised"})

    report = run(origin, mirror, library)

    assert report.pass_kind is PassKind.incremental
    assert report.exhaustive is False
    assert (report.written_new, report.written_existing) == (0, 1)
    assert library.documents["b.md"][1] == b"revised"


def test_a_document_the_repository_dropped_leaves_the_library(
    origin: Origin, mirror: Path
):
    origin.commit({"a.md": b"one", "b.md": b"two"})
    library = RecordingLibrary()
    run(origin, mirror, library)
    origin.commit({"a.md": b"one"})

    report = run(origin, mirror, library)

    assert report.retracted == 1
    assert library.retracted == ["b.md"]
    assert set(library.documents) == {"a.md"}


def test_a_renamed_document_moves_under_its_new_key(origin: Origin, mirror: Path):
    content = b"# a document long enough to be recognised after a move\n" * 4
    origin.commit({"old.md": content})
    library = RecordingLibrary()
    run(origin, mirror, library)
    origin.commit({"new.md": content})

    run(origin, mirror, library)

    assert set(library.documents) == {"new.md"}
    assert library.retracted == ["old.md"]


def test_a_rename_whose_write_failed_keeps_the_old_document(
    origin: Origin, mirror: Path
):
    """Taking the old name out would leave the library holding neither."""
    content = b"# a document long enough to be recognised after a move\n" * 4
    origin.commit({"old.md": content})
    library = RecordingLibrary()
    run(origin, mirror, library)
    origin.commit({"new.md": content})
    library.refuse = {"new.md"}

    report = run(origin, mirror, library)

    assert not report.succeeded
    assert set(library.documents) == {"old.md"}
    assert library.retracted == []


def test_widening_the_selection_re_reads_the_whole_repository(
    origin: Origin, mirror: Path
):
    """No difference between two revisions would ever mention those files."""
    origin.commit({"a.md": b"one", "notes.txt": b"two"})
    library = RecordingLibrary()
    run(origin, mirror, library)

    report = run(origin, mirror, library, selection=Selection(include=["**/*.txt"]))

    assert report.pass_kind is PassKind.full
    assert "notes.txt" in library.documents


def test_a_document_that_could_not_be_written_keeps_the_cursor_where_it_was(
    origin: Origin, mirror: Path
):
    """Moving on would leave a document nobody ever looks at again."""
    origin.commit({"a.md": b"one", "b.md": b"two"})
    library = RecordingLibrary(refuse={"b.md"})

    report = run(origin, mirror, library)

    assert not report.succeeded
    assert report.may_advance_cursor is False
    assert library.cursor is None
    assert "a.md" in library.documents, "the rest of the run still happened"


def test_a_run_that_recovers_advances_past_the_whole_pass(origin: Origin, mirror: Path):
    origin.commit({"a.md": b"one", "b.md": b"two"})
    failing = RecordingLibrary(refuse={"b.md"})
    run(origin, mirror, failing)
    recovered = RecordingLibrary()

    report = run(origin, mirror, recovered)

    assert report.succeeded
    assert recovered.cursor is not None
    assert set(recovered.documents) == {"a.md", "b.md"}


def test_a_pass_cut_short_by_failures_says_it_was_cut_short(
    origin: Origin, mirror: Path
):
    """Otherwise the report reads as a pass that saw everything and failed a few."""
    origin.commit({f"{index}.md": str(index).encode() for index in range(40)})
    library = RecordingLibrary(refuse={f"{index}.md" for index in range(40)})

    report = run(origin, mirror, library)

    codes = [issue.code for issue in report.errors]
    assert "stopped_early" in codes
    assert library.cursor is None


def test_a_library_that_cannot_be_asked_for_its_cursor_is_not_written_to(
    origin: Origin, mirror: Path
):
    class Unreachable(RecordingLibrary):
        async def read_cursor(self) -> str | None:
            raise LibraryError("the control plane did not answer")

    library = Unreachable()
    origin.commit({"a.md": b"one"})

    report = run(origin, mirror, library)

    assert [issue.code for issue in report.errors] == ["cursor_not_read"]
    assert library.documents == {}


def test_a_repository_that_cannot_be_reached_touches_nothing(tmp_path: Path):
    library = RecordingLibrary()
    source = GitRepositorySource(
        url=str(tmp_path / "absent"), branch="main", mirror=tmp_path / "mirror"
    )

    report = asyncio.run(
        synchronize(settings=settings(), source=source, library=library)
    )

    assert not report.succeeded
    assert [issue.code for issue in report.errors] == ["source_unavailable"]
    assert library.documents == {}
    assert library.cursor is None


def test_a_repository_past_the_bound_is_refused_before_anything_is_written(
    origin: Origin, mirror: Path
):
    origin.commit({f"{index}.md": b"x" for index in range(5)})
    library = RecordingLibrary()

    report = run(origin, mirror, library, max_files=3)

    assert [issue.code for issue in report.errors] == ["library_too_large"]
    assert library.documents == {}


@pytest.mark.parametrize("concurrent", [1, 4])
def test_the_same_run_is_the_same_whatever_the_concurrency(
    origin: Origin, mirror: Path, concurrent: int
):
    origin.commit({f"{index}.md": str(index).encode() for index in range(12)})
    library = RecordingLibrary()
    source = GitRepositorySource(url=origin.url, branch="main", mirror=mirror)

    report = asyncio.run(
        synchronize(
            settings=settings(),
            source=source,
            library=library,
            concurrency=concurrent,
        )
    )
    source.close()

    assert report.written_new == 12
    assert len(library.documents) == 12
