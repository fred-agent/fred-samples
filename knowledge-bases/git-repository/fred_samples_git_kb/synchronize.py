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
One synchronization run, from the cursor Fred holds to the cursor it records.

The shape worth noticing is what is *not* here: no ledger, no local state, no
Fred-side identifier. The cursor lives in Fred, the difference between two
revisions is computed by Git, and this file only decides what that difference
means for a library.
"""

from __future__ import annotations

import asyncio
import logging

from fred_samples_git_kb.cursor import Cursor
from fred_samples_git_kb.library import Library
from fred_samples_git_kb.plan import (
    LibraryTooLarge,
    PassChoice,
    PassKind,
    Plan,
    Removal,
    Write,
    choose_pass,
    is_lfs_pointer,
    plan_full,
    plan_incremental,
)
from fred_samples_git_kb.report import RunReport
from fred_samples_git_kb.settings import Settings
from fred_samples_git_kb.source import RepositorySource, SourceUnavailable

logger = logging.getLogger(__name__)

# Writing is a remote call that converts and indexes; reading is a local file.
# Only the first is worth overlapping, and four at a time is enough to hide the
# latency without turning one team's run into a load test.
DEFAULT_CONCURRENCY = 4

# A run where everything fails should say so after a few documents rather than
# after two thousand.
FAILURE_BUDGET = 20


async def synchronize(
    *,
    settings: Settings,
    source: RepositorySource,
    library: Library,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> RunReport:
    """Bring the library to the repository's current state, and say what happened."""
    digest = settings.selection.digest
    try:
        cursor = Cursor.parse(await library.read_cursor())
    except Exception as error:  # noqa: BLE001 - a library that cannot be read
        # Not the same as an unreadable cursor, which reads as none and costs a
        # full pass: the library itself did not answer, so writing into it now
        # would be guessing.
        report = RunReport(pass_kind=PassKind.full, revision="")
        report.fail("cursor_not_read", str(error))
        return report

    try:
        head = await asyncio.to_thread(source.head)
    except SourceUnavailable as error:
        return _unreachable(error)

    # Asked before fetching anything: a branch that has not moved costs one
    # remote ref listing and nothing else, which is what most scheduled runs are.
    unfetched = choose_pass(
        cursor=cursor,
        head_revision=head,
        selection_digest=digest,
        base_available=False,
    )
    if unfetched.kind is PassKind.up_to_date:
        # No warning: on a schedule this is what most runs are, and a warning
        # raised by every ordinary run is one nobody reads on the run that
        # matters. The summary already says the pass did nothing.
        return RunReport(pass_kind=PassKind.up_to_date, revision=head)

    try:
        await asyncio.to_thread(source.fetch, head)
        base_available = cursor is not None and await asyncio.to_thread(
            source.holds, cursor.revision
        )
        choice = choose_pass(
            cursor=cursor,
            head_revision=head,
            selection_digest=digest,
            base_available=base_available,
        )
        held: dict[str, str | None] = {}
        if choice.kind is PassKind.full:
            try:
                held = await library.documents()
            except Exception as error:  # noqa: BLE001 - same reasoning as the cursor
                report = RunReport(pass_kind=PassKind.full, revision=head)
                report.fail("library_not_read", str(error))
                return report
        plan = await _build(choice, head, settings, source, held)
    except SourceUnavailable as error:
        return _unreachable(error)
    except LibraryTooLarge as error:
        report = RunReport(pass_kind=PassKind.full, revision=head)
        report.fail("library_too_large", str(error))
        return report

    report = RunReport(
        pass_kind=choice.kind,
        revision=head,
        base=choice.base if choice.kind is PassKind.incremental else None,
        exhaustive=plan.exhaustive,
        considered=len(plan.writes)
        + len(plan.removals)
        + len(plan.skips)
        + plan.unchanged,
        unchanged=plan.unchanged,
    )
    if choice.reason != "since_last_run":
        report.warn(choice.reason, "a full pass was needed")
    for skipped in plan.skips:
        report.skip(skipped.source_key, skipped.reason)

    await _apply(plan, source=source, library=library, report=report, limit=concurrency)

    if report.may_advance_cursor:
        try:
            await library.record_cursor(
                Cursor(revision=head, selection=digest).render()
            )
        except Exception as error:  # noqa: BLE001 - any failure loses the progress
            # The documents went out but the next run cannot know it, so it will
            # repeat the whole pass. Idempotent, expensive, and worth reporting.
            report.fail("cursor_not_recorded", str(error))
    logger.info("[GIT KB] %s", report.summary())
    return report


