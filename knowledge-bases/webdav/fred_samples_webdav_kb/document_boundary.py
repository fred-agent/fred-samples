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

Documents go through Knowledge Flow's synchronizing ingestion surface, using
the SDK's own publisher — so this sample writes no authentication code and
holds no store credential. A Knowledge Base never writes to OpenSearch or
object storage directly. Fred accepts a write and ingests it afterwards; the
boundary follows each one to its end, so a document counted as written has
actually landed.

Without a Knowledge Flow URL in the environment the boundary only logs, which
is what lets `make sync` run against a real share with no Fred at all.
"""

from __future__ import annotations

import logging
from typing import Protocol

from fred_sdk.knowledge_base import DocumentPublisher, MissingPodConfiguration
from fred_sdk.knowledge_base.configuration import PodConfiguration

from fred_samples_webdav_kb.settings import DEFAULT_PROFILE, IngestionProfile

logger = logging.getLogger(__name__)

# Which configured document source a write is attributed to. Knowledge Flow
# resolves this against its own deployment configuration and refuses anything
# it does not know, so a Knowledge Base cannot name itself here: the vocabulary
# is the platform's, not a contributor's.
SOURCE_TAG = "fred"


class Boundary(Protocol):
    """What the handler needs from Fred, so a run can be tried without one."""

    async def publish(
        self, *, relative_path: str, content: bytes, version: str
    ) -> None: ...

    async def documents(self) -> dict[str, str | None]: ...

    async def retract(self, *, relative_path: str) -> None: ...

    async def aclose(self) -> None: ...


class LoggingBoundary:
    """Says what it would do. The offline developer tool's boundary."""

    def __init__(self, *, profile: IngestionProfile = DEFAULT_PROFILE) -> None:
        self._profile = profile

    async def publish(
        self, *, relative_path: str, content: bytes, version: str
    ) -> None:
        logger.info(
            "would publish %s (%d bytes, version %s, profile %s)",
            relative_path,
            len(content),
            version or "none",
            self._profile,
        )

    async def documents(self) -> dict[str, str | None]:
        # Nothing was ever written, so the library this stands in for is empty:
        # every run is a first run, which is what a dry run should look like.
        return {}

    async def retract(self, *, relative_path: str) -> None:
        logger.info("would retract %s", relative_path)

    async def aclose(self) -> None:
        return None


class _KnowledgeFlowBoundary:
    """Writes into, and takes back out of, the library Fred gave this run.

    Every operation is addressed by the file's path on the share, which is
    this implementation's source key — including the read-back, which is what
    a run reconciles against. Nothing Fred assigns outlives a call here: a
    write is accepted with a task and followed to its end, so `publish` returns
    only once the document has landed — and raises when it did not.
    """

    def __init__(
        self, publisher: DocumentPublisher, *, profile: IngestionProfile = DEFAULT_PROFILE
    ) -> None:
        self._publisher = publisher
        self._profile = profile

    async def publish(
        self, *, relative_path: str, content: bytes, version: str
    ) -> None:
        # An empty version is offered as none at all rather than as an empty
        # string: a share with no entity tags has no version, and saying so is
        # not the same as claiming one that never changes.
        handle = await self._publisher.publish(
            relative_path=relative_path,
            content=content,
            version=version or None,
            profile=self._profile,
        )
        logger.info("accepted %s as task %s", relative_path, handle.task_id)
        outcome = await self._publisher.wait(handle.task_id)
        if not outcome.succeeded:
            logger.info("failed %s: ingestion %s", relative_path, outcome.state)
            raise RuntimeError(
                f"ingestion {outcome.state}: {outcome.error or 'no reason given'}"
            )
        logger.info("landed %s", relative_path)

    async def documents(self) -> dict[str, str | None]:
        return await self._publisher.documents()

    async def retract(self, *, relative_path: str) -> None:
        await self._publisher.retract(relative_path=relative_path)
        logger.info("retracted %s", relative_path)

    async def aclose(self) -> None:
        await self._publisher.aclose()


def open_boundary(
    *,
    library_id: str,
    source_tag: str = SOURCE_TAG,
    profile: IngestionProfile = DEFAULT_PROFILE,
) -> Boundary:
    """Whichever boundary this environment can support."""
    try:
        configuration = PodConfiguration.load()
    except MissingPodConfiguration as error:
        logger.info("No Fred configuration (%s): logging documents instead", error)
        return LoggingBoundary(profile=profile)

    if not configuration.knowledge_flow_url:
        logger.info("No Knowledge Flow URL set: logging documents instead")
        return LoggingBoundary(profile=profile)

    return _KnowledgeFlowBoundary(
        DocumentPublisher(configuration, library_id=library_id, source_tag=source_tag),
        profile=profile,
    )
