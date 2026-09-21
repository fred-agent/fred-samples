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
A real WebDAV server, in this process, with no socket and no Apache.

Every test below drives the actual client against actual multistatus XML, so
the awkward cases are tested for real rather than asserted about a mock: a
share that keeps no entity tags, one that answers 404 for a single property,
one that points outside itself, one that contains itself.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

import httpx
import pytest

from fred_samples_webdav_kb.webdav import Address, WebDavSource, address


@dataclass
class File:
    """One file the share holds."""

    content: bytes
    etag: str | None = "v1"
    modified: str = "Mon, 01 Sep 2026 10:00:00 GMT"

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass
class Share:
    """A WebDAV share, described as the set of files it holds.

    Collections are implied by the paths, exactly as they are on a real server,
    so a test says what the tree contains rather than how to build it.
    """

    files: dict[str, File] = field(default_factory=dict)
    base_path: str = "/dav/"
    # Faults a test can turn on, each one something a real share does.
    etags: bool = True
    propfind_status: int | None = None
    extra_hrefs: dict[str, list[str]] = field(default_factory=dict)
    fail_get: set[str] = field(default_factory=set)
    oversized: set[str] = field(default_factory=set)
    retry_once: bool = False
    # A collection holding a symlink back to itself: every level is a path the
    # walk has never seen, so only a depth bound ever ends it.
    endless: bool = False
    # The same child listed twice, which some servers do under load.
    repeat_children: bool = False

    def __post_init__(self) -> None:
        self.propfind_calls: list[str] = []
        self._retried: set[str] = set()

    # ── the server ────────────────────────────────────────────────────────────

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = _decode(request.url.path)
        if request.method == "PROPFIND":
            return self._propfind(path)
        if request.method == "GET":
            return self._get(path)
        return httpx.Response(405)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    # ── PROPFIND ──────────────────────────────────────────────────────────────

    def _propfind(self, path: str) -> httpx.Response:
        self.propfind_calls.append(path)
        if self.propfind_status is not None:
            return httpx.Response(self.propfind_status, text="refused")
        if self.retry_once and path not in self._retried:
            self._retried.add(path)
            return httpx.Response(503, headers={"Retry-After": "0"})
        if not path.endswith("/"):
            # Exactly what Apache does, and what this client refuses to follow.
            return httpx.Response(301, headers={"Location": f"{path}/"})
        if not self._is_collection(path):
            return httpx.Response(404)

        responses = [self._collection_xml(path)]
        relative = path[len(self.base_path) :]
        for name, is_collection in self._children(relative):
            child = f"{relative}{name}"
            responses.append(
                self._collection_xml(f"{self.base_path}{child}/")
                if is_collection
                else self._file_xml(child)
            )
            if self.repeat_children:
                responses.append(
                    self._collection_xml(f"{self.base_path}{child}/")
                    if is_collection
                    else self._file_xml(child)
                )
        if self.endless:
            responses.append(self._collection_xml(f"{path}link/"))
        responses.extend(
            _response_xml(href, properties="")
            for href in self.extra_hrefs.get(path, [])
        )
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<D:multistatus xmlns:D="DAV:">' + "".join(responses) + "</D:multistatus>"
        )
        return httpx.Response(207, text=body)

    def _is_collection(self, path: str) -> bool:
        if path == self.base_path or self.endless:
            return True
        relative = path[len(self.base_path) :]
        return any(name.startswith(relative) for name in self.files)

    def _children(self, relative: str) -> list[tuple[str, bool]]:
        """Direct children of a collection, each said to be one or not."""
        seen: dict[str, bool] = {}
        for name in self.files:
            if not name.startswith(relative):
                continue
            remainder = name[len(relative) :]
            head, slash, _ = remainder.partition("/")
            if head:
                seen.setdefault(head, bool(slash))
        return sorted(seen.items())

    def _collection_xml(self, path: str) -> str:
        return _response_xml(
            _encode(path),
            properties="<D:resourcetype><D:collection/></D:resourcetype>",
        )

    def _file_xml(self, relative: str) -> str:
        entry = self.files[relative]
        size = 80_000_000 if relative in self.oversized else entry.size
        properties = [
            "<D:resourcetype/>",
            f"<D:getcontentlength>{size}</D:getcontentlength>",
            f"<D:getlastmodified>{entry.modified}</D:getlastmodified>",
        ]
        found = "".join(properties)
        missing = ""
        if self.etags and entry.etag is not None:
            found += f"<D:getetag>{escape(entry.etag)}</D:getetag>"
        else:
            # A share that keeps no entity tags says so per property, which is
            # the shape that trips a parser reading the first propstat blindly.
            missing = (
                "<D:propstat><D:prop><D:getetag/></D:prop>"
                "<D:status>HTTP/1.1 404 Not Found</D:status></D:propstat>"
            )
        return _response_xml(
            _encode(f"{self.base_path}{relative}"), properties=found, extra=missing
        )

    # ── GET ───────────────────────────────────────────────────────────────────

    def _get(self, path: str) -> httpx.Response:
        relative = path[len(self.base_path) :]
        if relative in self.fail_get:
            return httpx.Response(500, text="broken")
        if relative in self.oversized:
            return httpx.Response(200, content=b"x" * 2_000_000)
        entry = self.files.get(relative)
        if entry is None:
            return httpx.Response(404)
        return httpx.Response(200, content=entry.content)


