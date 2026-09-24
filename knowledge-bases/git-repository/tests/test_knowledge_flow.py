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
"""The library over the SDK counts a write only once its ingestion has landed."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from fred_sdk.knowledge_base import DocumentPublisher
from fred_sdk.knowledge_base.documents import DocumentHandle, DocumentOutcome

from fred_samples_git_kb.knowledge_flow import KnowledgeFlowLibrary, open_library
from fred_samples_git_kb.library import LibraryError, LoggingLibrary


class FakePublisher:
    def __init__(self, *, state: str = "succeeded") -> None:
        self.state = state
        self.recorded: str | None = None
        self.published: list[tuple[str, str | None]] = []

    async def publish(self, *, relative_path: str, content: bytes, version=None):
        self.published.append((relative_path, version))
        return DocumentHandle(
            task_id="t-1", document_uid="u-1", source_key=relative_path, created=True
        )

    async def wait(self, task_id: str) -> DocumentOutcome:
        return DocumentOutcome(task_id=task_id, state=self.state, error="boom")

    async def source_version(self) -> str | None:
        return self.recorded

    async def record_source_version(self, value: str) -> None:
        self.recorded = value


def library(publisher: FakePublisher) -> KnowledgeFlowLibrary:
    return KnowledgeFlowLibrary(cast(DocumentPublisher, publisher))


def test_a_landed_write_is_counted_under_its_key_and_version():
    publisher = FakePublisher()

    created = asyncio.run(
        library(publisher).write(source_key="a.md", document_version="v1", content=b"")
    )

    assert created is True
    assert publisher.published == [("a.md", "v1")]


def test_a_failed_ingestion_is_a_failed_write():
    """Otherwise the cursor would move past a document the library never got."""
    with pytest.raises(LibraryError, match="ingestion failed: boom"):
        asyncio.run(
            library(FakePublisher(state="failed")).write(
                source_key="a.md", document_version="v1", content=b""
            )
        )


def test_the_cursor_is_kept_by_fred():
    publisher = FakePublisher()

    asyncio.run(library(publisher).record_cursor("rev|digest"))

    assert asyncio.run(library(publisher).read_cursor()) == "rev|digest"


def test_without_fred_configuration_the_library_only_logs():
    assert isinstance(open_library("lib-1"), LoggingLibrary)
