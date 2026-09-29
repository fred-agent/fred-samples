"""Progress-tracker application API.

Fred's gateway strips ``/app-services/<app_id>`` before proxying, so this
service sees ``/teams/<team_id>/...``. The application UI reaches it through
the host, which attaches the signed-in user's bearer. Fred's agent runtime
reaches ``/mcp`` directly: on an interactive turn it sends that same user's
bearer, and under a delegated grant a workload holding the delegation caller
role presents its own bearer and names the person the call acts for. Every
entry point resolves to a person and runs the same validation and the same
authorization checks.

The gateway authorizes nothing. This is a first-party backend: it validates
every caller's bearer locally, then checks team membership and the
``app:progress-tracker`` grant through its own process-lifetime ``RebacSdk``
before any team route runs, and fails closed when OpenFGA cannot answer.

Storage is SQLite: one file, no external service, real transactions. Every
statement lives in this module, so swapping it for the datastore your own
application already runs stays a small change.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any, cast

import aiosqlite
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi_mcp import AuthConfig
from fred_core import (
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
from pydantic import BaseModel, Field

# Everything below is driven by this one id, so a copy of this directory
# becomes a different application by changing it in one place. The SDK checks
# `app:<APP_ID>`; `app__<APP_ID>` remains the catalog/admin id.
APP_ID = os.environ.get("APP_ID", "progress-tracker")
DB_PATH = os.environ.get("PROGRESS_TRACKER_DB", "./progress-tracker.db")
# Agents reach this backend through the cataloged MCP server, and a team holds
# that as a capability separately from the application itself: entitlement to
# open the page does not entitle an agent to write the record.
MCP_SERVER_ID = os.environ.get("MCP_SERVER_ID", "mcp-progress-tracker")
# Only routes carrying this tag become tools. Tagging is the whole allowlist:
# an untagged route stays reachable by the UI and invisible to an agent.
MCP_TOOL_TAG = "ProgressTracker"
# A pending pin is a short-lived statement of intent ("my next conversation is
# about this task"), so a stale click must not hijack a chat hours later.
PIN_TTL_SECONDS = 30 * 60
LIST_LIMIT = 50
# Base of the agent runtime, used only to read back conversation history.
RUNTIME_BASE = os.environ.get("RUNTIME_BASE", "")
M2M_SECRET_ENV = "PROGRESS_TRACKER_M2M_CLIENT_SECRET"  # pragma: allowlist secret
DELEGATION_ENV = "FRED_DELEGATION"
OPENFGA_TOKEN_ENV = "PROGRESS_TRACKER_OPENFGA_API_TOKEN"  # pragma: allowlist secret

# A task is one row plus three child tables. The primary key on
# (team_id, handle) is what makes a duplicate handle an error the writer sees,
# rather than a second row that silently shadows the first on every read.
SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    team_id    TEXT NOT NULL,
    handle     TEXT NOT NULL,
    title      TEXT NOT NULL,
    stage      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (team_id, handle)
);
CREATE TABLE IF NOT EXISTS sessions (
    team_id    TEXT NOT NULL,
    handle     TEXT NOT NULL,
    session_id TEXT NOT NULL,
    PRIMARY KEY (team_id, handle, session_id)
);
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id    TEXT NOT NULL,
    handle     TEXT NOT NULL,
    at         TEXT NOT NULL,
    text       TEXT NOT NULL,
    session_id TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id    TEXT NOT NULL,
    handle     TEXT NOT NULL,
    at         TEXT NOT NULL,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    session_id TEXT
);
CREATE TABLE IF NOT EXISTS pins (
    team_id    TEXT NOT NULL,
    user_sub   TEXT NOT NULL,
    handle     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (team_id, user_sub)
);
CREATE INDEX IF NOT EXISTS notes_by_task     ON notes     (team_id, handle, id);
CREATE INDEX IF NOT EXISTS decisions_by_task ON decisions  (team_id, handle, id);
CREATE INDEX IF NOT EXISTS sessions_by_id    ON sessions   (team_id, session_id);
"""


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Fail startup unless ReBAC is ready and the schema exists."""

    sdk = await rebac_sdk_factory(_security_configuration(), kpi_writer=KPI)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # WAL lets the readers above run while a write is in flight; it is a
            # property of the database file, so setting it once here is enough.
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(SCHEMA)
            await db.commit()
        app.state.rebac_sdk = sdk
        yield
    finally:
        await sdk.close()


app = FastAPI(
    title=APP_ID,
    lifespan=lifespan,
    dependencies=[Depends(declare_delegation_parameters)],
)
register_exception_handlers(app)


@asynccontextmanager
async def _db() -> AsyncIterator[aiosqlite.Connection]:
    """One connection per request, so every handler gets its own transaction."""

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Wait rather than fail when another request holds the write lock.
        await db.execute("PRAGMA busy_timeout=5000")
        yield db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _source(session_id: str | None) -> str:
    """Derived, never stored: a session id marks a write made in an agent turn."""

    return "agent" if session_id else "ui"


class CreateTask(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class Note(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    # Set on an agent turn and never by the UI, which is exactly what makes it
    # usable as attribution: a write carrying a session came from an agent.
    session_id: str | None = Field(default=None, max_length=128)


class Decision(BaseModel):
    question: str = Field(min_length=1, max_length=400)
    answer: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=128)


class PinSession(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)


def _rebac_sdk(request: Request) -> RebacSdk:
    return cast(RebacSdk, request.app.state.rebac_sdk)


CurrentUser = Annotated[KeycloakUser, Depends(get_current_user_without_gcu)]
Rebac = Annotated[RebacSdk, Depends(_rebac_sdk)]


async def require_entitled(
    team_id: str,
    user: CurrentUser,
    rebac: Rebac,
) -> str:
    """Require team membership and the typed ``app:progress-tracker`` grant.

    One call answers both halves: a non-member is refused, and so is a member
    whose team was never granted this application.
    """

    await rebac.check_application_access(user, team_id=team_id, app_id=APP_ID)
    return team_id


Entitled = Annotated[str, Depends(require_entitled)]


async def _children(
    db: aiosqlite.Connection, team_id: str, handles: list[str]
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Fetch sessions, notes and decisions for a set of tasks, grouped by handle.

    Three queries whatever the page size, so listing stays one round trip per
    child table rather than one per task.
    """

    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        handle: {"session_ids": [], "notes": [], "decisions": []} for handle in handles
    }
    if not handles:
        return grouped
    marks = ",".join("?" * len(handles))
    args = [team_id, *handles]

    async with db.execute(
        f"SELECT handle, session_id FROM sessions WHERE team_id=? AND handle IN ({marks})",
        args,
    ) as cursor:
        for row in await cursor.fetchall():
            grouped[row["handle"]]["session_ids"].append(row["session_id"])

    async with db.execute(
        f"SELECT handle, at, text, session_id FROM notes"
        f" WHERE team_id=? AND handle IN ({marks}) ORDER BY id",
        args,
    ) as cursor:
        for row in await cursor.fetchall():
            grouped[row["handle"]]["notes"].append(
                {
                    "at": row["at"],
                    "text": row["text"],
                    "session_id": row["session_id"],
                    "source": _source(row["session_id"]),
                }
            )

    async with db.execute(
        f"SELECT handle, at, question, answer, session_id FROM decisions"
        f" WHERE team_id=? AND handle IN ({marks}) ORDER BY id",
        args,
    ) as cursor:
        for row in await cursor.fetchall():
            grouped[row["handle"]]["decisions"].append(
                {
                    "at": row["at"],
                    "question": row["question"],
                    "answer": row["answer"],
                    "session_id": row["session_id"],
                    "source": _source(row["session_id"]),
                }
            )
    return grouped


