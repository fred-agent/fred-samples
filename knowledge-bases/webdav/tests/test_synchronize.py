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
above all what survives a run that went wrong.
"""

from __future__ import annotations

import json

from fred_samples_webdav_kb.ledger import load_ledger
from fred_samples_webdav_kb.synchronize import synchronize
from tests.conftest import File, RecordingBoundary, Share, settings_for, source_for


async def run(share: Share, ledger_path, *, boundary=None, **options):
    """One synchronization, exactly as a dispatched run performs it."""
    settings = settings_for(share, **options)
    source = source_for(share)
    boundary = boundary or RecordingBoundary()
    try:
        report = await synchronize(
            settings=settings,
            source=source,
            boundary=boundary,
            ledger_path=ledger_path,
        )
    finally:
        await source.aclose()
    return report, boundary


async def test_a_first_run_publishes_everything_and_records_it(ledger_path):
    share = Share(files={"a.md": File(b"a"), "docs/b.md": File(b"b")})
    report, boundary = await run(share, ledger_path)

    assert report.succeeded
    assert report.exhaustive
    assert report.created == 2
    assert sorted(boundary.published) == ["a.md", "docs/b.md"]
    assert sorted(load_ledger(ledger_path)) == ["a.md", "docs/b.md"]


async def test_a_second_run_over_an_unchanged_share_publishes_nothing(ledger_path):
    share = Share(files={"a.md": File(b"a")})
    await run(share, ledger_path)
    report, boundary = await run(share, ledger_path)

    assert boundary.published == {}
    assert report.unchanged == 1
    assert report.created == 0


async def test_an_edited_file_is_published_again_as_an_update(ledger_path):
    share = Share(files={"a.md": File(b"a", etag="v1")})
    await run(share, ledger_path)

    share.files["a.md"] = File(b"a changed", etag="v2")
    report, boundary = await run(share, ledger_path)

    assert report.updated == 1
    assert boundary.published["a.md"] == (b"a changed", "etag:v2")


async def test_a_file_the_share_dropped_is_retracted(ledger_path):
    share = Share(files={"a.md": File(b"a"), "b.md": File(b"b")})
    await run(share, ledger_path)

    del share.files["b.md"]
    report, boundary = await run(share, ledger_path)

    assert boundary.retracted == ["b.md"]
    assert report.removed == 1
    assert list(load_ledger(ledger_path)) == ["a.md"]


async def test_a_share_that_cannot_be_reached_changes_nothing(ledger_path):
    share = Share(files={"a.md": File(b"a")})
    await run(share, ledger_path)
    before = load_ledger(ledger_path)

    share.propfind_status = 500
    report, boundary = await run(share, ledger_path)

    assert not report.succeeded
    assert boundary.retracted == []
    assert load_ledger(ledger_path) == before


async def test_a_failed_write_keeps_the_version_the_library_still_holds(ledger_path):
    """Otherwise the document is orphaned the moment the share drops the file.

    Forgetting the entry looks harmless — the next run republishes it. But if
    the file disappears from the share first, no run ever has a record of it
    again, and the document stays in the library for ever.
    """
    share = Share(files={"a.md": File(b"a", etag="v1")})
    await run(share, ledger_path)

    share.files["a.md"] = File(b"a changed", etag="v2")
    report, _ = await run(
        share, ledger_path, boundary=RecordingBoundary(fail_publish={"a.md"})
    )
    assert not report.succeeded
    assert load_ledger(ledger_path) == {"a.md": "etag:v1"}

    del share.files["a.md"]
    _, boundary = await run(share, ledger_path)
    assert boundary.retracted == ["a.md"]


async def test_a_failed_retract_keeps_its_entry_so_a_later_run_retries(ledger_path):
    share = Share(files={"a.md": File(b"a"), "b.md": File(b"b")})
    await run(share, ledger_path)

    del share.files["b.md"]
    report, _ = await run(
        share, ledger_path, boundary=RecordingBoundary(fail_retract={"b.md"})
    )
    assert not report.succeeded
    assert "b.md" in load_ledger(ledger_path)

    _, boundary = await run(share, ledger_path)
    assert boundary.retracted == ["b.md"]


async def test_a_bounded_run_leaves_the_rest_alone_rather_than_retracting_it(
    ledger_path,
):
    share = Share(files={"a.md": File(b"a"), "b.md": File(b"b"), "c.md": File(b"c")})
    await run(share, ledger_path)

    report, boundary = await run(share, ledger_path, max_files=2)

    assert not report.exhaustive
    assert boundary.retracted == []
    assert sorted(load_ledger(ledger_path)) == ["a.md", "b.md", "c.md"]
    assert any(issue.code == "max_files_reached" for issue in report.warnings)


async def test_a_walk_cut_short_never_retracts(ledger_path):
    """A share that never ends is bounded by depth, and then proves nothing."""
    share = Share(files={"a.md": File(b"a")})
    await run(share, ledger_path)

    share.endless = True
    report, boundary = await run(share, ledger_path)

    assert not report.exhaustive
    assert boundary.retracted == []
    assert "a.md" in load_ledger(ledger_path)


async def test_a_file_too_large_is_skipped_and_keeps_its_document(ledger_path):
    share = Share(files={"a.md": File(b"a"), "big.md": File(b"big")})
    await run(share, ledger_path)

    share.oversized.add("big.md")
    report, boundary = await run(share, ledger_path)

    assert boundary.retracted == []
    assert report.skipped == 1
    assert "big.md" in load_ledger(ledger_path)


async def test_an_unreadable_ledger_republishes_and_retracts_nothing(ledger_path):
    """And says the run was not exhaustive, because it no longer knows.

    The walk saw the whole share, but this run has no record of what the
    library holds — so a document the share dropped in the meantime can never
    be found again, and claiming a complete reconciliation would be a lie.
    """
    share = Share(files={"a.md": File(b"a")})
    await run(share, ledger_path)
    ledger_path.write_text("not json at all", encoding="utf-8")

    report, boundary = await run(share, ledger_path)

    assert boundary.published != {}
    assert boundary.retracted == []
    assert not report.exhaustive
    assert any(issue.code == "ledger_unreadable" for issue in report.warnings)


async def test_a_share_full_of_other_files_still_reconciles_completely(ledger_path):
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
    await run(share, ledger_path, max_files=10)

    del share.files["b.md"]
    report, boundary = await run(share, ledger_path, max_files=10)

    assert report.exhaustive
    assert boundary.retracted == ["b.md"]


async def test_accepting_any_certificate_is_reported_on_every_run(ledger_path):
    share = Share(files={"a.md": File(b"a")})
    report, _ = await run(share, ledger_path, trust_any_certificate=True)

    assert any(issue.code == "certificate_not_verified" for issue in report.warnings)


async def test_a_share_with_no_entity_tags_says_why_it_republishes(ledger_path):
    share = Share(files={"a.md": File(b"a")}, etags=False)
    await run(share, ledger_path)
    report, boundary = await run(share, ledger_path)

    # Date and size still catch an untouched file, so this one is quiet.
    assert boundary.published == {}
    assert report.unchanged == 1

    for entry in share.files.values():
        entry.etag = None
        entry.modified = ""
    report, boundary = await run(share, ledger_path)
    assert any(issue.code == "no_version_from_share" for issue in report.warnings)
    assert boundary.published != {}


async def test_the_ledger_is_replaced_whole_rather_than_edited(ledger_path):
    share = Share(files={"a.md": File(b"a")})
    await run(share, ledger_path)
    stored = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert stored == {"a.md": "etag:v1"}


async def test_a_binary_file_reaches_the_library_byte_for_byte(ledger_path):
    """Markdown is only where this starts; images are the same path, unchanged.

    Every byte here is one that breaks something if any layer decides the
    content is text: a PNG signature, a NUL, a lone 0xFF that is not valid
    UTF-8, and a CRLF that a text mode would rewrite.
    """
    png = b"\x89PNG\r\n\x1a\n\x00\xff\xfe\r\n" + bytes(range(256))
    share = Share(files={"pictures/diagram.png": File(png), "notes.md": File(b"text")})

    _, boundary = await run(share, ledger_path, include="**/*.md,**/*.png")

    assert boundary.published["pictures/diagram.png"][0] == png
    assert sorted(boundary.published) == ["notes.md", "pictures/diagram.png"]


async def test_taking_images_as_well_is_a_pattern_not_a_deployment(ledger_path):
    """And the default leaves them out until someone asks for them."""
    share = Share(files={"a.md": File(b"a"), "b.png": File(b"\x89PNG")})

    _, boundary = await run(share, ledger_path)
    assert sorted(boundary.published) == ["a.md"]

    _, boundary = await run(share, ledger_path, include="**/*.md,**/*.png,**/*.jpg")
    assert sorted(boundary.published) == ["b.png"]