async def _build(
    choice: PassChoice,
    head: str,
    settings: Settings,
    source: RepositorySource,
    held: dict[str, str | None],
) -> Plan:
    if choice.kind is PassKind.incremental:
        assert choice.base is not None
        changes = await asyncio.to_thread(source.changes, choice.base, head)
        return plan_incremental(
            changes,
            selection=settings.selection,
            max_files=settings.max_files,
            max_file_bytes=settings.max_file_bytes,
        )
    files = await asyncio.to_thread(source.inventory, head)
    return plan_full(
        files,
        held=held,
        selection=settings.selection,
        max_files=settings.max_files,
        max_file_bytes=settings.max_file_bytes,
    )


async def _apply(
    plan: Plan,
    *,
    source: RepositorySource,
    library: Library,
    report: RunReport,
    limit: int,
) -> None:
    """Write first, then remove, so a renamed document is never absent."""
    # The mirror is one repository opened once; reads are serialized because
    # that is what makes them safe, and they are local and fast enough that
    # overlapping them would buy nothing.
    reading = asyncio.Lock()
    permits = asyncio.Semaphore(limit)
    abandoned = 0
    unwritten: set[str] = set()

    async def one(write: Write) -> None:
        nonlocal abandoned
        async with permits:
            # Inside the permit, not before it: every task starts at once and
            # then waits here, so a check made before the wait is always made
            # before the first failure has happened.
            if len(report.errors) >= FAILURE_BUDGET:
                abandoned += 1
                return
            try:
                async with reading:
                    content = await asyncio.to_thread(source.read, write.file)
            except Exception as error:  # noqa: BLE001 - one document's failure
                report.fail("read_failed", str(error), subject=write.source_key)
                unwritten.add(write.source_key)
                return
            if is_lfs_pointer(content):
                report.skip(
                    write.source_key, "lfs_pointer", "stored outside the repository"
                )
                unwritten.add(write.source_key)
                return
            try:
                created = await library.write(
                    source_key=write.source_key,
                    document_version=write.document_version,
                    content=content,
                )
            except Exception as error:  # noqa: BLE001 - one document's failure
                report.fail("write_failed", str(error), subject=write.source_key)
                unwritten.add(write.source_key)
                return
            report.wrote(created=created)

    await asyncio.gather(*(one(write) for write in plan.writes))

    # A full pass cannot pair a removal with the write replacing it, so after a
    # failed write it removes nothing; the run has failed and the next one replays.
    removals = () if plan.exhaustive and unwritten else plan.removals
    for removal in removals:
        if len(report.errors) >= FAILURE_BUDGET:
            abandoned += 1
            continue
        if removal.replaced_by is not None and removal.replaced_by in unwritten:
            # The name this document moved to never made it into the library,
            # so taking the old one out now would leave neither. The run has
            # already failed, so the next one replays this difference.
            continue
        await _remove(removal, library=library, report=report)

    if abandoned:
        # Otherwise the report reads as a pass that considered everything and
        # happened to fail a few, which is not what happened.
        report.fail(
            "stopped_early",
            f"{abandoned} documents were not attempted after {FAILURE_BUDGET} failures",
        )


async def _remove(removal: Removal, *, library: Library, report: RunReport) -> None:
    try:
        await library.remove(source_key=removal.source_key)
    except Exception as error:  # noqa: BLE001 - one document's failure
        report.fail("remove_failed", str(error), subject=removal.source_key)
        return
    report.removed()


def _unreachable(error: SourceUnavailable) -> RunReport:
    """A run that never read the repository proves nothing about the library."""
    report = RunReport(pass_kind=PassKind.full, revision="")
    report.fail("source_unavailable", str(error))
    return report