def _response_xml(href: str, *, properties: str, extra: str = "") -> str:
    found = (
        f"<D:propstat><D:prop>{properties}</D:prop>"
        "<D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
        if properties
        else ""
    )
    return f"<D:response><D:href>{escape(href)}</D:href>{found}{extra}</D:response>"


def _encode(path: str) -> str:
    from urllib.parse import quote

    return quote(path, safe="/")


def _decode(path: str) -> str:
    from urllib.parse import unquote

    return unquote(path)


def share_url(share: Share) -> str:
    """The address a person would paste, which is the encoded one."""
    return f"https://share.example.com{_encode(share.base_path)}"


def source_for(
    share: Share, *, max_file_bytes: int = 1_000_000, url: str | None = None
) -> WebDavSource:
    """The real client, pointed at the share above through an in-memory transport."""
    where: Address = address(url or share_url(share))
    return WebDavSource(
        where=where,
        max_file_bytes=max_file_bytes,
        client=httpx.AsyncClient(
            transport=share.transport(), follow_redirects=False, base_url=""
        ),
    )


class RecordingBoundary:
    """A library that holds what it accepted, and can refuse on demand.

    `contents` is the library itself and survives across runs, which is what
    makes a second run incremental now that nothing is kept on disk. The two
    logs are per run, and `new_run` is what clears them — a run that is offered
    a document is not the same thing as a library that holds one, and these
    tests turn on telling the two apart.
    """

    def __init__(
        self,
        *,
        holds: Mapping[str, str | None] | None = None,
        fail_publish: Collection[str] = (),
        fail_retract: Collection[str] = (),
        still_ingesting: Collection[str] = (),
        fail_listing: bool = False,
    ) -> None:
        self.contents: dict[str, str | None] = dict(holds or {})
        self.published: dict[str, tuple[bytes, str]] = {}
        self.retracted: list[str] = []
        self.fail_listing = fail_listing
        # Accepted and still being ingested: written, and correctly absent from
        # the listing until it finishes. The gap the whole design turns on.
        self.still_ingesting = set(still_ingesting)
        self.fail_publish = set(fail_publish)
        self.fail_retract = set(fail_retract)

    def new_run(self) -> None:
        self.published.clear()
        self.retracted.clear()

    async def publish(
        self, *, relative_path: str, content: bytes, version: str
    ) -> None:
        if relative_path in self.fail_publish:
            raise RuntimeError(f"refused {relative_path}")
        self.published[relative_path] = (content, version)
        if relative_path not in self.still_ingesting:
            self.contents[relative_path] = version or None

    async def documents(self) -> dict[str, str | None]:
        if self.fail_listing:
            raise RuntimeError("the library could not be read")
        return dict(self.contents)

    async def retract(self, *, relative_path: str) -> None:
        if relative_path in self.fail_retract:
            raise RuntimeError(f"refused {relative_path}")
        self.retracted.append(relative_path)
        self.contents.pop(relative_path, None)

    async def aclose(self) -> None:
        return None


def settings_for(
    share: Share,
    *,
    include: str = "**/*.md",
    exclude: str = "",
    max_files: int | None = None,
    trust_any_certificate: bool = False,
):
    """The settings a team's form would produce for this share."""
    from fred_samples_webdav_kb.settings import read_settings

    configuration: dict[str, object] = {
        "url": share_url(share),
        "include": include,
        "exclude": exclude,
        "trust_any_certificate": trust_any_certificate,
    }
    if max_files is not None:
        configuration["max_files"] = max_files
    else:
        configuration["max_files"] = None
    return read_settings(configuration)


@pytest.fixture(autouse=True)
def _no_ambient_fred_configuration(monkeypatch: pytest.MonkeyPatch):
    """No test may pick up the configuration a developer happens to have.

    A pod resolves `./config/configuration.yaml` by default, so a suite run
    from this directory would otherwise reach the real Control Plane and
    Knowledge Flow of whoever ran it — passing or failing on their machine's
    state rather than on the code.
    """
    monkeypatch.setenv("CONFIG_FILE", "/nonexistent/configuration.yaml")
    monkeypatch.setenv("ENV_FILE", "/nonexistent/.env")
