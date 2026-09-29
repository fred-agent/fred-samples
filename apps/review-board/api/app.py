"""Review-board application API and bearer-authenticated MCP server.

The browser reaches the REST routes through Fred's application gateway. Fred's
agent runtime reaches ``/mcp`` directly. Its catalog entry uses
``auth_mode: delegated``: an interactive turn sends the same user's bearer, and
a delegated run sends a workload bearer holding the delegation caller role
plus a grant naming the person the call acts for. Every path resolves to a
person and runs the same local JWT validation and the same direct OpenFGA checks.

There is deliberately no shared agent service key and no custom Python
``AgentCapability`` package. The backend's M2M, OpenFGA and OpenSearch secrets
remain backend-only process credentials; none is copied into the agents pod.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi_mcp import AuthConfig
from fred_core import (
    AssertedUser,
    KeycloakUser,
    RamLogStore,
    SecurityConfiguration,
    get_current_user_without_gcu,
    log_setup,
    security_configuration_from_env,
)
from fred_core.common import register_exception_handlers
from fred_core.kpi import KPIDefaults, KpiLogStore, KPIWriter
from fred_core.security.mcp_delegation import (
    declare_delegation_parameters,
    mcp_mount_auth,
    strip_grant_tool_fields,
)
from fred_core.security.mcp_delegation_fastapi import DelegatedFastApiMCP
from fred_core.security.rebac.rebac_sdk import RebacSdk, rebac_sdk_factory
from opensearchpy import AsyncOpenSearch
from opensearchpy import exceptions as os_exceptions
from pydantic import BaseModel, ConfigDict, Field
from review import (
    CancelRun,
    Claim,
    Corpus,
    DocumentReference,
    DocumentSelection,
    InterruptRun,
    ReviewContext,
    ReviewRun,
    ReviewState,
    RunClaim,
    SaveStage,
    StageId,
    claim_run,
    event,
    find_run,
    load_version,
    projected_state,
    require_run,
    run_statistics,
    save_version,
    stage_transition,
    statistics,
    touch_run,
)

APP_ID = os.environ.get("APP_ID", "review-board")
MCP_SERVER_ID = os.environ.get("MCP_SERVER_ID", "mcp-review-board")
TASKS_INDEX = os.environ.get("REVIEW_BOARD_TASKS_INDEX", "review-board-tasks-v1")
STATES_INDEX = os.environ.get("REVIEW_BOARD_STATES_INDEX", "review-board-states-v1")
M2M_SECRET_ENV = "REVIEW_BOARD_M2M_CLIENT_SECRET"  # pragma: allowlist secret
DELEGATION_ENV = "FRED_DELEGATION"
OPENFGA_TOKEN_ENV = "REVIEW_BOARD_OPENFGA_API_TOKEN"  # pragma: allowlist secret
LIST_LIMIT = 100
KNOWLEDGE_FLOW = os.environ.get("KNOWLEDGE_FLOW_BASE", "")

TEAM_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
TASK_ID_PATTERN = r"^task-[0-9a-f]{32}$"
AGENT_LABEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"

TeamId = Annotated[
    str,
    Path(min_length=1, max_length=128, pattern=TEAM_ID_PATTERN),
]
TaskId = Annotated[str, Path(pattern=TASK_ID_PATTERN)]
RUN_ID_PATTERN = r"^run-[0-9a-f]{32}$"
RunId = Annotated[str, Path(pattern=RUN_ID_PATTERN)]
AgentLabel = Annotated[
    str,
    Path(min_length=1, max_length=64, pattern=AGENT_LABEL_PATTERN),
]


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


def _security_configuration() -> SecurityConfiguration:
    """The hardened profile, assembled by the shared library.

    Only the two variable names that are this application's own are supplied;
    every shared rule lives in one place rather than a copy per application.
    """

    return security_configuration_from_env(
        m2m_secret_env=M2M_SECRET_ENV,
        openfga_token_env=OPENFGA_TOKEN_ENV,
        delegation_env=DELEGATION_ENV,
    )


def _opensearch_client() -> AsyncOpenSearch:
    """Create an app-scoped client; credentials are optional only as a pair."""

    username = os.environ.get("REVIEW_BOARD_OPENSEARCH_USERNAME", "").strip()
    password = os.environ.get("REVIEW_BOARD_OPENSEARCH_PASSWORD", "").strip()
    if bool(username) != bool(password):
        raise RuntimeError(
            "REVIEW_BOARD_OPENSEARCH_USERNAME and "
            "REVIEW_BOARD_OPENSEARCH_PASSWORD must be set together"
        )

    options: dict[str, Any] = {
        "hosts": [_required_env("OPENSEARCH_URL")],
        "verify_certs": _env_bool("OPENSEARCH_VERIFY_CERTS", True),
    }
    if username:
        options["http_auth"] = (username, password)
    ca_certs = os.environ.get("OPENSEARCH_CA_CERTS", "").strip()
    if ca_certs:
        options["ca_certs"] = ca_certs
    if not options["verify_certs"]:
        options["ssl_show_warn"] = False
    return AsyncOpenSearch(**options)


# Without this the shared library logs to handlers that were never installed,
# so every audit event it emits — including each delegation decision — is
# dropped instead of recorded.
log_setup(
    service_name=f"{APP_ID}-api",
    log_level=os.environ.get("LOG_LEVEL", "INFO"),
    store=RamLogStore(),
)

KPI = KPIWriter(
    KpiLogStore("info"),
    KPIDefaults(source=f"{APP_ID}-api", static_dims={"service": APP_ID}),
)


TASKS_MAPPING: dict[str, Any] = {
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "team_id": {"type": "keyword"},
            "task_id": {"type": "keyword"},
            "title": {"type": "text", "index": False},
            "created_by": {"type": "keyword"},
            "created_at": {"type": "date"},
            "document": {"type": "object", "enabled": False},
            "review": {"type": "object", "enabled": False},
        },
    }
}

STATES_MAPPING: dict[str, Any] = {
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "team_id": {"type": "keyword"},
            "task_id": {"type": "keyword"},
            "agent_label": {"type": "keyword"},
            "status": {"type": "keyword"},
            "progress_percent": {"type": "short"},
            "detail": {"type": "text", "index": False},
            "reported_by": {"type": "keyword"},
            "updated_at": {"type": "date"},
        },
    }
}


async def _ensure_indices(client: AsyncOpenSearch) -> None:
    for index, mapping in (
        (TASKS_INDEX, TASKS_MAPPING),
        (STATES_INDEX, STATES_MAPPING),
    ):
        if not await client.indices.exists(index=index):
            await client.indices.create(index=index, body=mapping)
        elif index == TASKS_INDEX:
            await client.indices.put_mapping(
                index=index,
                body={
                    "properties": {
                        "document": TASKS_MAPPING["mappings"]["properties"]["document"],
                        "review": TASKS_MAPPING["mappings"]["properties"]["review"],
                    }
                },
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Fail startup unless ReBAC and the application datastore are ready."""

    sdk = await rebac_sdk_factory(_security_configuration(), kpi_writer=KPI)
    client: AsyncOpenSearch | None = None
    try:
        client = _opensearch_client()
        await _ensure_indices(client)
        app.state.rebac_sdk = sdk
        app.state.opensearch = client
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=False
        ) as knowledge_client:
            app.state.knowledge_client = knowledge_client
            yield
    finally:
        if client is not None:
            await client.close()
        await sdk.close()


