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
One synchronization run, from the ledger the last one left to the one this
leaves behind.

The order is the whole design: walk, decide, write, remove, record. Nothing is
written before the plan is complete, nothing is removed before the writes are
done, and nothing is recorded about a document whose write did not happen.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fred_samples_webdav_kb.document_boundary import Boundary
from fred_samples_webdav_kb.ledger import (
    Ledger,
    LedgerError,
    load_ledger,
    save_ledger,
)
from fred_samples_webdav_kb.plan import Plan, Write, plan_run
from fred_samples_webdav_kb.report import RunReport
from fred_samples_webdav_kb.settings import Settings
from fred_samples_webdav_kb.source import DocumentSource, SourceUnavailable
from fred_samples_webdav_kb.webdav import FileTooLarge

logger = logging.getLogger(__name__)

# Every document costs a fetch from the share and a write that converts and
# indexes it. Four at a time hides both latencies without turning one team's
# run into a load test of someone else's web server.
DEFAULT_CONCURRENCY = 4

# A run where everything fails should say so after a few documents rather than
# after two thousand.
FAILURE_BUDGET = 20


async def synchronize(
    *,
    settings: Settings,
    source: DocumentSource,
    boundary: Boundary,
    ledger_path: Path,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> RunReport:
    """Bring the library to what the share now holds, and say what happened."""
    report = RunReport()

    remembered = True
    try:
        previous = load_ledger(ledger_path)
    except LedgerError as error:
        # Not fatal: everything is republished, which is correct because a
        # document is addressed by its path on both sides. But this run has no
        # record of what the library holds, so documents the share dropped
        # while the ledger was unreadable can no longer be found — which is
        # exactly what `reconciliation_complete` is for.
        report.warn("ledger_unreadable", str(error))
        previous = {}
        remembered = False

    try:
        inventory = await source.inventory()
    except SourceUnavailable as error:
        # A run that never read the share proves nothing about the library.
        report.fail("source_unavailable", str(error))
        return report

    plan = plan_run(
        inventory,
        previous=previous,
        selection=settings.selection,
        max_files=settings.max_files,
        max_file_bytes=settings.max_file_bytes,
    )
    report.exhaustive = plan.exhaustive and remembered
    report.discovered = plan.selected
    report.unchanged = len(plan.unchanged)
    _report_plan(plan, settings, report)

    current = await _apply(
        plan,
        previous=previous,
        source=source,
        boundary=boundary,
        report=report,
        limit=concurrency,
    )

    try:
        save_ledger(ledger_path, current)
    except OSError as error:
        # The documents went out, but the next run cannot know it and will
        # republish all of them. Idempotent, expensive, and worth reporting as
        # the failure it is.
        report.fail("ledger_not_saved", str(error))

    logger.info("[WEBDAV KB] %s: %s", settings.where.url, report.summary())
    return report


def _report_plan(plan: Plan, settings: Settings, report: RunReport) -> None:
    """Say, once, everything about this run an operator needs to know up front."""
    if settings.trust_any_certificate:
        report.warn(
            "certificate_not_verified",
            "This instance accepts any certificate the share presents, so the "
            "connection is not protected against interception. Trust the "
            "issuing authority instead, through $SSL_CERT_FILE.",
        )
    if plan.versionless:
        report.warn(
            "no_version_from_share",
            f"{plan.versionless} files carry neither an entity tag nor a "
            "modification date, so they are republished on every run.",
        )
    if plan.deferred:
        report.warn(
            "max_files_reached",
            f"{len(plan.deferred)} more files match than the bound of "
            f"{settings.max_files}; they stay unsynchronized, and this run "
            "removes nothing, until the bound is raised.",
        )
    if not plan.exhaustive and not plan.deferred:
        report.warn(
            "walk_incomplete",
            "The share was not read exhaustively, so this run removes nothing.",
        )
    for skipped in plan.skips:
        report.skip(skipped.source_key, skipped.reason)


async def _apply(
    plan: Plan,
    *,
    previous: Ledger,
    source: DocumentSource,
    boundary: Boundary,
    report: RunReport,
    limit: int,
) -> Ledger:
    """Write, then remove, and return what the next run should believe.

    A path is recorded with its new version only once that version is actually
    in the library. Everything else keeps the version the library already held,
    which is both what makes the next run retry it and what keeps it eligible
    for removal if the share drops it in the meantime.
    """
    current: Ledger = {path: previous[path] for path in plan.unchanged}
    recorded = asyncio.Lock()
    permits = asyncio.Semaphore(limit)
    abandoned = 0

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
                content = await source.read(write.file)
            except FileTooLarge as error:
                # The share's own listing understated it. Still a skip, never a
                # removal, and the entry it may already have is kept below.
                report.skip(write.source_key, "too_large", str(error))
                return
            except Exception as error:  # noqa: BLE001 - one document's failure
                report.fail("read_failed", str(error), subject=write.source_key)
                return
            try:
                await boundary.publish(
                    relative_path=write.source_key,
                    content=content,
                    version=write.version,
                )
            except Exception as error:  # noqa: BLE001 - one document's failure
                report.fail("write_failed", str(error), subject=write.source_key)
                return
            async with recorded:
                current[write.source_key] = write.version
            report.wrote(
                created=write.source_key not in previous, size_bytes=len(content)
            )

    await asyncio.gather(*(one(write) for write in plan.writes))

    for removal in plan.removals:
        if len(report.errors) >= FAILURE_BUDGET:
            abandoned += 1
            current[removal.source_key] = previous[removal.source_key]
            continue
        try:
            await boundary.retract(relative_path=removal.source_key)
        except Exception as error:  # noqa: BLE001 - one document's failure
            # Kept: the document is still in the library, and a run that forgot
            # it would never try to take it out again.
            current[removal.source_key] = previous[removal.source_key]
            report.fail("remove_failed", str(error), subject=removal.source_key)
            continue
        report.removed += 1

    # Anything this run intended but did not carry out keeps whatever the
    # library already holds for it — a skip, a failed read, a failed write, a
    # document abandoned past the budget. Dropping the entry instead would look
    # harmless, and then the run after next would find the file gone from the
    # share, have no record of it, and leave the document orphaned for ever.
    for path in (
        *(skip.source_key for skip in plan.skips),
        *(w.source_key for w in plan.writes),
    ):
        if path not in current and path in previous:
            current[path] = previous[path]
    if not plan.exhaustive:
        # Nothing this run did not observe may be forgotten: dropping an entry
        # here is what would make the *next* exhaustive run retract a document
        # that was never missing.
        for path, version in previous.items():
            current.setdefault(path, version)

    if abandoned:
        # Otherwise the report reads as a pass that considered everything and
        # happened to fail a few, which is not what happened.
        report.fail(
            "stopped_early",
            f"{abandoned} documents were not attempted after {FAILURE_BUDGET} failures",
        )
    return current
