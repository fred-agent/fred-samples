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
object storage directly.

Without a Knowledge Flow URL in the environment the boundary only logs, which
is what lets `make sync` run against a real share with no Fred at all.
"""

from __future__ import annotations

import logging
from typing import Protocol

from fred_sdk.knowledge_base import DocumentPublisher, MissingPodEnvironment
from fred_sdk.knowledge_base.environment import PodEnvironment

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

    async def retract(self, *, relative_path: str) -> None: ...

    async def aclose(self) -> None: ...


class LoggingBoundary:
    """Says what it would do. The offline developer tool's boundary."""

    async def publish(
        self, *, relative_path: str, content: bytes, version: str
    ) -> None:
        logger.info(
            "would publish %s (%d bytes, version %s)",
            relative_path,
            len(content),
            version or "none",
        )

    async def retract(self, *, relative_path: str) -> None:
        logger.info("would retract %s", relative_path)

    async def aclose(self) -> None:
        return None


class _KnowledgeFlowBoundary:
    """Writes into, and takes back out of, the library Fred gave this run.

    Both operations are addressed by the file's path on the share, which is
    this implementation's source key. Nothing Fred assigns is ever held here.
    """

    def __init__(self, publisher: DocumentPublisher) -> None:
        self._publisher = publisher

    async def publish(
        self, *, relative_path: str, content: bytes, version: str
    ) -> None:
        # An empty version is offered as none at all rather than as an empty
        # string: a share with no entity tags has no version, and saying so is
        # not the same as claiming one that never changes.
        await self._publisher.publish(
            relative_path=relative_path, content=content, version=version or None
        )
        logger.info("published %s", relative_path)

    async def retract(self, *, relative_path: str) -> None:
        await self._publisher.retract(relative_path=relative_path)
        logger.info("retracted %s", relative_path)

    async def aclose(self) -> None:
        await self._publisher.aclose()


def open_boundary(*, library_id: str, source_tag: str = SOURCE_TAG) -> Boundary:
    """Whichever boundary this environment can support."""
    try:
        environment = PodEnvironment.from_env(require_temporal=False)
    except MissingPodEnvironment as error:
        logger.info("No Fred environment (%s): logging documents instead", error)
        return LoggingBoundary()

    if not environment.knowledge_flow_url:
        logger.info("No Knowledge Flow URL set: logging documents instead")
        return LoggingBoundary()

    return _KnowledgeFlowBoundary(
        DocumentPublisher(environment, library_id=library_id, source_tag=source_tag)
    )
