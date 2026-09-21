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
Reading a WebDAV share: one walk of the tree, and one file at a time.

The protocol is two verbs. PROPFIND asks a collection what it holds, GET
fetches a file. Everything below that is the careful part — what a server is
allowed to say about itself, and what this implementation refuses to believe.

Full rationale for the walk's shape, and for every refusal here, is in
README.md; the comments below say only what the code cannot.
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx
from defusedxml import ElementTree as DefusedElementTree

from fred_samples_webdav_kb.source import (
    Inventory,
    NotAWebDavCollection,
    RemoteFile,
    SourceUnavailable,
)

logger = logging.getLogger(__name__)

# An element of a parsed multistatus. Spelled as Any rather than imported from
# xml.etree, which is the module defusedxml exists to keep out of this file.
type DavElement = Any

DAV = "{DAV:}"
MULTI_STATUS = 207

# Where a deployment puts a private certificate authority's PEM. Not a
# configuration field: it names a file inside the pod, which nobody filling in
# a form can create.
CA_FILE_ENV = "FRED_SAMPLES_WEBDAV_CA_FILE"

# Depth 1 and a walk of our own, never Depth: infinity. Apache ships
# `DavDepthInfinity off` by default and answers 403 to it, so the one request
# that would be cheapest is also the one most shares refuse.
_PROPFIND_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<D:propfind xmlns:D="DAV:"><D:prop>'
    b"<D:resourcetype/><D:getetag/><D:getlastmodified/><D:getcontentlength/>"
    b"</D:prop></D:propfind>"
)

# Named properties rather than allprop: a share with rich metadata answers
# allprop with kilobytes per file, and none of it is read here.
_PROPFIND_HEADERS = {
    "Depth": "1",
    "Content-Type": 'application/xml; charset="utf-8"',
    # Some caching proxies happily serve a stale multistatus, which reads as a
    # tree that never changes and a library that silently stops updating.
    "Cache-Control": "no-cache",
}

# Bounds on the walk itself, not on what a team chose. They exist so a broken
# or hostile share costs one bounded run rather than a pod that never returns:
# a collection that contains itself through a symlink is an ordinary Apache
# misconfiguration, not an attack.
MAX_COLLECTIONS = 5_000
MAX_DEPTH = 32

# A retry is for a share that is momentarily busy, not for one that is broken.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 30.0
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})

_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 60.0

# What a caller is told about a failure. The rest goes to the log rather than
# into a run report an operator reads.
_MAX_DETAIL = 200


class FileTooLarge(SourceUnavailable):
    """The share sent more bytes than this run agreed to carry."""


class CertificateNotTrusted(SourceUnavailable):
    """The share's certificate is signed by an authority this pod does not have.

    Its own error because it is the first thing a corporate share does, and
    because it is the one failure a retry cannot help and an operator cannot
    diagnose from the share: the fault is in this pod's trust store, and the
    same URL fetched with curl from a laptop works.
    """


@dataclass(frozen=True, slots=True)
class Address:
    """Where the share is, split the way every request below needs it.

    `base_path` is **decoded** and always ends in a slash. Decoded, because the
    hrefs it is compared against are decoded before they are checked, and a
    folder whose name holds a space or an accent is written one way in an
    address bar and another in an answer — comparing the two forms matches
    nothing, drops every file, and leaves an empty inventory that reads as a
    share whose every document was deleted.
    """

    origin: str
    base_path: str

    @property
    def url(self) -> str:
        return f"{self.origin}{quote(self.base_path, safe='/')}"

    def resolve(self, relative: str) -> str:
        """The absolute URL of a path relative to the configured root."""
        return (
            f"{self.origin}{quote(self.base_path, safe='/')}{quote(relative, safe='/')}"
        )


class AddressError(ValueError):
    """A configured address this implementation cannot work with."""


