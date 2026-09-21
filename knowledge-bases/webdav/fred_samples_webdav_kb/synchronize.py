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
One synchronization run, from what the library already holds to what the share
now shows.

The order is the whole design: ask the library, walk the share, decide, write,
remove. Nothing is written before the plan is complete, and nothing is removed
before the writes are done. Nothing is recorded anywhere — the library is asked
again next time, so a document that did not make it is simply offered again.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

from fred_samples_webdav_kb.document_boundary import Boundary
from fred_samples_webdav_kb.plan import Plan, Write, plan_run
from fred_samples_webdav_kb.report import RunReport
from fred_samples_webdav_kb.settings import Settings
from fred_samples_webdav_kb.source import DocumentSource, SourceUnavailable
from fred_samples_webdav_kb.webdav import CertificateNotTrusted, FileTooLarge

logger = logging.getLogger(__name__)

# Every document costs a fetch from the share and a write that hands it over
# and waits for Fred to ingest it. Four at a time hides both latencies without
# turning one team's run into a load test of someone else's web server.
DEFAULT_CONCURRENCY = 4

# A run where everything fails should say so after a few documents rather than
# after two thousand.
FAILURE_BUDGET = 20


async def synchronize(
    *,
    settings: Settings,
    source: DocumentSource,
    boundary: Boundary,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> RunReport:
    """Bring the library to what the share now holds, and say what happened."""
    report = RunReport()

    try:
        previous = await boundary.documents()
    except Exception as error:  # noqa: BLE001 - a result is owed, not a raise
        # A run that does not know what the library holds can decide nothing
        # about it, least of all that a document should be taken out of it.
        report.fail("library_unreadable", str(error))
        return report

    try:
        inventory = await source.inventory()
    except CertificateNotTrusted as error:
        # Its own code, beside `ca_file_unreadable`: both are a deployment the
        # operator has to change, not a share to wait out, and reading them as
        # "the share is down" is how a week goes by before anyone mounts a root.
        report.fail("certificate_not_trusted", str(error))
        return report
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
    report.exhaustive = plan.exhaustive
    report.discovered = plan.selected
    report.unchanged = len(plan.unchanged)
    _report_plan(plan, settings, report)

    await _apply(
        plan,
        previous=previous,
        source=source,
        boundary=boundary,
        report=report,
        limit=concurrency,
    )

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
    previous: Mapping[str, str | None],
    source: DocumentSource,
    boundary: Boundary,
    report: RunReport,
    limit: int,
) -> None:
    """Write, then remove, and record nothing.

    What the library holds is only ever known by asking it, so everything this
    run intended but did not carry out — a skip, a failed read, a write that
    did not land, a document abandoned past the budget — is left exactly as it
    was, still listed, still eligible for removal if the share drops it, and
    offered again by the next run.
    """
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
                # removal.
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
            report.wrote(
                created=write.source_key not in previous, size_bytes=len(content)
            )

    await asyncio.gather(*(one(write) for write in plan.writes))

    for removal in plan.removals:
        if len(report.errors) >= FAILURE_BUDGET:
            abandoned += 1
            continue
        try:
            await boundary.retract(relative_path=removal.source_key)
        except Exception as error:  # noqa: BLE001 - one document's failure
            # The document is still in the library, so the next run finds it
            # listed, finds the share still without it, and tries again.
            report.fail("remove_failed", str(error), subject=removal.source_key)
            continue
        report.removed += 1

    if abandoned:
        # Otherwise the report reads as a pass that considered everything and
        # happened to fail a few, which is not what happened.
        report.fail(
            "stopped_early",
            f"{abandoned} documents were not attempted after {FAILURE_BUDGET} failures",
        )
