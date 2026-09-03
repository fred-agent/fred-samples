"""Progress-tracker application API.

Fred's gateway strips ``/app-services/<app_id>`` before proxying, so this
service sees ``/teams/<team_id>/...``. It is reached by two callers: the
application UI (through the host, which attaches the caller's bearer) and the
agent capability (server side, with this sample's shared key).

The gateway authorizes nothing. Every handler below therefore asks the Control
Plane whether the caller may use this application for this team, and fails
closed when that question cannot be answered.

Storage is SQLite: one file, no external service, real transactions. Every
statement lives in this module, so swapping it for the datastore your own
application already runs stays a small change.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any

import aiosqlite
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from auth_jwt import verify_bearer

# Everything below is driven by this one id, so a copy of this directory
# becomes a different application by changing it in one place.
APP_ID = os.environ.get("APP_ID", "progress-tracker")
DB_PATH = os.environ.get("PROGRESS_TRACKER_DB", "./progress-tracker.db")
# A pending pin is a short-lived statement of intent ("my next conversation is
# about this task"), so a stale click must not hijack a chat hours later.
PIN_TTL_SECONDS = 30 * 60
LIST_LIMIT = 50
CONTROL_PLANE = os.environ.get("CONTROL_PLANE_BASE", "")
# Base of the agent runtime, used only to read back conversation history.
RUNTIME_BASE = os.environ.get("RUNTIME_BASE", "")
# Server-to-server credential for the agent path, invented by this sample
# because Fred has no capability outbound auth yet. Not a platform contract --
# see README.md before copying it into a real application.
SERVICE_KEY = os.environ.get("PROGRESS_TRACKER_SERVICE_KEY", "")

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


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create the schema once, before the first request can read it."""

    async with aiosqlite.connect(DB_PATH) as db:
        # WAL lets the readers above run while a write is in flight; it is a
        # property of the database file, so setting it once here is enough.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(SCHEMA)
        await db.commit()
    yield


app = FastAPI(title=APP_ID, lifespan=lifespan)


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
    """Derived, never stored: only the capability sends a session id."""

    return "agent" if session_id else "ui"