app = FastAPI(
    title=APP_ID,
    lifespan=lifespan,
    dependencies=[Depends(declare_delegation_parameters)],
)
register_exception_handlers(app)


def _rebac_sdk(request: Request) -> RebacSdk:
    return cast(RebacSdk, request.app.state.rebac_sdk)


def _search_client(request: Request) -> AsyncOpenSearch:
    return cast(AsyncOpenSearch, request.app.state.opensearch)


CurrentUser = Annotated[KeycloakUser, Depends(get_current_user_without_gcu)]
Rebac = Annotated[RebacSdk, Depends(_rebac_sdk)]
Search = Annotated[AsyncOpenSearch, Depends(_search_client)]


def _corpus(
    request: Request,
    principal: CurrentUser,
    authorization: Annotated[str | None, Header()] = None,
) -> Corpus:
    grant = (
        {
            "person": principal.uid,
            "run": principal.run_id,
            "agent": principal.agent_id,
        }
        if isinstance(principal, AssertedUser)
        else None
    )
    return Corpus(
        KNOWLEDGE_FLOW,
        getattr(request.app.state, "knowledge_client", None),
        authorization or "",
        grant,
    )


Knowledge = Annotated[Corpus, Depends(_corpus)]


async def require_entitled(
    team_id: TeamId,
    user: CurrentUser,
    rebac: Rebac,
) -> str:
    """Require team membership and the typed ``app:review-board`` grant."""

    await rebac.check_application_access(user, team_id=team_id, app_id=APP_ID)
    return team_id


