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

Documents go through Knowledge Flow's REST API, using the SDK's publisher so
this sample writes no authentication code and holds no store credential. A
Knowledge Base never writes to OpenSearch or S3 directly.

Without a Knowledge Flow URL in the environment the boundary only logs, which
is what lets `make sync` run against a folder with no Fred at all.
"""

from __future__ import annotations

import logging
from pathlib import Path
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
        self, *, relative_path: str, path: Path, version: str
    ) -> None: ...

    async def retract(self, *, relative_path: str) -> None: ...

    async def aclose(self) -> None: ...


class _LoggingBoundary:
    """Says what it would do. The offline developer tool's boundary."""

    async def publish(self, *, relative_path: str, path: Path, version: str) -> None:
        logger.info("would publish %s (%d bytes)", relative_path, path.stat().st_size)

    async def retract(self, *, relative_path: str) -> None:
        logger.info("would retract %s", relative_path)

    async def aclose(self) -> None:
        return None


class _KnowledgeFlowBoundary:
    """Writes into, and takes back out of, the library Fred gave this run.

    Both operations are addressed by the file's path within the folder, which is
    this implementation's source key. Nothing Fred assigns is ever held here.
    """

    def __init__(self, publisher: DocumentPublisher) -> None:
        self._publisher = publisher

    async def publish(self, *, relative_path: str, path: Path, version: str) -> None:
        # read_bytes blocks; the walk is already off the event loop, so keep the
        # read here rather than holding every file's content through the scan.
        await self._publisher.publish(
            relative_path=relative_path, content=path.read_bytes(), version=version
        )
        logger.info("published %s", relative_path)

    async def retract(self, *, relative_path: str) -> None:
        await self._publisher.retract(relative_path=relative_path)
        logger.info("retracted %s", relative_path)

    async def aclose(self) -> None:
        await self._publisher.aclose()


def open_boundary(*, library_id: str, source_tag: str) -> Boundary:
    """Whichever boundary this environment can support."""
    try:
        environment = PodEnvironment.from_env(require_temporal=False)
    except MissingPodEnvironment as error:
        logger.info("No Fred environment (%s): logging documents instead", error)
        return _LoggingBoundary()

    if not environment.knowledge_flow_url:
        logger.info("No Knowledge Flow URL set: logging documents instead")
        return _LoggingBoundary()

    return _KnowledgeFlowBoundary(
        DocumentPublisher(environment, library_id=library_id, source_tag=source_tag)
    )