class CreateTask(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class Note(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    # Sent by the capability, never by the UI -- which is exactly what makes it
    # usable as attribution: a write carrying a session came from an agent turn.
    session_id: str | None = Field(default=None, max_length=128)


class Decision(BaseModel):
    question: str = Field(min_length=1, max_length=400)
    answer: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=128)


class PinSession(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    # Sent only on the agent path, where the caller is the runtime rather than
    # a person: it comes from the runtime's own identity, never from the model,
    # so it cannot be steered by a prompt. Ignored when a user bearer is used.
    user_sub: str | None = Field(default=None, max_length=128)


async def require_entitled(
    team_id: str,
    authorization: Annotated[str | None, Header()] = None,
    x_service_key: Annotated[str | None, Header()] = None,
) -> str:
    """Ask the Control Plane the question the gateway does not.

    One call answers both halves, because grants are team to capability: a
    non-member is refused outright, and a member whose team was never granted
    this application sees it absent from the list.

    Two callers, two modes. A person's request carries their bearer and is
    checked as above. The agents pod carries a shared service key instead and
    is trusted on that alone -- no Control Plane call happens on that path, so
    it may not also present a bearer.
    """

    # The agent path presents a service key instead of a user bearer. Compared
    # with compare_digest so a wrong key cannot be recovered from timing.
    if x_service_key is not None:
        if not (SERVICE_KEY and secrets.compare_digest(x_service_key, SERVICE_KEY)):
            raise HTTPException(status_code=403, detail="bad_service_key")
        # The two modes are mutually exclusive on purpose: this path asks the
        # Control Plane nothing, so a bearer arriving beside the key has been
        # vetted by no one -- refusing the combination keeps every subject
        # this module hands out traceable to a token someone actually verified.
        if authorization:
            raise HTTPException(status_code=400, detail="service_key_with_bearer")
        return team_id

    # Proves the token is genuinely Keycloak's, unexpired and untampered,
    # before spending a network round trip on the separate question of
    # whether this team may use this application.
    verify_bearer(authorization)
    if not CONTROL_PLANE:
        raise HTTPException(status_code=403, detail="entitlement_check_unconfigured")

    url = f"{CONTROL_PLANE}/control-plane/v1/teams/{team_id}/applications"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers={"authorization": authorization})
    except httpx.HTTPError:
        # Fail closed: an unreachable Control Plane must never mean "allowed".
        raise HTTPException(status_code=403, detail="entitlement_check_unavailable")

    if response.status_code == 403:
        raise HTTPException(status_code=403, detail="not_a_team_member")
    if response.status_code != 200:
        raise HTTPException(status_code=403, detail="entitlement_check_failed")
    listed = any(item.get("id") == APP_ID for item in response.json().get("items", []))
    if not listed:
        raise HTTPException(status_code=403, detail="app_not_granted_to_team")
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


async def _task(
    db: aiosqlite.Connection, team_id: str, handle: str
) -> dict[str, Any]:
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
    return {"status": "ok"}


@app.get("/teams/{team_id}/tasks")
async def list_tasks(
    team_id: Entitled, session_id: str | None = Query(default=None)
) -> dict[str, Any]:
    """List the team's tasks, or just the one linked to a conversation."""

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


@app.get("/teams/{team_id}/tasks/{handle}")
async def get_task(team_id: Entitled, handle: str) -> dict[str, Any]:
    async with _db() as db:
        return await _task(db, team_id, handle)


@app.post("/teams/{team_id}/tasks/{handle}/sessions")
async def pin_session(
    team_id: Entitled, handle: str, body: PinSession
) -> dict[str, Any]:
    """Link a conversation to this task so later turns resolve it with no question."""

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


def _acting_sub(authorization: str | None, body: PinSession) -> str:
    """Who is this call acting as?

    A service-key call carries no user token to read a subject from, so the
    runtime supplies the acting user instead. A browser call always derives it
    from the verified bearer, so a person cannot claim someone else's pin by
    putting a subject in the body.
    """

    if authorization is None:
        if body.user_sub is None:
            raise HTTPException(status_code=401, detail="unreadable_subject")
        return body.user_sub
    return verify_bearer(authorization)


@app.post("/teams/{team_id}/tasks/{handle}/discuss")
async def discuss_next(
    team_id: Entitled,
    handle: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Mark this task as the subject of the caller's next conversation.

    One pending pin per user per team -- a newer click replaces the older one.
    The capability claims it on the first tool call of a session that has no
    task yet, which is what lets the user just start talking.
    """

    sub = verify_bearer(authorization)
    async with _db() as db:
        await _task(db, team_id, handle)  # 404s before pinning something unopenable
        await db.execute(
            "INSERT INTO pins (team_id, user_sub, handle, created_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (team_id, user_sub)"
            " DO UPDATE SET handle=excluded.handle, created_at=excluded.created_at",
            (team_id, sub, handle, _now()),
        )
        await db.commit()
    return {"handle": handle, "expires_in_seconds": PIN_TTL_SECONDS}


@app.post("/teams/{team_id}/discuss/claim")
async def claim_pending_pin(
    team_id: Entitled,
    body: PinSession,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Attach the caller's pending pin, if any, to this conversation.

    The whole claim runs in one write transaction, so two conversations
    starting at once cannot both take the same pin: the second waits, then
    finds it gone and reports none.
    """

    sub = _acting_sub(authorization, body)
    async with _db() as db:
        # IMMEDIATE takes the write lock up front rather than on the first
        # write, which is what makes the read below safe to act on.
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT handle, created_at FROM pins WHERE team_id=? AND user_sub=?",
            (team_id, sub),
        ) as cursor:
            pin = await cursor.fetchone()
        if pin is None:
            await db.rollback()
            return {"claimed": False}

        await db.execute(
            "DELETE FROM pins WHERE team_id=? AND user_sub=?", (team_id, sub)
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
    team_id: Entitled,
    body: PinSession,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Discard the caller's pending pin because the intent is already served.

    Resuming a conversation that is already linked satisfies the same intent a
    pin exists to carry. Left behind, that pin would be claimed by whatever
    unrelated conversation the user opened next, silently attaching it to this
    task.
    """

    sub = _acting_sub(authorization, body)
    async with _db() as db:
        cursor = await db.execute(
            "DELETE FROM pins WHERE team_id=? AND user_sub=?", (team_id, sub)
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


@app.post("/teams/{team_id}/tasks/{handle}/notes", status_code=201)
async def add_note(team_id: Entitled, handle: str, body: Note) -> dict[str, Any]:
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


@app.post("/teams/{team_id}/tasks/{handle}/decisions", status_code=201)
async def record_decision(
    team_id: Entitled, handle: str, body: Decision
) -> dict[str, Any]:
    """Checkpoint a decision. This is the record later agent turns rely on."""

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