Entitled = Annotated[str, Depends(require_entitled)]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateTask(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    document: DocumentSelection | None = None


class TaskSummary(StrictModel):
    task_id: str
    title: str
    created_by: str
    created_at: str
    document: DocumentReference | None = None
    status: str = "queued"
    progress_percent: int = 0
    run_id: str | None = None


class TaskList(StrictModel):
    items: list[TaskSummary]
    limit: int = LIST_LIMIT
    truncated: bool = False
    statistics: dict[str, Any]


ProgressStatus = Literal["queued", "running", "blocked", "completed", "failed"]


class ProgressUpdate(StrictModel):
    status: ProgressStatus
    progress_percent: int = Field(ge=0, le=100)
    detail: str = Field(default="", max_length=1000)


class ProgressState(StrictModel):
    agent_label: str
    status: ProgressStatus
    progress_percent: int
    detail: str
    updated_at: str


class TaskDetail(TaskSummary):
    progress: list[ProgressState]
    review: ReviewState | None = None


class Health(StrictModel):
    status: Literal["ok"] = "ok"


class StoredTask(StrictModel):
    team_id: str
    task_id: str
    title: str
    created_by: str
    created_at: str
    document: DocumentReference | None = None
    review: ReviewState | None = None


class StoredProgress(ProgressState):
    team_id: str
    task_id: str
    reported_by: str


def _task_summary(task: StoredTask) -> TaskSummary:
    state = projected_state(task.review)
    run = state.runs[-1] if state and state.runs else None
    return TaskSummary(
        task_id=task.task_id,
        title=task.title,
        created_by=task.created_by,
        created_at=task.created_at,
        document=task.document,
        status=run.status if run else "queued",
        progress_percent=25 * sum(stage.status == "completed" for stage in run.stages)
        if run
        else 0,
        run_id=run.run_id if run else None,
    )


def _progress_state(progress: StoredProgress) -> ProgressState:
    return ProgressState(
        agent_label=progress.agent_label,
        status=progress.status,
        progress_percent=progress.progress_percent,
        detail=progress.detail,
        updated_at=progress.updated_at,
    )


def _storage_unavailable() -> HTTPException:
    # OpenSearch exceptions can contain node addresses and request details. The
    # public response deliberately reveals neither.
    return HTTPException(status_code=503, detail="progress_store_unavailable")


async def _load_task(client: AsyncOpenSearch, task_id: str) -> StoredTask:
    try:
        result = await client.get(index=TASKS_INDEX, id=task_id)
    except os_exceptions.NotFoundError:
        raise HTTPException(status_code=404, detail="task_not_found")
    except os_exceptions.OpenSearchException as error:
        raise _storage_unavailable() from error
    return StoredTask.model_validate(result["_source"])


async def _progress_for(
    client: AsyncOpenSearch,
    *,
    team_id: str,
    task_id: str,
) -> list[ProgressState]:
    try:
        result = await client.search(
            index=STATES_INDEX,
            body={
                "size": LIST_LIMIT,
                "sort": [{"agent_label": {"order": "asc"}}],
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"team_id": team_id}},
                            {"term": {"task_id": task_id}},
                        ]
                    }
                },
            },
        )
    except os_exceptions.OpenSearchException as error:
        raise _storage_unavailable() from error
    return [
        _progress_state(StoredProgress.model_validate(hit["_source"]))
        for hit in result["hits"]["hits"]
    ]


