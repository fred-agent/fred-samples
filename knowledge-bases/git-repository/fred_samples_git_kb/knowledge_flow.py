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
The seam where this Knowledge Base hands documents to Fred.

It speaks Knowledge Flow's synchronizing ingestion surface — the one that
addresses a document by the key its source chose — with the pod's own
workload identity, so nothing here holds a store credential and no document
carries a Fred-side identifier back into this implementation.

This file is temporary by design: the SDK will publish its own client for this
surface, and when it does, everything below collapses into using it. Nothing
above this file changes when that happens.

Without a Knowledge Flow URL in the environment it logs instead, which is what
lets a run be tried against a real repository with no Fred at all.
"""

from __future__ import annotations

import logging
import mimetypes

import httpx
from fred_core.security.backend_to_backend_auth import M2MTokenProvider
from fred_sdk.knowledge_base import MissingPodConfiguration
from fred_sdk.knowledge_base.configuration import PodConfiguration

from fred_samples_git_kb.library import Library, LibraryError, LoggingLibrary

logger = logging.getLogger(__name__)

# A write converts and indexes the document before it answers, so this is
# minutes rather than the seconds a metadata call would get.
_WRITE_TIMEOUT = httpx.Timeout(300.0, connect=10.0)

# Which configured document source a write is attributed to. Knowledge Flow
# resolves this against its own deployment configuration, so a Knowledge Base
# cannot name itself here: it is the platform's vocabulary, not ours.
SOURCE_TAG = "fred"

# What a caller is told about a failure. The rest goes to the log instead of
# into a run report an operator reads.
_MAX_DETAIL = 200


class KnowledgeFlowLibrary:
    """One library, written through the surface meant for a synchronizing caller."""

    def __init__(self, configuration: PodConfiguration, *, library_id: str) -> None:
        self._base_url = configuration.knowledge_flow_url
        self._library_id = library_id
        self._client = httpx.AsyncClient(timeout=_WRITE_TIMEOUT)
        # Built by the configuration, never here: it already knows the realm,
        # the client and which environment variable holds the secret.
        self._tokens = M2MTokenProvider(configuration.m2m)

    # ── the cursor ────────────────────────────────────────────────────────────

    async def read_cursor(self) -> str | None:
        response = await self._client.get(
            f"{self._documents_base}/source-version", headers=await self._headers()
        )
        self._raise_for(response, "reading the library's source version")
        return response.json().get("source_version")

    async def record_cursor(self, value: str) -> None:
        response = await self._client.put(
            f"{self._documents_base}/source-version",
            json={"source_version": value},
            headers=await self._headers(),
        )
        self._raise_for(response, "recording the library's source version")

    # ── the documents ─────────────────────────────────────────────────────────

    async def write(
        self,
        *,
        source_key: str,
        path: str,
        document_version: str,
        content: bytes,
    ) -> bool:
        form = {
            "path": path,
            "source_key": source_key,
            "document_version": document_version,
            "source_tag": SOURCE_TAG,
        }
        response = await self._client.post(
            f"{self._documents_base}/documents",
            data=form,
            files={
                "file": (
                    path.rsplit("/", 1)[-1],
                    content,
                    mimetypes.guess_type(path)[0] or "application/octet-stream",
                )
            },
            headers=await self._headers(),
        )
        self._raise_for(response, f"writing {source_key}")
        # The answer is an outcome, not a progress stream: whether this key was
        # new to the library is the library's to say, not something to remember.
        return bool(response.json().get("created", False))

    async def remove(self, *, source_key: str) -> bool:
        response = await self._client.request(
            "DELETE",
            f"{self._documents_base}/documents",
            params={"source_key": source_key},
            headers=await self._headers(),
        )
        self._raise_for(response, f"removing {source_key}")
        return bool(response.json().get("removed", False))

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── internals ─────────────────────────────────────────────────────────────

    @property
    def _documents_base(self) -> str:
        return f"{self._base_url}/libraries/{self._library_id}"

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._tokens.get_token()}"}

    @staticmethod
    def _raise_for(response: httpx.Response, what: str) -> None:
        if response.status_code < 400:
            return
        detail = " ".join(response.text.split())[:_MAX_DETAIL]
        raise LibraryError(f"{what}: {response.status_code} {detail}")


def open_library(library_id: str) -> Library:
    """Whichever library this environment can support."""
    try:
        configuration = PodConfiguration.load()
    except MissingPodConfiguration as error:
        logger.info("No Fred configuration (%s): logging documents instead", error)
        return LoggingLibrary()

    if not configuration.knowledge_flow_url:
        logger.info("No Knowledge Flow URL set: logging documents instead")
        return LoggingLibrary()

    return KnowledgeFlowLibrary(configuration, library_id=library_id)