async def _assemble(
    db: aiosqlite.Connection, team_id: str, rows: list[aiosqlite.Row]
) -> list[dict[str, Any]]:
    handles = [row["handle"] for row in rows]
    children = await _children(db, team_id, handles)
    return [{**dict(row), **children[row["handle"]]} for row in rows]


async def _task(db: aiosqlite.Connection, team_id: str, handle: str) -> dict[str, Any]:
    async with db.execute(
        "SELECT handle, title, stage, created_at, updated_at FROM tasks"
        " WHERE team_id=? AND handle=?",
        (team_id, handle),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="task_not_found")
    return (await _assemble(db, team_id, [row]))[0]


async def _touch(db: aiosqlite.Connection, team_id: str, handle: str) -> None:
    await db.execute(
        "UPDATE tasks SET updated_at=? WHERE team_id=? AND handle=?",
        (_now(), team_id, handle),
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Readiness endpoint; startup already proved ReBAC and the schema."""

    return {"status": "ok"}


@app.get(
    "/teams/{team_id}/tasks",
    tags=[MCP_TOOL_TAG],
    operation_id="list_tasks",
)
async def list_tasks(
    team_id: Entitled, session_id: str | None = Query(default=None)
) -> dict[str, Any]:
    """List the team's tasks, or resolve the one this conversation is about.

    Passing the conversation identifier is how a later turn finds its task
    again without asking the user to repeat it.
    """

    async with _db() as db:
        if session_id:
            query = (
                "SELECT t.handle, t.title, t.stage, t.created_at, t.updated_at"
                " FROM tasks t JOIN sessions s"
                " ON s.team_id=t.team_id AND s.handle=t.handle"
                " WHERE t.team_id=? AND s.session_id=?"
                " ORDER BY t.created_at DESC LIMIT ?"
            )
            args: tuple[Any, ...] = (team_id, session_id, LIST_LIMIT)
        else:
            query = (
                "SELECT handle, title, stage, created_at, updated_at FROM tasks"
                " WHERE team_id=? ORDER BY created_at DESC LIMIT ?"
            )
            args = (team_id, LIST_LIMIT)
        async with db.execute(query, args) as cursor:
            rows = await cursor.fetchall()
        return {"items": await _assemble(db, team_id, list(rows))}


@app.post("/teams/{team_id}/tasks", status_code=201)
async def create_task(team_id: Entitled, body: CreateTask) -> dict[str, Any]:
    """The user's starting point: record the task, before any agent sees it."""

    now = _now()
    async with _db() as db:
        # Short and speakable, so a user can name it in chat without an id --
        # which means collisions are possible, and the primary key turns one
        # into a retry here instead of a second task shadowing the first.
        for _ in range(5):
            handle = f"TASK-{uuid.uuid4().hex[:4].upper()}"
            try:
                await db.execute(
                    "INSERT INTO tasks (team_id, handle, title, stage,"
                    " created_at, updated_at) VALUES (?, ?, ?, 'open', ?, ?)",
                    (team_id, handle, body.title, now, now),
                )
            except sqlite3.IntegrityError:
                continue
            await db.commit()
            return await _task(db, team_id, handle)
    raise HTTPException(status_code=500, detail="could_not_allocate_handle")


@app.get(
    "/teams/{team_id}/tasks/{handle}",
    tags=[MCP_TOOL_TAG],
    operation_id="read_task",
)
async def get_task(team_id: Entitled, handle: str) -> dict[str, Any]:
    """Read one task by its handle, with its notes, decisions and conversations."""
    async with _db() as db:
        return await _task(db, team_id, handle)


@app.post(
    "/teams/{team_id}/tasks/{handle}/sessions",
    tags=[MCP_TOOL_TAG],
    operation_id="link_conversation_to_task",
)
async def pin_session(
    team_id: Entitled, handle: str, body: PinSession, rebac: Rebac
) -> dict[str, Any]:
    """Link a conversation to this task so later turns resolve it with no question."""

    await rebac.check_team_capability(team_id, MCP_SERVER_ID)
    async with _db() as db:
        await _task(db, team_id, handle)  # 404s before linking to nothing
        await db.execute(
            "INSERT OR IGNORE INTO sessions (team_id, handle, session_id)"
            " VALUES (?, ?, ?)",
            (team_id, handle, body.session_id),
        )
        await _touch(db, team_id, handle)
        await db.commit()
        return await _task(db, team_id, handle)


@app.post("/teams/{team_id}/tasks/{handle}/discuss")
async def discuss_next(
    team_id: Entitled, handle: str, user: CurrentUser
) -> dict[str, Any]:
    """Mark this task as the subject of the caller's next conversation.

    One pending pin per user per team -- a newer click replaces the older one.
    It is claimed on the first tool call of a session that has no task yet,
    which is what lets the user just start talking.
    """

    async with _db() as db:
        await _task(db, team_id, handle)  # 404s before pinning something unopenable
        await db.execute(
            "INSERT INTO pins (team_id, user_sub, handle, created_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (team_id, user_sub)"
            " DO UPDATE SET handle=excluded.handle, created_at=excluded.created_at",
            (team_id, user.uid, handle, _now()),
        )
        await db.commit()
    return {"handle": handle, "expires_in_seconds": PIN_TTL_SECONDS}


@app.post(
    "/teams/{team_id}/discuss/claim",
    tags=[MCP_TOOL_TAG],
    operation_id="claim_pinned_task",
)
async def claim_pending_pin(
    team_id: Entitled, body: PinSession, user: CurrentUser, rebac: Rebac
) -> dict[str, Any]:
    """Take up the task the user asked to discuss, and attach it to this conversation.

    Call this first in a conversation that has no task yet. The pin belongs to
    the calling user, so there is nothing to identify beyond the conversation.

    The whole claim runs in one write transaction, so two conversations
    starting at once cannot both take the same pin: the second waits, then
    finds it gone and reports none.
    """

    await rebac.check_team_capability(team_id, MCP_SERVER_ID)
    async with _db() as db:
        # IMMEDIATE takes the write lock up front rather than on the first
        # write, which is what makes the read below safe to act on.
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT handle, created_at FROM pins WHERE team_id=? AND user_sub=?",
            (team_id, user.uid),
        ) as cursor:
            pin = await cursor.fetchone()
        if pin is None:
            await db.rollback()
            return {"claimed": False}

        await db.execute(
            "DELETE FROM pins WHERE team_id=? AND user_sub=?", (team_id, user.uid)
        )
        if _age_seconds(pin["created_at"]) > PIN_TTL_SECONDS:
            await db.commit()
            return {"claimed": False}

        await db.execute(
            "INSERT OR IGNORE INTO sessions (team_id, handle, session_id)"
            " VALUES (?, ?, ?)",
            (team_id, pin["handle"], body.session_id),
        )
        await _touch(db, team_id, pin["handle"])
        await db.commit()
        return {"claimed": True, "task": await _task(db, team_id, pin["handle"])}