async def _task_detail(
    client: AsyncOpenSearch,
    *,
    team_id: str,
    task_id: str,
    corpus: Corpus,
) -> TaskDetail:
    task = await _load_task(client, task_id)
    if task.team_id != team_id:
        # A task id never changes the team selected by Fred or authorized above.
        raise HTTPException(status_code=404, detail="task_not_found")
    if task.document:
        await corpus.document(team_id, task.document)
    return TaskDetail(
        **_task_summary(task).model_dump(),
        progress=[]
        if task.review
        else await _progress_for(client, team_id=team_id, task_id=task_id),
        review=projected_state(task.review),
    )


def _progress_doc_id(team_id: str, task_id: str, agent_label: str) -> str:
    material = f"{team_id}\0{task_id}\0{agent_label}".encode()
    return hashlib.sha256(material).hexdigest()


@app.get("/healthz", response_model=Health)
async def healthz() -> Health:
    """Readiness endpoint; startup already proved ReBAC and OpenSearch."""

    return Health()


@app.post(
    "/teams/{team_id}/tasks",
    response_model=TaskSummary,
    status_code=201,
    operation_id="create_progress_task",
)
async def create_task(
    body: CreateTask,
    team_id: Entitled,
    user: CurrentUser,
    client: Search,
    corpus: Knowledge,
) -> TaskSummary:
    """Create an opaque task id that can safely be handed to several agents."""

    document = await corpus.document(team_id, body.document) if body.document else None
    task = StoredTask(
        team_id=team_id,
        task_id=f"task-{uuid.uuid4().hex}",
        title=body.title,
        created_by=user.uid,
        created_at=_now(),
        document=document,
        review=ReviewState() if document else None,
    )
    try:
        await client.index(
            index=TASKS_INDEX,
            id=task.task_id,
            body=task.model_dump(),
            params={"refresh": "wait_for", "op_type": "create"},
        )
    except os_exceptions.OpenSearchException as error:
        raise _storage_unavailable() from error
    return _task_summary(task)


@app.get(
    "/teams/{team_id}/tasks",
    response_model=TaskList,
    operation_id="list_progress_tasks",
)
async def list_tasks(team_id: Entitled, client: Search, corpus: Knowledge) -> TaskList:
    """List the team's accessible tasks and the dashboard aggregates over them.

    Both come from one pass of access checks, which costs at most one Knowledge
    Flow folder lookup however many tasks are listed.
    """

    tasks, truncated = await _accessible_tasks(client, corpus, team_id)
    return TaskList(
        items=[_task_summary(task) for task in tasks],
        truncated=truncated,
        statistics=statistics(
            [task for task in tasks if task.review is not None], truncated, LIST_LIMIT
        ),
    )


async def _accessible_tasks(
    client: AsyncOpenSearch, corpus: Corpus, team_id: str
) -> tuple[list[StoredTask], bool]:
    try:
        result = await client.search(
            index=TASKS_INDEX,
            body={
                "size": LIST_LIMIT + 1,
                "sort": [{"created_at": {"order": "desc"}}],
                "query": {"bool": {"filter": [{"term": {"team_id": team_id}}]}},
            },
        )
    except os_exceptions.OpenSearchException as error:
        raise _storage_unavailable() from error
    hits = result["hits"]["hits"]
    candidates = []
    for hit in hits[:LIST_LIMIT]:
        task = StoredTask.model_validate(hit["_source"])
        if task.team_id == team_id:
            candidates.append(task)
    documents = {
        (task.document.tag_id, task.document.document_uid): task.document
        for task in candidates
        if task.document is not None
    }
    if documents:
        await corpus.folders(team_id)
    gate = asyncio.Semaphore(8)

    async def readable(document: DocumentReference) -> bool:
        async with gate:
            try:
                await corpus.document(team_id, document)
            except HTTPException as error:
                if error.status_code in {403, 404}:
                    return False
                raise
            return True

    checks = await asyncio.gather(
        *(readable(document) for document in documents.values())
    )
    allowed = {
        key for key, permitted in zip(documents, checks, strict=True) if permitted
    }
    tasks = [
        task
        for task in candidates
        if task.document is None
        or (task.document.tag_id, task.document.document_uid) in allowed
    ]
    return tasks, len(hits) > LIST_LIMIT


