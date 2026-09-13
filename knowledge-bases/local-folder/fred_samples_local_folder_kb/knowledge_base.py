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
A Knowledge Base that synchronizes Markdown files from a local folder.

Read this file first — it is the whole authoring surface: one declaration, the
configuration an operator fills in, and one async handler that reports honest
counters back to Fred. Everything else in the package supports these.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fred_sdk.contracts.models import FieldSpec, TuningValue
from fred_sdk.knowledge_base import (
    MAX_ISSUES,
    KnowledgeBase,
    KnowledgeBaseIssue,
    KnowledgeBaseRunContext,
    KnowledgeBaseRunOutcome,
    KnowledgeBaseSyncResult,
)

from fred_samples_local_folder_kb.document_boundary import SOURCE_TAG, open_boundary
from fred_samples_local_folder_kb.ledger import (
    Ledger,
    LedgerError,
    ledger_path_for,
    load_ledger,
    save_ledger,
)

logger = logging.getLogger(__name__)

DEFAULT_GLOB = "**/*.md"
_HASH_CHUNK_BYTES = 1 << 20

kb = KnowledgeBase(
    id="fred.samples.local-folder",
    version="1.0.0",
    name="Local folder",
    description=(
        "Synchronize Markdown documents from a folder on the machine running "
        "this Knowledge Base."
    ),
    configuration_fields=[
        FieldSpec(
            key="root_path",
            type="string",
            title="Folder",
            description="Absolute path of the folder to synchronize.",
            required=True,
        ),
        FieldSpec(
            key="glob",
            type="string",
            title="File pattern",
            description=(
                "Which files to pick up, relative to the folder. Hidden files "
                "and folders are never picked up."
            ),
            default=DEFAULT_GLOB,
        ),
        FieldSpec(
            key="max_files",
            type="integer",
            title="Maximum files per run",
            description=(
                "Safety bound. A run stops after this many files and leaves the "
                "rest of the folder unsynchronized until the bound is raised. "
                "Leave empty to synchronize everything."
            ),
            min=1,
        ),
    ],
)


@kb.synchronize
async def synchronize(context: KnowledgeBaseRunContext) -> KnowledgeBaseSyncResult:
    """Reconcile the configured folder with what the previous run published."""
    try:
        # Reading the configuration probes the filesystem, and the walk hashes
        # every file — blocking work kept off the event loop. A handler owes
        # Fred a result, not an exception, including for a glob that only turns
        # out to be malformed once the walk starts.
        settings = await asyncio.to_thread(_read_settings, context.configuration)
        return await _synchronize(
            settings, ledger_path_for(context.instance_id), context
        )
    except _ConfigurationError as error:
        return _failed(error.code, str(error))


# ── Configuration ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Settings:
    root: Path
    glob: str
    max_files: int | None