def _age_seconds(stamp: str | None) -> float:
    if not stamp:
        return float("inf")
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - then).total_seconds()


@app.delete("/teams/{team_id}/discuss")
async def drop_pending_pin(
    team_id: Entitled, body: PinSession, user: CurrentUser
) -> dict[str, Any]:
    """Discard the caller's pending pin because the intent is already served.

    Resuming a conversation that is already linked satisfies the same intent a
    pin exists to carry. Left behind, that pin would be claimed by whatever
    unrelated conversation the user opened next, silently attaching it to this
    task.
    """

    async with _db() as db:
        cursor = await db.execute(
            "DELETE FROM pins WHERE team_id=? AND user_sub=?", (team_id, user.uid)
        )
        await db.commit()
        return {"dropped": cursor.rowcount > 0}


@app.get("/teams/{team_id}/tasks/{handle}/conversations")
async def task_conversations(
    team_id: Entitled,
    handle: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Read back the conversations pinned to this task.

    The runtime returns only rows belonging to the authenticated user and an
    empty list for anyone else's session, so this is per-viewer by construction:
    forwarding the caller's own bearer is what keeps a teammate's transcript
    unreadable here.
    """

    async with _db() as db:
        task = await _task(db, team_id, handle)
    if not RUNTIME_BASE:
        return {"items": [], "unavailable": "runtime_not_configured"}

    items: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for session_id in task["session_ids"]:
            url = f"{RUNTIME_BASE}/agents/sessions/{session_id}/messages"
            try:
                response = await client.get(
                    url, headers={"authorization": authorization or ""}
                )
            except httpx.HTTPError:
                continue
            if response.status_code != 200:
                continue
            items.append(
                {
                    "session_id": session_id,
                    "messages": _readable(response.json()),
                }
            )
    return {"items": items}


def _readable(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep what a person would recognise as the conversation.

    Plans, thoughts, tool calls and their results are dropped: this view exists
    to show what was said, not to replay the agent's working.
    """

    out = []
    for message in messages:
        if message.get("role") not in ("user", "assistant"):
            continue
        if message.get("channel") != "final":
            continue
        text = "\n".join(
            part.get("text", "")
            for part in message.get("parts", [])
            if part.get("type") == "text"
        ).strip()
        if not text:
            continue
        out.append(
            {
                "role": message["role"],
                "at": message.get("timestamp"),
                "text": text,
            }
        )
    return out


@app.post(
    "/teams/{team_id}/tasks/{handle}/notes",
    status_code=201,
    tags=[MCP_TOOL_TAG],
    operation_id="record_task_note",
)
async def add_note(
    team_id: Entitled, handle: str, body: Note, rebac: Rebac
) -> dict[str, Any]:
    """Append an observation to the task's timeline."""

    await rebac.check_team_capability(team_id, MCP_SERVER_ID)
    async with _db() as db:
        await _task(db, team_id, handle)
        await db.execute(
            "INSERT INTO notes (team_id, handle, at, text, session_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (team_id, handle, _now(), body.text, body.session_id),
        )
        await _touch(db, team_id, handle)
        await db.commit()
        return await _task(db, team_id, handle)


@app.post(
    "/teams/{team_id}/tasks/{handle}/decisions",
    status_code=201,
    tags=[MCP_TOOL_TAG],
    operation_id="record_task_decision",
)
async def record_decision(
    team_id: Entitled, handle: str, body: Decision, rebac: Rebac
) -> dict[str, Any]:
    """Checkpoint a decision. This is the record later agent turns rely on."""

    await rebac.check_team_capability(team_id, MCP_SERVER_ID)
    async with _db() as db:
        await _task(db, team_id, handle)
        await db.execute(
            "INSERT INTO decisions (team_id, handle, at, question, answer, session_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (team_id, handle, _now(), body.question, body.answer, body.session_id),
        )
        await db.execute(
            "UPDATE tasks SET stage='decided', updated_at=? WHERE team_id=? AND handle=?",
            (_now(), team_id, handle),
        )
        await db.commit()
        return await _task(db, team_id, handle)


# Built after the routes: the tool list is snapshotted from the OpenAPI schema
# at construction. Forwarding the authorization header is what makes an agent's
# call run the same bearer validation and the same grants as the UI's.
mcp = strip_grant_tool_fields(
    DelegatedFastApiMCP(
        app,
        name="Progress Tracker",
        description=(
            "Resolve the task a user asked to discuss, then record the notes and "
            "decisions that conversation produces."
        ),
        include_tags=[MCP_TOOL_TAG],
        auth_config=AuthConfig(dependencies=[Depends(mcp_mount_auth)]),
        headers=["authorization"],
    )
)
mcp.mount_http(mount_path="/mcp")