@app.get(
    "/teams/{team_id}/tasks/{task_id}",
    response_model=TaskDetail,
    tags=["AgentProgress"],
    operation_id="read_task_progress",
)
async def read_task_progress(
    team_id: Entitled,
    task_id: TaskId,
    client: Search,
    corpus: Knowledge,
) -> TaskDetail:
    """Read one task and the latest claimed state for every agent label."""

    return await _task_detail(client, team_id=team_id, task_id=task_id, corpus=corpus)


@app.put(
    "/teams/{team_id}/tasks/{task_id}/reporters/{agent_label}",
    response_model=ProgressState,
    tags=["AgentProgress"],
    operation_id="report_task_progress",
)
async def report_task_progress(
    body: ProgressUpdate,
    team_id: Entitled,
    task_id: TaskId,
    agent_label: AgentLabel,
    user: CurrentUser,
    rebac: Rebac,
    client: Search,
) -> ProgressState:
    """Record this agent label's latest progress for an existing task.

    ``team_id`` is a declared tool argument so Fred's context-aware MCP wrapper
    replaces any model value with the active collaborative team. ``agent_label``
    is only display attribution: the user bearer proves the human, not which
    agent instance among the team's agents made the call.
    """

    # The application grant admits the page/backend. This independent grant is
    # what lets the team admin decide whether agents may activate the MCP tool.
    await rebac.check_team_capability(team_id, MCP_SERVER_ID)
    task = await _load_task(client, task_id)
    if task.team_id != team_id:
        raise HTTPException(status_code=404, detail="task_not_found")
    if task.document or task.review is not None:
        raise HTTPException(409, "document_review_requires_ordered_stage_tools")

    state = StoredProgress(
        team_id=team_id,
        task_id=task_id,
        agent_label=agent_label,
        status=body.status,
        progress_percent=body.progress_percent,
        detail=body.detail,
        reported_by=user.uid,
        updated_at=_now(),
    )
    try:
        await client.index(
            index=STATES_INDEX,
            id=_progress_doc_id(team_id, task_id, agent_label),
            body=state.model_dump(),
            params={"refresh": "wait_for"},
        )
    except os_exceptions.OpenSearchException as error:
        raise _storage_unavailable() from error
    return _progress_state(state)


@app.get("/teams/{team_id}/folders")
async def corpus_folders(team_id: Entitled, corpus: Knowledge) -> dict[str, Any]:
    return {"items": await corpus.folders(team_id)}


