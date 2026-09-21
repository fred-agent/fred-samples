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
A whole run, against a real share, through the real client.

These are the tests that say what a team's library looks like afterwards — and
above all what survives a run that went wrong, now that the library itself is
the only record of what it holds.
"""

from __future__ import annotations

from fred_samples_webdav_kb.synchronize import synchronize
from tests.conftest import File, RecordingBoundary, Share, settings_for, source_for


async def run(share: Share, boundary: RecordingBoundary | None = None, **options):
    """One synchronization, exactly as a dispatched run performs it."""
    boundary = boundary or RecordingBoundary()
    boundary.new_run()
    settings = settings_for(share, **options)
    source = source_for(share)
    try:
        report = await synchronize(
            settings=settings,
            source=source,
            boundary=boundary,
        )
    finally:
        await source.aclose()
    return report, boundary


async def test_a_first_run_publishes_everything_into_an_empty_library():
    share = Share(files={"a.md": File(b"a"), "docs/b.md": File(b"b")})
    report, boundary = await run(share)

    assert report.succeeded
    assert report.exhaustive
    assert report.created == 2
    assert sorted(boundary.published) == ["a.md", "docs/b.md"]
    assert sorted(boundary.contents) == ["a.md", "docs/b.md"]


async def test_a_second_run_over_an_unchanged_share_publishes_nothing():
    share = Share(files={"a.md": File(b"a")})
    _, boundary = await run(share)
    report, boundary = await run(share, boundary)

    assert boundary.published == {}
    assert report.unchanged == 1
    assert report.created == 0


async def test_an_edited_file_is_published_again_as_an_update():
    share = Share(files={"a.md": File(b"a", etag="v1")})
    _, boundary = await run(share)

    share.files["a.md"] = File(b"a changed", etag="v2")
    report, boundary = await run(share, boundary)

    assert report.updated == 1
    assert boundary.published["a.md"] == (b"a changed", "etag:v2")


async def test_a_file_the_share_dropped_is_retracted():
    share = Share(files={"a.md": File(b"a"), "b.md": File(b"b")})
    _, boundary = await run(share)

    del share.files["b.md"]
    report, boundary = await run(share, boundary)

    assert boundary.retracted == ["b.md"]
    assert report.removed == 1
    assert list(boundary.contents) == ["a.md"]


async def test_a_document_the_library_does_not_list_is_published_again():
    """The property that makes an accepted-but-unfinished write safe.

    A write is queued, so it may fail out of sight of this run. The document is
    then simply absent from the next run's listing, is written again, and no
    account of what was sent has to be kept anywhere to notice.
    """
    share = Share(files={"a.md": File(b"a"), "b.md": File(b"b")})
    _, boundary = await run(share)

    # What a worker that failed after the 202 leaves behind.
    del boundary.contents["b.md"]
    report, boundary = await run(share, boundary)

    assert sorted(boundary.published) == ["b.md"]
    assert boundary.retracted == []
    assert report.created == 1
    assert report.unchanged == 1


async def test_a_document_still_being_ingested_is_offered_again():
    """Which is the price of keeping no record, and it has to be said out loud.

    A write is accepted, not finished, and an unfinished document is absent
    from the listing for exactly the same reason a failed one is. This run
    cannot tell the two apart, so it offers it again — correct, idempotent, and
    wasteful for as long as ingestion takes.
    """
    share = Share(files={"a.md": File(b"a")})
    boundary = RecordingBoundary(still_ingesting={"a.md"})
    _, boundary = await run(share, boundary)

    report, boundary = await run(share, boundary)

    assert sorted(boundary.published) == ["a.md"]
    assert boundary.retracted == []
    assert report.unchanged == 0


async def test_a_document_the_library_holds_and_the_share_does_not_is_retracted():
    """Whoever put it there: the listing is the record, not this pod's own past.

    A library a run reconciles against is one a machine fills, which the pod
    declares as it starts. Pointing an instance at a library somebody else's
    documents are in empties it, and that is the behaviour, not an accident.
    """
    share = Share(files={"a.md": File(b"a")})
    boundary = RecordingBoundary(holds={"written-by-somebody-else.md": "v0"})

    report, boundary = await run(share, boundary)

    assert boundary.retracted == ["written-by-somebody-else.md"]
    assert report.removed == 1


async def test_a_share_that_cannot_be_reached_changes_nothing():
    share = Share(files={"a.md": File(b"a")})
    _, boundary = await run(share)
    before = dict(boundary.contents)

    share.propfind_status = 500
    report, boundary = await run(share, boundary)

    assert not report.succeeded
    assert boundary.retracted == []
    assert boundary.contents == before


async def test_a_library_that_cannot_be_read_stops_the_run_before_anything():
    """Reading a failed listing as an empty library retracts a team's documents.

    A run that cannot ask what the library holds knows nothing about it, so it
    writes nothing, removes nothing, and says so — the share is not even walked.
    """
    share = Share(files={"a.md": File(b"a")})
    boundary = RecordingBoundary(fail_listing=True)

    report, boundary = await run(share, boundary)

    assert not report.succeeded
    assert not report.exhaustive
    assert boundary.published == {}
    assert boundary.retracted == []
    assert [issue.code for issue in report.errors] == ["library_unreadable"]
    assert share.propfind_calls == []


async def test_a_failed_write_leaves_the_document_the_library_still_holds():
    """Otherwise the document is orphaned the moment the share drops the file.

    The library still lists the old version, which is both what makes the next
    run write it again and what keeps it eligible for removal.
    """
    share = Share(files={"a.md": File(b"a", etag="v1")})
    _, boundary = await run(share)

    share.files["a.md"] = File(b"a changed", etag="v2")
    boundary.fail_publish = {"a.md"}
    report, boundary = await run(share, boundary)
    assert not report.succeeded
    assert boundary.contents == {"a.md": "etag:v1"}

    boundary.fail_publish = set()
    del share.files["a.md"]
    _, boundary = await run(share, boundary)
    assert boundary.retracted == ["a.md"]


async def test_a_failed_retract_leaves_the_document_so_a_later_run_retries():
    share = Share(files={"a.md": File(b"a"), "b.md": File(b"b")})
    _, boundary = await run(share)

    del share.files["b.md"]
    boundary.fail_retract = {"b.md"}
    report, boundary = await run(share, boundary)
    assert not report.succeeded
    assert "b.md" in boundary.contents

    boundary.fail_retract = set()
    _, boundary = await run(share, boundary)
    assert boundary.retracted == ["b.md"]


async def test_a_bounded_run_leaves_the_rest_alone_rather_than_retracting_it():
    share = Share(files={"a.md": File(b"a"), "b.md": File(b"b"), "c.md": File(b"c")})
    _, boundary = await run(share)

    report, boundary = await run(share, boundary, max_files=2)

    assert not report.exhaustive
    assert boundary.retracted == []
    assert sorted(boundary.contents) == ["a.md", "b.md", "c.md"]
    assert any(issue.code == "max_files_reached" for issue in report.warnings)


async def test_a_walk_cut_short_never_retracts():
    """A share that never ends is bounded by depth, and then proves nothing."""
    share = Share(files={"a.md": File(b"a")})
    _, boundary = await run(share)

    share.endless = True
    report, boundary = await run(share, boundary)

    assert not report.exhaustive
    assert boundary.retracted == []
    assert "a.md" in boundary.contents


async def test_a_file_too_large_is_skipped_and_keeps_its_document():
    share = Share(files={"a.md": File(b"a"), "big.md": File(b"big")})
    _, boundary = await run(share)

    share.oversized.add("big.md")
    report, boundary = await run(share, boundary)

    assert boundary.retracted == []
    assert report.skipped == 1
    assert "big.md" in boundary.contents


async def test_a_share_full_of_other_files_still_reconciles_completely():
    """The bound counts documents, so other files must not make every run partial.

    A share holding a few hundred pictures beside its Markdown would otherwise
    be cut short on every single run — and a run that is never exhaustive never
    removes anything, so a deletion would go unnoticed for ever.
    """
    share = Share(
        files={
            **{f"pictures/{index}.png": File(b"x") for index in range(30)},
            "a.md": File(b"a"),
            "b.md": File(b"b"),
        }
    )
    _, boundary = await run(share, max_files=10)

    del share.files["b.md"]
    report, boundary = await run(share, boundary, max_files=10)

    assert report.exhaustive
    assert boundary.retracted == ["b.md"]


async def test_accepting_any_certificate_is_reported_on_every_run():
    share = Share(files={"a.md": File(b"a")})
    report, _ = await run(share, trust_any_certificate=True)

    assert any(issue.code == "certificate_not_verified" for issue in report.warnings)


async def test_a_share_with_no_entity_tags_says_why_it_republishes():
    share = Share(files={"a.md": File(b"a")}, etags=False)
    _, boundary = await run(share)
    report, boundary = await run(share, boundary)

    # Date and size still catch an untouched file, so this one is quiet.
    assert boundary.published == {}
    assert report.unchanged == 1

    for entry in share.files.values():
        entry.etag = None
        entry.modified = ""
    report, boundary = await run(share, boundary)
    assert any(issue.code == "no_version_from_share" for issue in report.warnings)
    assert boundary.published != {}


async def test_a_binary_file_reaches_the_library_byte_for_byte():
    """Markdown is only where this starts; images are the same path, unchanged.

    Every byte here is one that breaks something if any layer decides the
    content is text: a PNG signature, a NUL, a lone 0xFF that is not valid
    UTF-8, and a CRLF that a text mode would rewrite.
    """
    png = b"\x89PNG\r\n\x1a\n\x00\xff\xfe\r\n" + bytes(range(256))
    share = Share(files={"pictures/diagram.png": File(png), "notes.md": File(b"text")})

    _, boundary = await run(share, include="**/*.md,**/*.png")

    assert boundary.published["pictures/diagram.png"][0] == png
    assert sorted(boundary.published) == ["notes.md", "pictures/diagram.png"]


async def test_taking_images_as_well_is_a_pattern_not_a_deployment():
    """And the default leaves them out until someone asks for them."""
    share = Share(files={"a.md": File(b"a"), "b.png": File(b"\x89PNG")})

    _, boundary = await run(share)
    assert sorted(boundary.published) == ["a.md"]

    _, boundary = await run(share, boundary, include="**/*.md,**/*.png,**/*.jpg")
    assert sorted(boundary.published) == ["b.png"]
