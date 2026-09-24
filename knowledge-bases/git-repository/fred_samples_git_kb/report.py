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
What a pass actually did, and whether the cursor has earned the right to move.

This is the implementation's own vocabulary, deliberately not Fred's. Fred's
result shape is one translation away, in the layer that talks to it, so a
change on either side is a change to one file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fred_samples_git_kb.plan import PassKind

# Past this many, an operator is reading a failure, not a list. The platform
# bounds what it stores too; stopping here keeps the two from disagreeing.
MAX_ISSUES = 50


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    message: str = ""
    subject: str | None = None


@dataclass
class RunReport:
    """Counts what happened, in the terms this implementation can prove."""

    pass_kind: PassKind
    revision: str
    base: str | None = None
    exhaustive: bool = False
    considered: int = 0
    written_new: int = 0
    written_existing: int = 0
    retracted: int = 0
    unchanged: int = 0
    skipped: int = 0
    warnings: list[Issue] = field(default_factory=list)
    errors: list[Issue] = field(default_factory=list)

    def wrote(self, *, created: bool) -> None:
        if created:
            self.written_new += 1
        else:
            self.written_existing += 1

    def removed(self) -> None:
        self.retracted += 1

    def skip(self, source_key: str, reason: str, message: str = "") -> None:
        """One document this run will not carry, and why. Never an error.

        A skip leaves whatever the library already held: a limit of ours is not
        a statement about the repository.
        """
        self.skipped += 1
        self.warn(reason, message, subject=source_key)

    def warn(self, code: str, message: str = "", subject: str | None = None) -> None:
        _add(self.warnings, Issue(code, message, subject))

    def fail(self, code: str, message: str = "", subject: str | None = None) -> None:
        _add(self.errors, Issue(code, message, subject))

    @property
    def succeeded(self) -> bool:
        return not self.errors

    @property
    def may_advance_cursor(self) -> bool:
        """Whether the run earned the right to say where it got to.

        A cursor moved past a document this run failed to write would claim a
        state the library does not have, and no later run would ever look at
        that document again. Standing still costs a repeated pass; moving on
        costs a document nobody notices is missing.
        """
        return self.succeeded

    def summary(self) -> str:
        parts = [
            f"{self.pass_kind.value} pass at {self.revision[:8]}",
            f"{self.considered} considered",
            f"{self.written_new} created",
            f"{self.written_existing} updated",
            f"{self.retracted} removed",
        ]
        if self.unchanged:
            parts.append(f"{self.unchanged} unchanged")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        if self.errors:
            parts.append(f"{len(self.errors)} failed")
        return ", ".join(parts)

    def metrics(self) -> dict[str, object]:
        return {
            "pass": self.pass_kind.value,
            "revision": self.revision,
            "base": self.base,
            "skipped": self.skipped,
            "cursor_advanced": self.may_advance_cursor,
        }


def _add(issues: list[Issue], issue: Issue) -> None:
    """Stop collecting at the bound, and say once that collecting stopped."""
    if len(issues) < MAX_ISSUES:
        issues.append(issue)
    elif len(issues) == MAX_ISSUES:
        issues.append(Issue("issues_truncated", f"more than {MAX_ISSUES} reported"))