def address(raw: str) -> Address:
    """Read the configured address, or say why it is not one.

    The trailing slash is added rather than required: a collection asked for
    without one earns a redirect from most servers, and redirects are refused
    below — so the likeliest way to fill this form in has to be the working one.
    """
    candidate = raw.strip()
    if not candidate:
        raise AddressError("An address is required.")
    split = urlsplit(candidate)
    if split.scheme not in ("http", "https"):
        raise AddressError(f"{candidate!r} is not an http:// or https:// address.")
    if not split.hostname:
        raise AddressError(f"{candidate!r} names no host.")
    if "@" in split.netloc:
        # Credentials belong in the two fields beside this one, where they are
        # stored as a secret — not in an address that reaches logs and metrics.
        raise AddressError("Put the user name and password in their own fields.")
    if split.query or split.fragment:
        raise AddressError("An address carries no query string or fragment.")

    path = unquote(split.path or "/")
    if not path.endswith("/"):
        path = f"{path}/"
    return Address(origin=f"{split.scheme}://{split.netloc}", base_path=path)


@dataclass(frozen=True, slots=True)
class _Entry:
    """One child of a collection, as the multistatus described it."""

    path: str
    is_collection: bool
    version: str
    size_bytes: int


class WebDavSource:
    """One WebDAV share, read over HTTP with no state kept between runs."""

    def __init__(
        self,
        *,
        where: Address,
        username: str = "",
        password: str = "",
        verify: ssl.SSLContext | bool = True,
        max_file_bytes: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._where = where
        self._max_file_bytes = max_file_bytes
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
            verify=verify,
            # Never followed. A redirect off this host would present the share's
            # credentials to whatever answered, and a redirect on it means the
            # address is wrong in a way an operator should be told about rather
            # than have silently repaired on every run.
            follow_redirects=False,
            auth=httpx.BasicAuth(username, password) if username else None,
            headers={"User-Agent": "fred-samples-webdav-kb"},
        )

    async def inventory(self) -> Inventory:
        """Walk the tree breadth-first, one PROPFIND per collection.

        Deliberately unbounded in files: the configured bound is a maximum
        number of *documents*, and only the selection knows which of these are
        documents. Cutting the walk off by files seen would end it early on a
        share that merely holds a lot of pictures, and a walk cut short can
        never remove anything — so the bound meant to cap one run's work would
        quietly stop deletions from ever being noticed again. Depth and
        collections are still bounded, because those end a walk that has no
        end of its own.
        """
        found: dict[str, RemoteFile] = {}
        pending: deque[tuple[str, int]] = deque([("", 0)])
        visited: set[str] = {""}
        exhaustive = True

        while pending:
            relative, depth = pending.popleft()
            for entry in await self._list(relative):
                if not entry.is_collection:
                    # Keyed by path: a share that lists a file twice would
                    # otherwise have its document published twice and counted
                    # twice.
                    found[entry.path] = RemoteFile(
                        path=entry.path,
                        version=entry.version,
                        size_bytes=entry.size_bytes,
                    )
                    continue
                if entry.path in visited:
                    # A collection reached twice is a cycle, which a symlink on
                    # the server makes without anyone intending it.
                    continue
                if depth + 1 > MAX_DEPTH or len(visited) >= MAX_COLLECTIONS:
                    exhaustive = False
                    continue
                visited.add(entry.path)
                pending.append((entry.path, depth + 1))

        files = list(found.values())
        if not exhaustive:
            logger.warning(
                "[WEBDAV KB] %s: the walk was cut short, so nothing it did not "
                "see counts as deleted",
                self._where.url,
            )
        return Inventory(files=tuple(files), exhaustive=exhaustive)

    async def read(self, file: RemoteFile) -> bytes:
        """Fetch one file, refusing more bytes than were agreed.

        The size is checked again while reading because the one from the walk
        is the share's claim about a file it has since been free to change —
        and because a server is not obliged to be honest about it at all.
        """
        url = self._where.resolve(file.path)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with self._client.stream("GET", url) as response:
                    if _should_retry(response.status_code) and attempt < MAX_ATTEMPTS:
                        await _pause(attempt, response.headers.get("Retry-After"))
                        continue
                    _raise_for(response.status_code, "", f"reading {file.path}")
                    return await self._collect(response, file)
            except httpx.TransportError as error:
                rejected = _certificate_rejected(error)
                if rejected is not None:
                    raise _not_trusted(rejected, f"reading {file.path}") from error
                if attempt >= MAX_ATTEMPTS:
                    raise SourceUnavailable(f"reading {file.path}: {error}") from error
                await _pause(attempt, None)
        raise SourceUnavailable(f"reading {file.path}: {MAX_ATTEMPTS} attempts failed")

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── internals ─────────────────────────────────────────────────────────────

    async def _collect(self, response: httpx.Response, file: RemoteFile) -> bytes:
        # One growing buffer rather than a list of chunks joined at the end:
        # the join would hold the whole file twice, and several of these run at
        # once inside a pod with a memory limit.
        buffer = bytearray()
        async for chunk in response.aiter_bytes():
            if len(buffer) + len(chunk) > self._max_file_bytes:
                raise FileTooLarge(
                    f"{file.path} is past the {self._max_file_bytes} byte bound"
                )
            buffer.extend(chunk)
        return bytes(buffer)

    async def _list(self, relative: str) -> list[_Entry]:
        """One collection's direct children, named from the share's root.

        A collection is always asked for with its trailing slash: without one
        every server in the world answers a redirect, and this client refuses
        those on purpose.
        """
        within = f"{relative}/" if relative else ""
        response = await self._propfind(self._where.resolve(within))
        return list(
            _entries(
                response.text,
                base_path=self._where.base_path,
                origin=self._where.origin,
                within=within,
            )
        )

    async def _propfind(self, url: str) -> httpx.Response:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._client.request(
                    "PROPFIND", url, content=_PROPFIND_BODY, headers=_PROPFIND_HEADERS
                )
            except httpx.TransportError as error:
                rejected = _certificate_rejected(error)
                if rejected is not None:
                    # Never retried: a trust store does not change between two
                    # attempts a second apart, so three handshakes reach the
                    # same verdict and only delay the message explaining it.
                    raise _not_trusted(rejected, f"reaching {url}") from error
                if attempt >= MAX_ATTEMPTS:
                    raise SourceUnavailable(f"reaching {url}: {error}") from error
                await _pause(attempt, None)
                continue

            if _should_retry(response.status_code) and attempt < MAX_ATTEMPTS:
                await _pause(attempt, response.headers.get("Retry-After"))
                continue
            if response.status_code == MULTI_STATUS:
                return response
            if response.status_code < 300:
                # A plain 200 to a PROPFIND is a web server, a proxy or a
                # captive portal answering with a page. Retrying it would do
                # the same thing twice more and then blame the network.
                raise NotAWebDavCollection(
                    f"listing {url}: answered {response.status_code} rather than a "
                    "WebDAV multistatus — this address is probably not a WebDAV "
                    "collection."
                )
            _raise_for(response.status_code, response.text, f"listing {url}")
        raise SourceUnavailable(f"listing {url}: {MAX_ATTEMPTS} attempts failed")