class _ConfigurationError(ValueError):
    """A configured value this implementation cannot work with."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _read_settings(configuration: Mapping[str, TuningValue]) -> _Settings:
    raw_root = configuration.get("root_path")
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise _ConfigurationError(
            "root_path_missing", "root_path is required and must be a folder path"
        )
    root = Path(raw_root).expanduser().resolve()
    if not root.exists():
        raise _ConfigurationError("root_path_missing", f"{root} does not exist")
    if not root.is_dir():
        raise _ConfigurationError(
            "root_path_not_a_directory", f"{root} is not a folder"
        )

    return _Settings(
        root=root,
        glob=_read_glob(configuration.get("glob", DEFAULT_GLOB)),
        max_files=_read_max_files(configuration.get("max_files")),
    )


def _read_glob(raw: TuningValue | None) -> str:
    """An optional field cleared in a form arrives empty, not absent."""
    if not isinstance(raw, str):
        raise _ConfigurationError("glob_invalid", "glob must be a text pattern")
    pattern = raw.strip() or DEFAULT_GLOB
    parts = PurePosixPath(pattern).parts
    if PurePosixPath(pattern).is_absolute() or ".." in parts:
        raise _ConfigurationError(
            "glob_invalid", f"glob {pattern!r} must stay inside the configured folder"
        )
    return pattern


def _read_max_files(raw: TuningValue | None) -> int | None:
    """Read the bound as the integer the field declares.

    Fred validates an instance's values against the declared fields before a
    run starts and refuses a string-shaped number rather than coercing it, so
    a handler reads its configuration instead of re-parsing it.
    """
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise _ConfigurationError(
            "max_files_invalid",
            f"max_files must be a positive whole number, not {raw!r}",
        )
    return raw


# ── Synchronization ────────────────────────────────────────────────────────────


async def _synchronize(
    settings: _Settings, ledger_path: Path, context: KnowledgeBaseRunContext
) -> KnowledgeBaseSyncResult:
    warnings: list[KnowledgeBaseIssue] = []
    try:
        previous = load_ledger(ledger_path)
    except LedgerError as error:
        # An unreadable ledger is not fatal: everything is republished, which is
        # correct but expensive, so the operator has to be told.
        _add_issue(warnings, "ledger_unreadable", str(error))
        previous = {}

    paths, truncated = await asyncio.to_thread(_discover, settings, warnings)
    if truncated:
        _add_issue(
            warnings,
            "max_files_reached",
            f"stopped after {settings.max_files} files; the rest of the folder "
            "stays unsynchronized until the bound is raised",
        )

    scanned, seen, carried = await asyncio.to_thread(
        _scan, settings, paths, previous, warnings
    )

    current: Ledger = dict(carried)
    created = updated = unchanged = 0
    published_bytes = 0

    removed = 0
    boundary = open_boundary(library_id=context.library_id, source_tag=SOURCE_TAG)
    try:
        for relative, path, content_hash, size_bytes in scanned:
            known = previous.get(relative)
            if known == content_hash:
                unchanged += 1
                current[relative] = content_hash
                continue
            try:
                # The hash is this source's version of the file. Fred stores it
                # and hands it back; it never reads it.
                await boundary.publish(
                    relative_path=relative, path=path, version=content_hash
                )
            except Exception as error:  # noqa: BLE001 - any failure is one file's
                # Deliberately absent from the ledger, so the next run retries
                # it; recording it would strand the document unpublished.
                _add_issue(warnings, "publish_failed", str(error), subject=relative)
                continue
            current[relative] = content_hash
            published_bytes += size_bytes
            if known is None:
                created += 1
            else:
                updated += 1

        if truncated:
            # A bounded run has not seen every file, so absence proves nothing:
            # carry the untouched entries forward instead of retracting them.
            for relative, content_hash in previous.items():
                current.setdefault(relative, content_hash)
        else:
            for relative in sorted(set(previous) - seen):
                try:
                    await boundary.retract(relative_path=relative)
                except Exception as error:  # noqa: BLE001 - one document's failure
                    # Kept in the ledger: it is still in the library, and a run
                    # that forgot it would never try to take it out again.
                    current[relative] = previous[relative]
                    _add_issue(warnings, "retract_failed", str(error), subject=relative)
                    continue
                removed += 1
    finally:
        await boundary.aclose()

    save_error: str | None = None
    try:
        save_ledger(ledger_path, current)
    except OSError as error:
        # The documents went out, but the next run would republish all of them:
        # a run whose ledger is lost has failed, whatever the counters say.
        save_error = str(error)

    summary = (
        f"{len(paths)} discovered, {created} created, {updated} updated, "
        f"{removed} removed, {unchanged} unchanged"
    )
    return KnowledgeBaseSyncResult(
        outcome=(
            KnowledgeBaseRunOutcome.failed
            if save_error
            else KnowledgeBaseRunOutcome.succeeded
        ),
        reconciliation_complete=not truncated,
        summary=f"{summary} — ledger not saved: {save_error}"
        if save_error
        else summary,
        discovered=len(paths),
        created=created,
        updated=updated,
        removed=removed,
        unchanged=unchanged,
        warnings=warnings,
        errors=(
            [KnowledgeBaseIssue(code="ledger_write_failed", message=save_error)]
            if save_error
            else []
        ),
        metrics={
            "root_path": str(settings.root),
            "glob": settings.glob,
            "published_bytes": published_bytes,
        },
    )


def _scan(
    settings: _Settings,
    paths: list[Path],
    previous: Ledger,
    warnings: list[KnowledgeBaseIssue],
) -> tuple[list[tuple[str, Path, str, int]], set[str], Ledger]:
    """Hash every discovered file, off the event loop.

    Returns what to consider publishing, every path that still exists — which is
    what deletion is judged against — and the entries of files that exist but
    could not be read, which must survive in the ledger untouched.
    """
    scanned: list[tuple[str, Path, str, int]] = []
    seen: set[str] = set()
    carried: Ledger = {}
    for path in paths:
        relative = path.relative_to(settings.root).as_posix()
        seen.add(relative)
        try:
            content_hash, size_bytes = _hash_file(path)
        except OSError as error:
            _add_issue(warnings, "read_failed", str(error), subject=relative)
            known = previous.get(relative)
            if known is not None:
                carried[relative] = known  # unread, but still there: not removed
            continue
        scanned.append((relative, path, content_hash, size_bytes))
    return scanned, seen, carried


def _discover(
    settings: _Settings, warnings: list[KnowledgeBaseIssue]
) -> tuple[list[Path], bool]:
    """Return the files to synchronize, and whether max_files cut the walk short.

    Sorted so a bounded run always picks the same files; a real implementation
    over a very large tree would stream the walk instead of materializing it.
    """
    try:
        candidates = sorted(settings.root.glob(settings.glob))
    except ValueError as error:
        raise _ConfigurationError(
            "glob_invalid", f"glob {settings.glob!r} is not a valid pattern: {error}"
        ) from error

    matches: list[Path] = []
    for path in candidates:
        relative = path.relative_to(settings.root)
        # A notes folder is often a git checkout or an editor's vault; its
        # dot-directories hold the tool's own state, not the team's documents.
        if any(part.startswith(".") for part in relative.parts):
            continue
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(settings.root):
            _add_issue(
                warnings,
                "path_escapes_root",
                "resolves outside the configured folder",
                subject=str(relative),
            )
            continue
        if settings.max_files is not None and len(matches) >= settings.max_files:
            return matches, True
        matches.append(path)
    return matches, False


def _hash_file(path: Path) -> tuple[str, int]:
    """Hash the file in chunks, so one large document cannot exhaust memory."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


# ── Issues ────────────────────────────────────────────────────────────────────


def _add_issue(
    issues: list[KnowledgeBaseIssue],
    code: str,
    message: str,
    subject: str | None = None,
) -> None:
    """Stop collecting at the SDK's bound; its `content_truncated` says so."""
    if len(issues) < MAX_ISSUES:
        issues.append(KnowledgeBaseIssue(code=code, message=message, subject=subject))


def _failed(code: str, message: str) -> KnowledgeBaseSyncResult:
    """A run that never read the source proves nothing about what it holds."""
    return KnowledgeBaseSyncResult(
        outcome=KnowledgeBaseRunOutcome.failed,
        reconciliation_complete=False,
        summary=message,
        errors=[KnowledgeBaseIssue(code=code, message=message)],
    )
