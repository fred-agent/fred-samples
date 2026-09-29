"""Corpus authorization and optimistic, task-local document-review state."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, NamedTuple
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException
from opensearchpy import AsyncOpenSearch
from opensearchpy import exceptions as os_exceptions
from pydantic import BaseModel, ConfigDict, Field

STAGES = ("analyst", "risk_reviewer", "action_planner", "summary")
# The holder heartbeats while it works, so this bounds how long an
# abandoned run stays unreclaimable, not how long a stage may take.
LEASE_SECONDS = 90
MAX_RUNS = 10
MAX_CONTENT_BYTES = 120_000
StageId = Literal["analyst", "risk_reviewer", "action_planner", "summary"]
Status = Literal["queued", "running", "interrupted", "completed", "failed", "cancelled"]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DocumentSelection(StrictModel):
    tag_id: str = Field(min_length=1, max_length=256)
    document_uid: str = Field(min_length=1, max_length=256)


class DocumentReference(DocumentSelection):
    document_name: str = Field(min_length=1, max_length=512)


class Evidence(StrictModel):
    location: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=1000)


class Finding(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    severity: Literal["info", "low", "medium", "high", "critical"]
    detail: str = Field(min_length=1, max_length=2000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)


class Action(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    priority: Literal["low", "medium", "high"]
    detail: str = Field(min_length=1, max_length=2000)


class StageResult(StrictModel):
    decision: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=4000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)
    findings: list[Finding] = Field(default_factory=list, max_length=32)
    actions: list[Action] = Field(default_factory=list, max_length=32)
    limitations: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=20
    )
    outcome: Literal["accepted", "needs_attention", "blocked"] | None = None


class ReviewStage(StrictModel):
    stage_id: StageId
    status: Status = "queued"
    started_at: str | None = None
    finished_at: str | None = None
    result: StageResult | None = None
    error: str | None = None


class ReviewEvent(StrictModel):
    at: str
    event: str
    stage_id: StageId | None = None
    reported_by: str


class ReviewRun(StrictModel):
    run_id: str
    claim_id: str
    source_hash: str
    status: Status = "running"
    started_at: str
    updated_at: str
    finished_at: str | None = None
    stages: list[ReviewStage]
    events: list[ReviewEvent] = Field(default_factory=list)
    reported_by: str


class ReviewState(StrictModel):
    runs: list[ReviewRun] = Field(default_factory=list)
    active_run_id: str | None = None


class Claim(StrictModel):
    claim_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
    mode: Literal["start", "resume", "restart"] = "start"
    run_id: str | None = Field(default=None, pattern=r"^run-[0-9a-f]{32}$")


class RunClaim(StrictModel):
    claim_id: str = Field(min_length=8, max_length=128)


class SaveStage(RunClaim):
    result: StageResult


class InterruptRun(RunClaim):
    error: str = Field(min_length=1, max_length=500)


class CancelRun(StrictModel):
    run_id: str = Field(pattern=r"^run-[0-9a-f]{32}$")


class ReviewContext(StrictModel):
    task_id: str
    title: str
    document: DocumentReference
    source_hash: str
    content: str
    run: ReviewRun


class Corpus:
    """Only the fixed Knowledge Flow service sees the transient user bearer."""

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        authorization: str,
        grant: dict[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.client = client
        self.authorization = authorization
        # Under delegation the bearer is this service's own, so the person it
        # acts for travels beside it or the receiver acts for nobody.
        self.grant = grant or {}
        self.folder_cache: dict[str, list[dict[str, str]]] = {}

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise HTTPException(503, "knowledge_flow_not_configured")
        if self.grant:
            # `params` wins over any query already on the URL, so the grant has
            # to travel with it rather than beside it.
            kwargs["params"] = {**(kwargs.get("params") or {}), **self.grant}
        try:
            async with (
                asyncio.timeout(30),
                self.client.stream(
                    method,
                    self.base_url + path,
                    headers={"authorization": self.authorization},
                    follow_redirects=False,
                    **kwargs,
                ) as response,
            ):
                if response.status_code == 401:
                    raise HTTPException(401, "knowledge_flow_user_token_expired")
                if response.status_code == 403:
                    raise HTTPException(403, "knowledge_flow_forbidden")
                if response.status_code == 404:
                    raise HTTPException(404, "source_document_unavailable")
                if not response.is_success:
                    raise HTTPException(502, "knowledge_flow_unavailable")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > 1_000_000:
                        raise HTTPException(413, "knowledge_flow_response_too_large")
                    chunks.extend(chunk)
                return json.loads(chunks)
        except (httpx.HTTPError, TimeoutError) as error:
            raise HTTPException(502, "knowledge_flow_unavailable") from error
        except (ValueError, UnicodeError) as error:
            raise HTTPException(502, "knowledge_flow_invalid_response") from error

    async def folders(self, team_id: str) -> list[dict[str, str]]:
        if team_id not in self.folder_cache:
            tags = await self.request(
                "GET",
                "/tags",
                params={
                    "type": "document",
                    "owner_filter": "team",
                    "team_id": team_id,
                    "limit": 1000,
                    "offset": 0,
                },
            )
            if not isinstance(tags, list):
                raise HTTPException(502, "knowledge_flow_invalid_response")
            self.folder_cache[team_id] = [
                {
                    "tag_id": tag["id"],
                    "path": "/".join(filter(None, [tag.get("path"), tag["name"]])),
                }
                for tag in tags
                if isinstance(tag, dict) and tag.get("id") and tag.get("name")
            ]
        return self.folder_cache[team_id]

    async def require_folder(self, team_id: str, tag_id: str) -> None:
        if tag_id not in {folder["tag_id"] for folder in await self.folders(team_id)}:
            raise HTTPException(404, "source_folder_unavailable")

    async def document(
        self, team_id: str, selection: DocumentSelection
    ) -> DocumentReference:
        await self.require_folder(team_id, selection.tag_id)
        metadata = await self.request(
            "GET", "/documents/metadata/" + quote(selection.document_uid, safe="")
        )
        identity = metadata.get("identity", {}) if isinstance(metadata, dict) else {}
        tags = metadata.get("tags", {}) if isinstance(metadata, dict) else {}
        if identity.get(
            "document_uid"
        ) != selection.document_uid or selection.tag_id not in tags.get("tag_ids", []):
            raise HTTPException(404, "source_document_unavailable")
        file = metadata.get("file") or {}
        file_type = str(file.get("file_type", "")).lower()
        mime_type = str(file.get("mime_type", "")).lower().split(";", 1)[0]
        name = str(identity.get("document_name") or "").lower()
        # KF's tabular markdown can contain only the first 200 rows. A bounded
        # preview must never be presented as a complete document review.
        if (
            file_type in {"csv", "xlsx", "xls", "tsv", "ods", "parquet"}
            or mime_type
            in {
                "text/csv",
                "text/tab-separated-values",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.oasis.opendocument.spreadsheet",
                "application/vnd.apache.parquet",
            }
            or name.endswith((".csv", ".xlsx", ".xls", ".tsv", ".ods", ".parquet"))
        ):
            raise HTTPException(422, "source_format_not_supported_for_complete_review")
        return DocumentReference(
            **selection.model_dump(include={"tag_id", "document_uid"}),
            document_name=identity.get("document_name") or selection.document_uid,
        )

    async def content(
        self, team_id: str, document: DocumentReference
    ) -> tuple[str, str]:
        await self.document(team_id, document)
        response = await self.request(
            "GET", "/markdown/" + quote(document.document_uid, safe="")
        )
        text = response.get("content") if isinstance(response, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(422, "source_document_not_processed")
        if len(text.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise HTTPException(413, "source_document_too_large")
        # KF signs embedded media links anew on each read. Keep their stable
        # target, but never persist or let temporary signatures alter the hash.
        text = re.sub(r"(https?://[^\s<>\[\]()\"']*)", _without_signed_query, text)
        return text, hashlib.sha256(text.encode()).hexdigest()

    async def documents(
        self, team_id: str, tag_id: str, offset: int, limit: int
    ) -> dict[str, Any]:
        await self.require_folder(team_id, tag_id)
        payload = await self.request(
            "POST",
            "/documents/metadata/browse",
            json={"tag_id": tag_id, "offset": offset, "limit": limit},
        )
        if not isinstance(payload, dict) or not isinstance(
            payload.get("documents"), list
        ):
            raise HTTPException(502, "knowledge_flow_invalid_response")
        gate = asyncio.Semaphore(8)

        async def readable(metadata: Any) -> dict[str, Any] | None:
            if not isinstance(metadata, dict):
                return None
            identity = metadata.get("identity", {})
            if not identity.get("document_uid"):
                return None
            async with gate:
                try:
                    doc = await self.document(
                        team_id,
                        DocumentSelection(
                            tag_id=tag_id, document_uid=identity["document_uid"]
                        ),
                    )
                except HTTPException as error:
                    if error.status_code in {403, 404}:
                        return None
                    if (
                        error.detail
                        == "source_format_not_supported_for_complete_review"
                    ):
                        return None
                    raise
                return doc.model_dump(exclude={"tag_id"})

        checked = await asyncio.gather(
            *(readable(metadata) for metadata in payload["documents"][:limit])
        )
        items = [item for item in checked if item is not None]
        # KF's total can include inaccessible records. Do not expose that count.
        return {"items": items, "offset": offset, "limit": limit, "total": None}


def _without_signed_query(match: re.Match[str]) -> str:
    url = match.group()
    if not re.search(
        r"[?&](?:X-Amz-|X-Goog-|AWSAccessKeyId=|Signature=)", url, re.IGNORECASE
    ):
        return url
    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith(("x-amz-", "x-goog-"))
        and key.lower() not in {"awsaccesskeyid", "signature", "expires"}
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def projected_state(state: ReviewState | None) -> ReviewState | None:
    if state is None:
        return None
    projected = state.model_copy(deep=True)
    for run in projected.runs:
        if is_stale(run):
            run.status = "interrupted"
            for stage in run.stages:
                if stage.status == "running":
                    stage.status = "interrupted"
                    stage.error = (
                        "Execution lease expired; resume from a fresh Fred turn."
                    )
    return projected


def touch_run(state: ReviewState, run_id: str, claim_id: str) -> ReviewRun:
    """Refresh the lease without recording an event.

    A long model call is silent, so its holder says it is still alive. Each
    heartbeat is not history: recording one would evict the run's real events,
    which are capped.
    """

    run = require_run(state, run_id, claim_id)
    run.updated_at = now()
    return run


def is_stale(run: ReviewRun) -> bool:
    return run.status == "running" and datetime.fromisoformat(
        run.updated_at
    ) + timedelta(seconds=LEASE_SECONDS) < datetime.now(UTC)


def event(
    run: ReviewRun, kind: str, user_id: str, stage_id: StageId | None = None
) -> None:
    run.updated_at = now()
    run.events.append(
        ReviewEvent(
            at=run.updated_at, event=kind, stage_id=stage_id, reported_by=user_id
        )
    )
    run.events = run.events[-100:]


def claim_run(
    state: ReviewState, body: Claim, source_hash: str, user_id: str
) -> ReviewRun:
    latest = state.runs[-1] if state.runs else None
    if latest and latest.claim_id == body.claim_id:
        if latest.source_hash != source_hash:
            raise HTTPException(409, "source_document_changed")
        return latest
    if latest and latest.status == "running" and not is_stale(latest):
        raise HTTPException(409, "review_already_running")
    if body.mode == "resume":
        if (
            latest is None
            or latest.run_id != body.run_id
            or latest.status in {"completed", "cancelled"}
        ):
            raise HTTPException(409, "review_not_resumable")
        if latest.source_hash != source_hash:
            raise HTTPException(409, "source_document_changed")
        latest.claim_id = body.claim_id
        latest.status = "running"
        latest.finished_at = None
        latest.reported_by = user_id
        for stage in latest.stages:
            if stage.status != "completed":
                stage.status = "queued"
                stage.started_at = stage.finished_at = stage.error = None
        event(latest, "resumed", user_id)
        state.active_run_id = latest.run_id
        return latest
    if latest and body.mode != "restart":
        raise HTTPException(409, "explicit_resume_or_restart_required")
    if latest and body.mode == "restart" and latest.run_id != body.run_id:
        raise HTTPException(409, "stale_review_run")
    if latest is None and body.mode != "start":
        raise HTTPException(409, "review_has_no_previous_run")
    if len(state.runs) >= MAX_RUNS:
        raise HTTPException(409, "review_history_limit_create_new_task")
    if latest and is_stale(latest):
        latest.status = "interrupted"
        latest.finished_at = now()
        event(latest, "lease_expired", user_id)
    run = ReviewRun(
        run_id="run-" + uuid.uuid4().hex,
        claim_id=body.claim_id,
        source_hash=source_hash,
        started_at=now(),
        updated_at=now(),
        reported_by=user_id,
        stages=[ReviewStage(stage_id=stage_id) for stage_id in STAGES],
    )
    event(run, "started", user_id)
    state.runs.append(run)
    state.active_run_id = run.run_id
    return run


def require_run(
    state: ReviewState, run_id: str, claim_id: str, *, allow_completed: bool = False
) -> ReviewRun:
    if not state.runs or state.runs[-1].run_id != run_id:
        raise HTTPException(409, "stale_review_run")
    run = state.runs[-1]
    if run.claim_id != claim_id:
        raise HTTPException(409, "stale_review_claim")
    if allow_completed and run.status == "completed":
        return run
    if run.status != "running" or is_stale(run):
        raise HTTPException(409, "review_interrupted_resume_required")
    return run


def stage_transition(
    run: ReviewRun, stage_id: StageId, result: StageResult | None, user_id: str
) -> None:
    stage = next(stage for stage in run.stages if stage.stage_id == stage_id)
    if stage.status == "completed":
        if result is not None and stage.result != result:
            raise HTTPException(409, "completed_stage_is_immutable")
        return
    if any(
        previous.status != "completed"
        for previous in run.stages[: STAGES.index(stage_id)]
    ):
        raise HTTPException(409, "review_stage_out_of_order")
    if result is None:
        if stage.status != "running":
            stage.status = "running"
            stage.started_at = now()
            event(run, "stage_started", user_id, stage_id)
        return
    if stage.status != "running":
        raise HTTPException(409, "review_stage_not_started")
    if stage_id == "summary" and result.outcome is None:
        raise HTTPException(422, "summary_outcome_required")
    if len(result.model_dump_json().encode()) > 100_000:
        raise HTTPException(413, "stage_result_too_large")
    stage.result = result
    stage.status = "completed"
    stage.finished_at = now()
    event(run, "stage_completed", user_id, stage_id)
    if stage_id == "summary":
        run.status = "completed"
        run.finished_at = now()
        event(run, "completed", user_id)


async def load_version(
    client: AsyncOpenSearch, index: str, task_id: str
) -> dict[str, Any]:
    try:
        return await client.get(index=index, id=task_id)
    except os_exceptions.NotFoundError as error:
        raise HTTPException(404, "task_not_found") from error
    except os_exceptions.OpenSearchException as error:
        raise HTTPException(503, "progress_store_unavailable") from error


async def save_version(
    client: AsyncOpenSearch,
    index: str,
    task_id: str,
    value: dict[str, Any],
    version: dict[str, Any],
) -> None:
    try:
        await client.index(
            index=index,
            id=task_id,
            body=value,
            params={
                "if_seq_no": version["_seq_no"],
                "if_primary_term": version["_primary_term"],
                "refresh": "wait_for",
            },
        )
    except os_exceptions.ConflictError as error:
        raise HTTPException(409, "concurrent_review_update_retry") from error
    except os_exceptions.OpenSearchException as error:
        raise HTTPException(503, "progress_store_unavailable") from error


class RunCounts(NamedTuple):
    severities: Counter[str]
    outcomes: Counter[str]
    priorities: Counter[str]
    stage_seconds: dict[str, float]


def run_counts(run: ReviewRun) -> RunCounts:
    """Count one run's saved decisions. The single definition of what counts."""

    counts = RunCounts(Counter(), Counter(), Counter(), {})
    for stage in run.stages:
        if stage.status != "completed" or stage.result is None:
            continue
        # Every specialist reports findings and actions; the summary restates
        # them, so counting it too would double what the review actually found.
        if stage.stage_id != "summary":
            counts.severities.update(f.severity for f in stage.result.findings)
            counts.priorities.update(a.priority for a in stage.result.actions)
        elif stage.result.outcome:
            counts.outcomes[stage.result.outcome] += 1
        if stage.started_at and stage.finished_at:
            counts.stage_seconds[stage.stage_id] = max(
                0.0,
                (
                    datetime.fromisoformat(stage.finished_at)
                    - datetime.fromisoformat(stage.started_at)
                ).total_seconds(),
            )
    return counts