@app.get("/teams/{team_id}/documents")
async def corpus_documents(
    team_id: Entitled,
    corpus: Knowledge,
    tag_id: str = Query(min_length=1, max_length=256),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    return await corpus.documents(team_id, tag_id, offset, limit)


@app.get("/teams/{team_id}/tasks/{task_id}/review/{run_id}/statistics")
async def review_run_statistics(
    team_id: Entitled,
    task_id: TaskId,
    run_id: RunId,
    client: Search,
    corpus: Knowledge,
) -> dict[str, Any]:
    """Report one run's own numbers, for a reader who selected that run.

    Deliberately untagged: the dashboard reads this, and every tag'd route
    becomes a tool the model can call, which this need not be.
    """

    detail = await _task_detail(client, team_id=team_id, task_id=task_id, corpus=corpus)
    return run_statistics(find_run(detail.review, run_id))


async def _review_task(
    client: AsyncOpenSearch, corpus: Corpus, team_id: str, task_id: str
) -> tuple[StoredTask, dict[str, Any]]:
    version = await load_version(client, TASKS_INDEX, task_id)
    task = StoredTask.model_validate(version["_source"])
    if task.team_id != team_id:
        raise HTTPException(404, "task_not_found")
    if task.document is None or task.review is None:
        raise HTTPException(409, "task_has_no_corpus_document")
    await corpus.document(team_id, task.document)
    return task, version


async def _write_review(
    client: AsyncOpenSearch, task: StoredTask, version: dict[str, Any]
) -> None:
    if task.review and task.review.runs and task.review.runs[-1].status != "running":
        task.review.active_run_id = None
    await save_version(client, TASKS_INDEX, task.task_id, task.model_dump(), version)


def _review_detail(task: StoredTask) -> TaskDetail:
    return TaskDetail(
        **_task_summary(task).model_dump(),
        progress=[],
        review=projected_state(task.review),
    )


@app.post(
    "/teams/{team_id}/tasks/{task_id}/review/claim",
    response_model=TaskDetail,
    tags=["AgentProgress"],
    operation_id="claim_document_review",
)
async def claim_document_review(
    body: Claim,
    team_id: Entitled,
    task_id: TaskId,
    user: CurrentUser,
    rebac: Rebac,
    client: Search,
    corpus: Knowledge,
) -> TaskDetail:
    """Claim a fresh review, or explicitly resume/restart its latest run."""
    await rebac.check_team_capability(team_id, MCP_SERVER_ID)
    task, version = await _review_task(client, corpus, team_id, task_id)
    assert task.document is not None and task.review is not None
    _, source_hash = await corpus.content(team_id, task.document)
    claim_run(task.review, body, source_hash, user.uid)
    await _write_review(client, task, version)
    return _review_detail(task)


@app.get(
    "/teams/{team_id}/tasks/{task_id}/review/{run_id}/context",
    response_model=ReviewContext,
    tags=["AgentProgress"],
    operation_id="read_document_review_context",
)
async def read_document_review_context(
    team_id: Entitled,
    task_id: TaskId,
    run_id: str,
    client: Search,
    corpus: Knowledge,
    rebac: Rebac,
    claim_id: str = Query(min_length=8, max_length=128),
) -> ReviewContext:
    """Read the fixed document and previously persisted specialist decisions."""
    await rebac.check_team_capability(team_id, MCP_SERVER_ID)
    task, _ = await _review_task(client, corpus, team_id, task_id)
    assert task.document is not None and task.review is not None
    run = require_run(task.review, run_id, claim_id, allow_completed=True)
    content, source_hash = await corpus.content(team_id, task.document)
    if source_hash != run.source_hash:
        raise HTTPException(409, "source_document_changed")
    return ReviewContext(
        task_id=task.task_id,
        title=task.title,
        document=task.document,
        source_hash=source_hash,
        content=content,
        run=run,
    )


async def _update_stage(
    team_id: str,
    task_id: str,
    run_id: str,
    stage_id: StageId,
    body: RunClaim | SaveStage,
    user: KeycloakUser,
    rebac: RebacSdk,
    client: AsyncOpenSearch,
    corpus: Corpus,
) -> ReviewRun:
    await rebac.check_team_capability(team_id, MCP_SERVER_ID)
    task, version = await _review_task(client, corpus, team_id, task_id)
    assert task.document is not None and task.review is not None
    run = require_run(task.review, run_id, body.claim_id, allow_completed=True)
    _, source_hash = await corpus.content(team_id, task.document)
    if source_hash != run.source_hash:
        raise HTTPException(409, "source_document_changed")
    stage_transition(
        run, stage_id, body.result if isinstance(body, SaveStage) else None, user.uid
    )
    await _write_review(client, task, version)
    return run


@app.post(
    "/teams/{team_id}/tasks/{task_id}/review/{run_id}/stages/{stage_id}/start",
    response_model=ReviewRun,
    tags=["AgentProgress"],
    operation_id="start_document_review_stage",
)
async def start_document_review_stage(
    body: RunClaim,
    team_id: Entitled,
    task_id: TaskId,
    run_id: str,
    stage_id: StageId,
    user: CurrentUser,
    rebac: Rebac,
    client: Search,
    corpus: Knowledge,
) -> ReviewRun:
    """Start only the next fixed specialist; a repeated start is idempotent."""
    return await _update_stage(
        team_id, task_id, run_id, stage_id, body, user, rebac, client, corpus
    )


@app.post(
    "/teams/{team_id}/tasks/{task_id}/review/{run_id}/stages/{stage_id}/result",
    response_model=ReviewRun,
    tags=["AgentProgress"],
    operation_id="save_document_review_stage",
)
async def save_document_review_stage(
    body: SaveStage,
    team_id: Entitled,
    task_id: TaskId,
    run_id: str,
    stage_id: StageId,
    user: CurrentUser,
    rebac: Rebac,
    client: Search,
    corpus: Knowledge,
) -> ReviewRun:
    """Persist a structured decision once; summary completion closes the run."""
    return await _update_stage(
        team_id, task_id, run_id, stage_id, body, user, rebac, client, corpus
    )


@app.post(
    "/teams/{team_id}/tasks/{task_id}/review/{run_id}/heartbeat",
    response_model=ReviewRun,
    tags=["AgentProgress"],
    operation_id="heartbeat_document_review",
)
async def heartbeat_document_review(
    body: RunClaim,
    team_id: Entitled,
    task_id: TaskId,
    run_id: str,
    user: CurrentUser,
    rebac: Rebac,
    client: Search,
    corpus: Knowledge,
) -> ReviewRun:
    """Say the holder is still working, so a silent stage reads as alive.

    Without this the lease has to outlast the slowest model call, which makes
    an abandoned run unreclaimable for just as long.
    """

    del user
    await rebac.check_team_capability(team_id, MCP_SERVER_ID)
    task, version = await _review_task(client, corpus, team_id, task_id)
    assert task.review is not None
    run = touch_run(task.review, run_id, body.claim_id)
    await _write_review(client, task, version)
    return run


@app.post(
    "/teams/{team_id}/tasks/{task_id}/review/{run_id}/interrupt",
    response_model=ReviewRun,
    tags=["AgentProgress"],
    operation_id="interrupt_document_review",
)
async def interrupt_document_review(
    body: InterruptRun,
    team_id: Entitled,
    task_id: TaskId,
    run_id: str,
    user: CurrentUser,
    rebac: Rebac,
    client: Search,
    corpus: Knowledge,
) -> ReviewRun:
    """Persist a safe failure marker; the next interactive turn can resume."""
    await rebac.check_team_capability(team_id, MCP_SERVER_ID)
    task, version = await _review_task(client, corpus, team_id, task_id)
    assert task.review is not None
    run = task.review.runs[-1] if task.review.runs else None
    if (
        run
        and run.run_id == run_id
        and run.claim_id == body.claim_id
        and run.status == "failed"
    ):
        return run
    run = require_run(task.review, run_id, body.claim_id)
    run.status = "failed"
    run.finished_at = _now()
    for stage in run.stages:
        if stage.status == "running":
            stage.status = "failed"
            stage.error = body.error
            stage.finished_at = _now()
    event(run, "failed", user.uid)
    await _write_review(client, task, version)
    return run


@app.post("/teams/{team_id}/tasks/{task_id}/cancel", response_model=TaskDetail)
async def cancel_document_review(
    body: CancelRun,
    team_id: Entitled,
    task_id: TaskId,
    user: CurrentUser,
    client: Search,
    corpus: Knowledge,
) -> TaskDetail:
    task, version = await _review_task(client, corpus, team_id, task_id)
    assert task.review is not None
    run = task.review.runs[-1] if task.review.runs else None
    if run is None or run.run_id != body.run_id:
        raise HTTPException(409, "stale_review_run")
    if run.status == "cancelled":
        return _review_detail(task)
    if run.status == "completed":
        raise HTTPException(409, "completed_review_is_immutable")
    run.status = "cancelled"
    run.finished_at = _now()
    for stage in run.stages:
        if stage.status != "completed":
            stage.status = "cancelled"
    event(run, "cancelled", user.uid)
    await _write_review(client, task, version)
    return _review_detail(task)


# Build this after the tagged routes exist: fastapi-mcp snapshots OpenAPI at
# construction time. Its header allowlist forwards the incoming MCP bearer into
# the ordinary FastAPI route, so route auth and ReBAC are identical to REST.
mcp = strip_grant_tool_fields(
    DelegatedFastApiMCP(
        app,
        name="Review Board MCP",
        description="Read team tasks and persist ordered corpus-review decisions and progress.",
        include_tags=["AgentProgress"],
        auth_config=AuthConfig(dependencies=[Depends(mcp_mount_auth)]),
        headers=["authorization"],
    )
)
mcp.mount_http(mount_path="/mcp")
