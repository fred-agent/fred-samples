"""Document-triage application API.

Fred's gateway strips ``/app-services/<app_id>`` before proxying, so this
service sees ``/teams/<team_id>/...``. The gateway authorizes nothing: this is
a first-party backend that validates every caller's bearer locally and checks
the team's ``app:document-triage`` grant directly in OpenFGA through its own
process-lifetime ``RebacSdk``.

Two independent gates apply, and both are needed:

- ReBAC says whether this *user* may use this *application* for this *team*;
- Knowledge Flow says whether this *user* may touch that *path or document*.

Neither implies the other.

Every Knowledge Flow call carries the caller's own bearer, forwarded verbatim.
The backend's M2M and OpenFGA credentials are process credentials: they never
stand in for a human and are never mounted into an agent pod. That works
because the agent never writes -- agents may read team-shared files but may
only mutate inside their own subtree ("agents never share"), so the
agent proposes in chat and the person commits under their own identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any, cast
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
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
from fred_core.security.rebac.rebac_sdk import RebacSdk, rebac_sdk_factory
from pydantic import BaseModel, Field

# Everything below is driven by this one id, so a copy of this directory
# becomes a different application by changing it in one place.
APP_ID = os.environ.get("APP_ID", "document-triage")
# Knowledge Flow, e.g. http://knowledge-flow:8111/knowledge-flow/v1
KNOWLEDGE_FLOW = os.environ.get("KNOWLEDGE_FLOW_BASE", "")
M2M_SECRET_ENV = "DOCUMENT_TRIAGE_M2M_CLIENT_SECRET"  # pragma: allowlist secret
DELEGATION_ENV = "FRED_DELEGATION"
OPENFGA_TOKEN_ENV = "DOCUMENT_TRIAGE_OPENFGA_API_TOKEN"  # pragma: allowlist secret
TIMEOUT = 30.0
BROWSE_LIMIT = 200

MARKS = ("unreviewed", "reviewed", "needs_work")


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
    """Fail startup unless the process-lifetime ReBAC reader is ready."""

    sdk = await rebac_sdk_factory(_security_configuration(), kpi_writer=KPI)
    try:
        app.state.rebac_sdk = sdk
        yield
    finally:
        await sdk.close()


app = FastAPI(title=APP_ID, lifespan=lifespan)
register_exception_handlers(app)


def _rebac_sdk(request: Request) -> RebacSdk:
    return cast(RebacSdk, request.app.state.rebac_sdk)


CurrentUser = Annotated[KeycloakUser, Depends(get_current_user_without_gcu)]
Rebac = Annotated[RebacSdk, Depends(_rebac_sdk)]


async def require_entitled(
    team_id: str,
    user: CurrentUser,
    rebac: Rebac,
) -> str:
    """Require team membership and the typed ``app:document-triage`` grant.

    One call answers both halves: a non-member is refused, and so is a member
    whose team was never granted this application.
    """

    await rebac.check_application_access(user, team_id=team_id, app_id=APP_ID)
    return team_id


Entitled = Annotated[str, Depends(require_entitled)]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Mark(BaseModel):
    # Carried in the body rather than the URL: a document uid is an internal
    # working identifier, and the slug is derived from it, so the server can
    # check the client did not invent one.
    document_uid: str = Field(min_length=1, max_length=256)
    document_name: str = Field(min_length=1, max_length=512)
    mark: str = Field(pattern="^(unreviewed|reviewed|needs_work)$")
    note: str = Field(default="", max_length=4000)
    # Set by the UI when the user commits something that came out of a
    # conversation. The agent never sets this -- it is the human's statement
    # about where their decision came from, not the model's claim about itself.
    from_session: str | None = Field(default=None, max_length=128)


# --------------------------------------------------------------------------- #
# Knowledge Flow, called as the user.
#
# Every helper takes the caller's Authorization header and forwards it
# unchanged. None of them uses this backend's own credentials, which is what
# makes the per-user authorization Knowledge Flow already performs the real
# gate on documents and workspace paths.
# --------------------------------------------------------------------------- #


def _kf_ready() -> None:
    if not KNOWLEDGE_FLOW:
        raise HTTPException(status_code=503, detail="knowledge_flow_not_configured")


async def _kf(
    method: str,
    path: str,
    authorization: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
) -> httpx.Response:
    _kf_ready()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            response = await client.request(
                method,
                f"{KNOWLEDGE_FLOW}{path}",
                headers={"authorization": authorization},
                json=json_body,
                params=params,
                files=files,
            )
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="knowledge_flow_unavailable")
    if response.status_code in (401, 403):
        # Knowledge Flow refused this user for this path or document. Pass that
        # through rather than dressing it up as a server error.
        raise HTTPException(status_code=403, detail="knowledge_flow_forbidden")
    return response


def _fs_path(verb: str, path: str) -> str:
    """Percent-encode reserved characters while preserving "/" separators."""

    return f"/fs/{verb}/{quote(path.lstrip('/'), safe='/')}"


def _board_dir(team_id: str) -> str:
    return f"teams/{team_id}/shared/triage"


def _slug(document_uid: str, document_name: str) -> str:
    """A stable, readable file name for one document's triage record.

    The document uid is an internal working identifier and is deliberately not
    the file name; it is carried *inside* the record instead. The short digest
    keeps two documents with the same display name apart.
    """

    base = re.sub(r"[^a-z0-9]+", "-", document_name.lower()).strip("-")[:60]
    digest = hashlib.sha1(document_uid.encode()).hexdigest()[:8]
    return f"{base or 'document'}-{digest}"


def _blank(document_uid: str, document_name: str) -> dict[str, Any]:
    return {
        "slug": _slug(document_uid, document_name),
        "document_uid": document_uid,
        "document_name": document_name,
        "mark": "unreviewed",
        "note": "",
        "from_session": None,
        "updated_at": None,
    }


async def _read_marks(team_id: str, authorization: str) -> dict[str, dict[str, Any]]:
    """Load every triage record the team has written, keyed by slug.

    A board that has never been triaged has no directory at all, which Knowledge
    Flow reports as 404 -- an empty board, not an error.
    """

    listing = await _kf(
        "GET", "/fs/list", authorization, params={"path": _board_dir(team_id)}
    )
    if listing.status_code == 404:
        return {}
    listing.raise_for_status()
    entries = listing.json()
    if not isinstance(entries, list):
        return {}

    marks: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # Listing entries carry `path`, `size` and `type`. The path may come
        # back absolute or relative depending on the deployment, so address the
        # file by its basename under the directory we asked for.
        raw_path = str(entry.get("path") or "")
        if entry.get("type") == "directory" or not raw_path.endswith(".json"):
            continue
        name = raw_path.rsplit("/", 1)[-1]
        blob = await _kf(
            "GET", _fs_path("download", f"{_board_dir(team_id)}/{name}"), authorization
        )
        if blob.status_code != 200:
            continue
        try:
            record = json.loads(blob.content)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(record, dict) and isinstance(record.get("slug"), str):
            marks[record["slug"]] = record
    return marks


async def _write_mark(team_id: str, record: dict[str, Any], authorization: str) -> None:
    name = f"{record['slug']}.json"
    target = f"{_board_dir(team_id)}/{name}"
    body = json.dumps(record, indent=2, sort_keys=True).encode()
    response = await _kf(
        "POST",
        _fs_path("upload", target),
        authorization,
        # The destination is the URL path; `file` is the only form field the
        # upload route declares.
        files={"file": (name, body, "application/json")},
    )
    response.raise_for_status()


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Readiness endpoint; startup already proved the ReBAC reader."""

    return {"status": "ok"}


