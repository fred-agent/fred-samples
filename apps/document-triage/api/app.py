"""Document-triage application API.

Fred's gateway strips ``/app-services/<app_id>`` before proxying, so this
service sees ``/teams/<team_id>/...``. The gateway authorizes nothing, so every
handler asks the Control Plane whether the caller may use this application for
this team, and fails closed when that question cannot be answered.

**There is no service credential anywhere in this application.** Every outbound
call carries the caller's own bearer, forwarded verbatim, and Knowledge Flow
authorizes it per user. That is possible because of one design choice: the
agent never writes. Agents may read team-shared files but may only *mutate*
inside their own subtree (FILES-04, "agents never share"), so an application
whose agents wrote shared state would need a credential of its own -- and would
be routing around a platform boundary rather than working with it. Here the
agent proposes in chat and the human commits, so every write is a user write.

Two independent gates apply, and both are needed:

- the Control Plane says whether this *team* may use this *application*;
- Knowledge Flow says whether this *user* may touch that *path or document*.

Neither implies the other.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

# Everything below is driven by this one id, so a copy of this directory
# becomes a different application by changing it in one place.
APP_ID = os.environ.get("APP_ID", "document-triage")
CONTROL_PLANE = os.environ.get("CONTROL_PLANE_BASE", "")
# Knowledge Flow, e.g. http://knowledge-flow:8111/knowledge-flow/v1
KNOWLEDGE_FLOW = os.environ.get("KNOWLEDGE_FLOW_BASE", "")
TIMEOUT = 30.0
BROWSE_LIMIT = 200

MARKS = ("unreviewed", "reviewed", "needs_work")

app = FastAPI(title=APP_ID)


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


async def require_entitled(
    team_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Ask the Control Plane the question the gateway does not.

    One call answers both halves, because grants are team to capability: a
    non-member is refused outright, and a member whose team was never granted
    this application sees it absent from the list.
    """

    if not authorization:
        raise HTTPException(status_code=401, detail="missing_bearer")
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


# --------------------------------------------------------------------------- #
# Knowledge Flow, called as the user.
#
# Every helper takes the caller's Authorization header and forwards it
# unchanged. None of them has a credential of its own, which is what makes the
# per-user authorization Knowledge Flow already performs the real gate.
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


async def _write_mark(
    team_id: str, record: dict[str, Any], authorization: str
) -> None:
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
    # person recognises is the two joined, which is also what the capability's
    # `resolve_folder` matches against.
    items = []
    for tag in payload:
        if not isinstance(tag, dict):
            continue
        tag_id, name = tag.get("id"), tag.get("name")
        if not tag_id or not name:
            continue
        parent = tag.get("path")
        items.append(
            {"tag_id": tag_id, "path": f"{parent}/{name}" if parent else name}
        )
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