def _entries(
    document: str, *, base_path: str, origin: str, within: str
) -> Iterator[_Entry]:
    """Read a multistatus into one collection's direct children.

    Every path is resolved against the share's root, never against the
    collection being listed, so the name a document is written under is the
    same whichever walk reached it.

    A response naming anything outside the collection that was asked for is
    dropped: the href decides the key a document is written under, so a server
    that answered with someone else's path would otherwise choose where a
    team's document lands.
    """
    try:
        root = DefusedElementTree.fromstring(document)
    except Exception as error:
        raise SourceUnavailable(f"the share's answer is not XML: {error}") from error
    if root.tag != f"{DAV}multistatus":
        raise NotAWebDavCollection(
            "the share answered 207 without a WebDAV multistatus"
        )

    for response in root.findall(f"{DAV}response"):
        href = response.findtext(f"{DAV}href")
        if href is None:
            continue
        relative = _relative_path(href, base_path=base_path, origin=origin)
        if relative is None or not relative.startswith(within):
            logger.warning("[WEBDAV KB] ignoring an entry outside the share: %r", href)
            continue
        if relative == within:
            # The collection describing itself, which every Depth: 1 answer
            # opens with.
            continue

        properties = _properties(response)
        if properties is None:
            continue
        is_collection = (
            properties.find(f"{DAV}resourcetype/{DAV}collection") is not None
        )
        yield _Entry(
            path=relative.rstrip("/") if is_collection else relative,
            is_collection=is_collection,
            version=_version(properties),
            size_bytes=_size(properties),
        )


