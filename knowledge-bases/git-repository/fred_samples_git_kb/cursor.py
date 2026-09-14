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
What this Knowledge Base remembers between runs — kept in Fred, not on disk.

Fred stores one opaque string against the library and never reads it, so its
shape belongs to this implementation. Everything needed to decide the next
run's work is in it, which is what lets a pod keep no state of its own: lose
the pod, reschedule it, run it somewhere else, and the next run still knows
exactly where the last one stopped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# What the platform accepts for a source version. Rendering stays far below it.
MAX_LENGTH = 256
FORMAT = 1


@dataclass(frozen=True, slots=True)
class Cursor:
    """The revision a library is synchronized to, and under which selection."""

    revision: str
    selection: str

    def render(self) -> str:
        rendered = json.dumps(
            {"v": FORMAT, "rev": self.revision, "sel": self.selection},
            separators=(",", ":"),
        )
        if len(rendered) > MAX_LENGTH:
            raise ValueError(f"A cursor is at most {MAX_LENGTH} characters.")
        return rendered

    @classmethod
    def parse(cls, raw: str | None) -> Cursor | None:
        """Read a stored cursor; anything unreadable reads as none at all.

        A cursor records work already done, so the cost of misreading one is a
        full pass — while refusing to run over a cursor written by an older
        version of this code would be no synchronization at all.
        """
        if not raw:
            return None
        try:
            stored = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(stored, dict) or stored.get("v") != FORMAT:
            return None
        revision, selection = stored.get("rev"), stored.get("sel")
        if not isinstance(revision, str) or not isinstance(selection, str):
            return None
        if not revision or not selection:
            return None
        return cls(revision=revision, selection=selection)