def find_run(state: ReviewState | None, run_id: str) -> ReviewRun:
    """Read any run in history, which is what a reader clicking one asks for.

    `require_run` deliberately refuses anything but the latest run under a live
    claim; reading an earlier one carries no claim and changes nothing.
    """

    for run in state.runs if state else []:
        if run.run_id == run_id:
            return run
    raise HTTPException(404, "review_run_not_found")


def run_statistics(run: ReviewRun) -> dict[str, Any]:
    """One run's own numbers, scoped and labelled so they cannot read as global."""

    counts = run_counts(run)
    return {
        "scope": {
            "run_id": run.run_id,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "completed_stages": sum(s.status == "completed" for s in run.stages),
            "expected_stages": len(STAGES),
            "label": "Single review run; saved decisions only",
        },
        "outcome": next(iter(counts.outcomes), None),
        "severity_counts": dict(counts.severities),
        "action_priority_counts": dict(counts.priorities),
    }


def statistics(tasks: list[Any], truncated: bool, limit: int) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    severities: Counter[str] = Counter()
    dates: Counter[str] = Counter()
    durations: dict[str, list[float]] = {stage_id: [] for stage_id in STAGES}
    for task in tasks:
        state = projected_state(task.review)
        run = state.runs[-1] if state and state.runs else None
        statuses[run.status if run else "queued"] += 1
        if run is None:
            continue
        counts = run_counts(run)
        severities.update(counts.severities)
        outcomes.update(counts.outcomes)
        for stage_id, seconds in counts.stage_seconds.items():
            durations[stage_id].append(seconds)
        if run.status == "completed" and run.finished_at:
            dates[run.finished_at[:10]] += 1
    return {
        "scope": {
            "limit": limit,
            "tasks_included": len(tasks),
            "truncated": truncated,
            "label": "Latest accessible tasks; latest run per task",
        },
        "task_status_counts": dict(statuses),
        "outcome_counts": dict(outcomes),
        "severity_counts": dict(severities),
        "stage_durations": [
            {
                "stage_id": stage_id,
                "completed_count": len(values),
                "total_seconds": round(sum(values), 3),
                "average_seconds": round(sum(values) / len(values), 3) if values else 0,
            }
            for stage_id, values in durations.items()
        ],
        "completion_timeline": [
            {"date": day, "count": count} for day, count in sorted(dates.items())
        ],
    }