@app.get("/teams/{team_id}/folders")
async def list_folders(
    team_id: Entitled,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """The document folders this user can see, so the UI can offer a choice."""

    response = await _kf(
        "GET",
        "/tags",
        authorization or "",
        # team_id is not optional beside owner_filter=team: Knowledge Flow
        # raises MissingTeamIdError and answers 400 without it.
        params={"type": "document", "owner_filter": "team", "team_id": team_id},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return {"items": []}

    # A tag carries `id`, `name` and an optional parent `path`; the path a
    # person recognises is the two joined.
    items = []
    for tag in payload:
        if not isinstance(tag, dict):
            continue
        tag_id, name = tag.get("id"), tag.get("name")
        if not tag_id or not name:
            continue
        parent = tag.get("path")
        items.append({"tag_id": tag_id, "path": f"{parent}/{name}" if parent else name})
    return {"items": items}


@app.get("/teams/{team_id}/board")
async def board(
    team_id: Entitled,
    tag_id: str = Query(min_length=1),
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """The documents in one folder, joined with whatever the team has marked.

    The folder is the source of truth for *what exists*; the workspace records
    are the source of truth for *what was decided*. A document with no record
    yet simply reads as unreviewed, so nothing has to be seeded up front.
    """

    auth = authorization or ""
    documents = await _kf(
        "POST",
        "/documents/metadata/browse",
        auth,
        json_body={"tag_id": tag_id, "offset": 0, "limit": BROWSE_LIMIT},
    )
    documents.raise_for_status()
    payload = documents.json()
    raw = payload.get("documents", []) if isinstance(payload, dict) else []

    marks = await _read_marks(team_id, auth)
    items = []
    for document in raw:
        identity = document.get("identity", {}) if isinstance(document, dict) else {}
        uid = identity.get("document_uid")
        if not uid:
            continue
        name = str(identity.get("document_name") or uid)
        entry = _blank(str(uid), name)
        stored = marks.get(entry["slug"])
        if stored:
            entry.update(
                {
                    "mark": stored.get("mark", "unreviewed"),
                    "note": stored.get("note", ""),
                    "from_session": stored.get("from_session"),
                    "updated_at": stored.get("updated_at"),
                }
            )
        items.append(entry)

    counts = {mark: sum(1 for i in items if i["mark"] == mark) for mark in MARKS}
    return {"items": items, "counts": counts, "total": len(items)}


@app.post("/teams/{team_id}/board/{slug}/mark")
async def set_mark(
    team_id: Entitled,
    slug: str,
    body: Mark,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Commit one triage decision.

    This write is made with the *caller's* token, by a person who looked at the
    document. That is the whole trust story: no agent asserted this, and no
    service credential stood in for a human.
    """

    auth = authorization or ""
    if _slug(body.document_uid, body.document_name) != slug:
        # The slug is derived, so a mismatch means the client made it up.
        raise HTTPException(status_code=400, detail="slug_does_not_match_document")

    record = _blank(body.document_uid, body.document_name)
    record.update(
        {
            "mark": body.mark,
            "note": body.note,
            "from_session": body.from_session,
            "updated_at": _now(),
        }
    )
    await _write_mark(team_id, record, auth)
    return record