def _properties(response: DavElement) -> DavElement | None:
    """The properties the server actually answered with, ignoring the rest.

    A multistatus reports per-property status, and a share that does not keep
    entity tags answers 404 for that one property while answering 200 for the
    others. Reading a 404 block as though it held values is how a file ends up
    with an empty version and is republished on every single run.
    """
    for propstat in response.findall(f"{DAV}propstat"):
        status = propstat.findtext(f"{DAV}status") or ""
        if _is_success(status):
            return propstat.find(f"{DAV}prop")
    return None


def _is_success(status: str) -> bool:
    """Whether a `HTTP/1.1 200 OK` status line says 2xx."""
    parts = status.split()
    if len(parts) < 2 or not parts[1].isdigit():
        # No status line at all: take the block rather than drop a file over a
        # server's sloppiness.
        return not status.strip()
    return 200 <= int(parts[1]) < 300


def _relative_path(href: str, *, base_path: str, origin: str) -> str | None:
    """Where this href sits inside the configured share, or None if outside.

    Decoded before it is checked, never after: `%2e%2e` is exactly the thing
    a check run on the raw href would wave through.
    """
    split = urlsplit(href.strip())
    if split.netloc and f"{split.scheme}://{split.netloc}".lower() != origin.lower():
        return None
    path = unquote(split.path)
    if not path.startswith(base_path):
        return None
    relative = path[len(base_path) :]
    if _unusable(relative):
        return None
    return relative


def _unusable(relative: str) -> bool:
    """Whether this path can be a document's key, and a URL, and a file name."""
    if "//" in relative or "\\" in relative:
        return True
    if any(character < " " or character == "\x7f" for character in relative):
        return True
    return any(segment in (".", "..") for segment in relative.rstrip("/").split("/"))


def _version(properties: DavElement) -> str:
    """The share's own answer to "has this file changed".

    An entity tag when there is one — the cheapest correct answer, and what
    Apache gives for a static file. Failing that, the modification date and the
    size together, which is weaker but still catches every ordinary edit. A
    share offering neither leaves every file looking new on every run, which is
    correct and expensive, and the run says so.
    """
    etag = (properties.findtext(f"{DAV}getetag") or "").strip()
    if etag:
        # Kept exactly as given, weak validator and quotes included: it is
        # compared for equality and read by nobody.
        return f"etag:{etag}"
    modified = (properties.findtext(f"{DAV}getlastmodified") or "").strip()
    length = (properties.findtext(f"{DAV}getcontentlength") or "").strip()
    if modified:
        return f"mtime:{modified}:{length}"
    return ""


def _size(properties: DavElement) -> int:
    raw = (properties.findtext(f"{DAV}getcontentlength") or "").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        # Collections have no length, and some shares omit it for files they
        # generate. Unknown reads as zero and the bound is enforced on the
        # bytes that actually arrive.
        return 0


