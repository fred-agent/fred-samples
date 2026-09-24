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
The library this run writes into, as the synchronization needs to see it.

Not one operation names a Fred-side identifier: documents are addressed by
their path in the library, and the only thing remembered between runs is the
cursor, which Fred keeps and never reads. The implementation over Fred sits in
`knowledge_flow.py`; this port is what the synchronization is tested against.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class LibraryError(RuntimeError):
    """One document could not be written or removed. The run may continue."""


class Library(Protocol):
    """Where a run's documents go."""

    async def read_cursor(self) -> str | None:
        """What the last run recorded, or None if it never did."""
        ...

    async def record_cursor(self, value: str) -> None:
        """Store where this run got to. Kept verbatim, never interpreted."""
        ...

    async def documents(self) -> dict[str, str | None]:
        """What the library holds, by key, with the version it was written at."""
        ...

    async def write(
        self, *, source_key: str, document_version: str, content: bytes
    ) -> bool:
        """Put one document in the library. True when the key was new to it."""
        ...

    async def remove(self, *, source_key: str) -> None:
        """Take one document out. A key the library does not hold is not an error."""
        ...

    async def aclose(self) -> None:
        """Release whatever was opened to reach it."""
        ...


class LoggingLibrary:
    """Says what it would do, and remembers the cursor for the process only.

    What makes the synchronization runnable against a real repository with no
    Fred at all, which is the whole point of the developer tool beside it.
    """

    def __init__(self) -> None:
        self.cursor: str | None = None

    async def read_cursor(self) -> str | None:
        return self.cursor

    async def record_cursor(self, value: str) -> None:
        self.cursor = value
        logger.info("would record cursor %s", value)

    async def documents(self) -> dict[str, str | None]:
        # Nothing was ever written, so every full pass is a first one.
        return {}

    async def write(
        self, *, source_key: str, document_version: str, content: bytes
    ) -> bool:
        logger.info(
            "would write %s (%d bytes, version %s)",
            source_key,
            len(content),
            document_version[:8],
        )
        return True

    async def remove(self, *, source_key: str) -> None:
        logger.info("would remove %s", source_key)

    async def aclose(self) -> None:
        return None
