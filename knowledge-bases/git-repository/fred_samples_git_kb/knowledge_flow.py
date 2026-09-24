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

Everything goes through the SDK's `DocumentPublisher`, with the pod's own
workload identity: this sample writes no authentication code and holds no store
credential. A Knowledge Base never writes to OpenSearch or object storage
directly.

Without a Knowledge Flow URL in the configuration it logs instead, which is
what lets a run be tried against a real repository with no Fred at all.
"""

from __future__ import annotations

import logging

from fred_sdk.knowledge_base import DocumentPublisher, MissingPodConfiguration
from fred_sdk.knowledge_base.configuration import PodConfiguration

from fred_samples_git_kb.library import Library, LibraryError, LoggingLibrary

logger = logging.getLogger(__name__)

# Which configured document source a write is attributed to. Knowledge Flow
# resolves this against its own deployment configuration, so a Knowledge Base
# cannot name itself here: it is the platform's vocabulary, not ours.
SOURCE_TAG = "fred"


class KnowledgeFlowLibrary:
    """One library, written and read back through the SDK.

    Fred accepts a write and ingests it afterwards; `write` follows each one to
    its end, so a document counted as written has landed — which is what lets
    the cursor move past it.
    """

    def __init__(self, publisher: DocumentPublisher) -> None:
        self._publisher = publisher

    async def read_cursor(self) -> str | None:
        return await self._publisher.source_version()

    async def record_cursor(self, value: str) -> None:
        await self._publisher.record_source_version(value)

    async def documents(self) -> dict[str, str | None]:
        return await self._publisher.documents()

    async def write(
        self, *, source_key: str, document_version: str, content: bytes
    ) -> bool:
        handle = await self._publisher.publish(
            relative_path=source_key, content=content, version=document_version
        )
        outcome = await self._publisher.wait(handle.task_id)
        if not outcome.succeeded:
            raise LibraryError(
                f"ingestion {outcome.state}: {outcome.error or 'no reason given'}"
            )
        return handle.created

    async def remove(self, *, source_key: str) -> None:
        await self._publisher.retract(relative_path=source_key)

    async def aclose(self) -> None:
        await self._publisher.aclose()


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

    return KnowledgeFlowLibrary(
        DocumentPublisher(configuration, library_id=library_id, source_tag=SOURCE_TAG)
    )