def _certificate_rejected(error: BaseException) -> ssl.SSLCertVerificationError | None:
    """The certificate failure inside a transport error, if that is what it is.

    Walked rather than matched on the message: httpx reports a handshake as a
    plain `ConnectError` carrying only text, and the original is reached
    through `__context__` rather than `__cause__` — the wrapping happens in a
    different frame from the handshake, so the chain the wrapper builds ends
    one link early.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return current
        current = current.__cause__ or current.__context__
    return None


def _not_trusted(
    failure: ssl.SSLCertVerificationError, what: str
) -> CertificateNotTrusted:
    """Say which trust store refused, and what would fix it.

    Reading the environment again here rather than carrying it from `tls_policy`
    is deliberate: what matters to whoever reads this is whether the pod was
    given an authority at all, which is one variable and not a run's state.
    """
    ca_file = configured_ca_file()
    remedy = (
        f"${CA_FILE_ENV} names {ca_file}, and that authority does not certify "
        "this share"
        if ca_file
        else "This pod trusts public authorities only; give the issuing "
        f"authority's public root to the pod through ${CA_FILE_ENV}"
    )
    # `verify_message` is the short reason — "unable to get local issuer
    # certificate" — and is set by the handshake, not by the class, so anything
    # that did not come from one has only its text.
    reason = getattr(failure, "verify_message", None) or failure
    return CertificateNotTrusted(
        f"{what}: the share's certificate is signed by an authority this pod "
        f"does not trust ({reason}). {remedy}. Python reads no system trust "
        "store, so curl working on a laptop says nothing about this."
    )


def _should_retry(status: int) -> bool:
    return status in _RETRYABLE_STATUS


async def _pause(attempt: int, retry_after: str | None) -> None:
    """Wait before trying again, honouring the share's own answer if it gave one."""
    delay = BACKOFF_SECONDS * (2 ** (attempt - 1))
    if retry_after and retry_after.strip().isdigit():
        delay = min(float(retry_after.strip()), MAX_RETRY_AFTER_SECONDS)
    await asyncio.sleep(delay)


def _raise_for(status: int, body: str, what: str) -> None:
    if status < 400:
        if status >= 300:
            raise SourceUnavailable(
                f"{what}: the share redirected ({status}). Configure the address "
                "it redirects to, so credentials are never sent elsewhere."
            )
        return
    if status in (401, 403):
        raise SourceUnavailable(
            f"{what}: the share refused the request ({status}). Check the user "
            "name and password, and that the share allows PROPFIND."
        )
    if status == 405:
        raise NotAWebDavCollection(
            f"{what}: the server does not allow PROPFIND here — WebDAV is "
            "probably not enabled for this location."
        )
    if status == 404:
        raise SourceUnavailable(f"{what}: the share has no such collection (404).")
    detail = " ".join(body.split())[:_MAX_DETAIL]
    raise SourceUnavailable(f"{what}: {status} {detail}")


class TrustStoreError(ValueError):
    """The certificate authority this pod was told to trust cannot be read.

    Its own error because in a cluster it means one specific thing — a volume
    that did not mount — and because the alternative is a handshake failure
    that names the share rather than the mount.
    """


def configured_ca_file() -> str:
    """The private certificate authority this pod was given, if it was given one."""
    return os.environ.get(CA_FILE_ENV, "").strip()


def tls_policy(
    *, trust_any_certificate: bool, ca_file: str = ""
) -> ssl.SSLContext | bool:
    """How this run treats the share's certificate.

    A private authority is **added** to what is already trusted, never
    substituted for it. $SSL_CERT_FILE, the variable everyone reaches for
    first, replaces the whole trust store: point it at one corporate root and
    this pod stops trusting every other authority — including the ones its own
    Keycloak, Control Plane and Knowledge Flow may be presenting, which then
    fail with a handshake error that says nothing about the cause. That
    variable is still honoured, because httpx reads it and an operator may have
    set it deliberately; this only ever adds to whatever it produced.

    Returning a context rather than a path is also deliberate: httpx deprecated
    `verify=<str>`.
    """
    if trust_any_certificate:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    if not ca_file:
        return True
    context = httpx.create_ssl_context()
    try:
        context.load_verify_locations(cafile=ca_file)
    except ssl.SSLError as error:
        # Before OSError, which it inherits from: the file is there and is not
        # a certificate, which is a different mistake from it being absent.
        raise TrustStoreError(
            f"{ca_file} is not a PEM certificate authority: {error}"
        ) from error
    except OSError as error:
        raise TrustStoreError(
            f"the certificate authority at {ca_file} could not be read: {error}. "
            f"${CA_FILE_ENV} names it; check the volume is mounted there."
        ) from error
    return context
