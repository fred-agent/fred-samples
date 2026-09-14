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
What this Knowledge Base needs from a remote share, and nothing else.

Two questions: what does the tree hold, and what are one file's bytes. Keeping
them behind a port is what lets everything that decides — the selection, the
plan, the report — be tested without a server at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RemoteFile:
    """One file as the share holds it now.

    `version` is the share's own answer to "has this changed": an entity tag
    when the server offers one, else the modification date and size together.
    It is compared for equality and never parsed — by us or by Fred.
    """

    path: str
    version: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class Inventory:
    """Everything one walk of the share saw.

    `exhaustive` is the whole reason this type exists rather than a bare list:
    only a walk that reached every collection can make an absence mean a
    deletion. A walk cut short by a bound reports what it saw and says so.
    """

    files: tuple[RemoteFile, ...] = ()
    exhaustive: bool = True


class SourceUnavailable(RuntimeError):
    """The share could not be reached, authenticated against, or read."""


class NotAWebDavCollection(SourceUnavailable):
    """The configured address answered, but not as a WebDAV collection.

    Its own error, because it is the one failure with an obvious cause and an
    obvious fix — almost always a server with WebDAV off, or a plain web page.
    """


class DocumentSource(Protocol):
    """A share this Knowledge Base can synchronize from."""

    async def inventory(self) -> Inventory:
        """Walk the tree, newest answer for every file it holds.

        Takes no bound: how many of these are documents is the selection's
        question, not the walk's.
        """
        ...

    async def read(self, file: RemoteFile) -> bytes:
        """One file's bytes."""
        ...

    async def aclose(self) -> None:
        """Release whatever was opened to reach it."""
        ...
