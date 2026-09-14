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
Which of a repository's files belong in the library.

The patterns are gitignore's, because that is the vocabulary anyone
configuring this already has in the repository being synchronized — and
because writing a fourth glob dialect would be a worse answer than reusing the
one everybody already types into `.gitignore`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import pathspec

DEFAULT_INCLUDE: tuple[str, ...] = ("**/*.md",)


class PatternError(ValueError):
    """A pattern or a subdirectory this implementation will not work with."""


class Selection:
    """The rule deciding whether a repository path belongs in the library."""

    def __init__(
        self,
        *,
        subdirectory: str = "",
        include: Sequence[str] = DEFAULT_INCLUDE,
        exclude: Sequence[str] = (),
    ) -> None:
        self.subdirectory = _directory(subdirectory)
        self.include = tuple(include) or DEFAULT_INCLUDE
        self.exclude = tuple(exclude)
        self._include = _compile(self.include, "include")
        self._exclude = _compile(self.exclude, "exclude") if self.exclude else None

    def selects(self, path: str) -> bool:
        if self.subdirectory and not path.startswith(f"{self.subdirectory}/"):
            return False
        if self._exclude is not None and self._exclude.match_file(path):
            return False
        return self._include.match_file(path)

    def library_path(self, path: str) -> str:
        """Where the document sits inside the library.

        Not the same thing as its key: the key identifies it for ever and stays
        the repository's own path, while this only locates it, so a library
        synchronizing `docs/` holds `swift/README.md` rather than repeating the
        folder it was configured with.
        """
        if self.subdirectory:
            return path[len(self.subdirectory) + 1 :]
        return path

    @property
    def digest(self) -> str:
        """A short, stable fingerprint of what this selection means.

        Carried in the cursor: widening a pattern reaches files that no diff
        between two revisions would ever mention, so a selection that changed
        has to force a full pass. Sorted, because reordering patterns changes
        no meaning here.
        """
        meaning = json.dumps(
            [self.subdirectory, sorted(self.include), sorted(self.exclude)],
            separators=(",", ":"),
        )
        return hashlib.sha256(meaning.encode()).hexdigest()[:12]


def patterns(raw: str) -> tuple[str, ...]:
    """Read a form field holding several patterns, by comma or by line."""
    separated = raw.replace("\n", ",").split(",")
    return tuple(pattern.strip() for pattern in separated if pattern.strip())


def _directory(raw: str) -> str:
    candidate = raw.strip().strip("/")
    if not candidate:
        return ""
    segments = candidate.split("/")
    if any(not segment or segment in (".", "..") for segment in segments):
        raise PatternError(f"{raw!r} is not a folder inside the repository.")
    return candidate


def _compile(values: Sequence[str], label: str) -> pathspec.PathSpec:
    for value in values:
        # A negated pattern quietly re-includes what an exclusion removed, in an
        # order nobody reading two separate fields can see. Two fields, two
        # meanings.
        if value.startswith("!"):
            raise PatternError(
                f"{label} pattern {value!r} negates; put it in the other field instead."
            )
    try:
        return pathspec.PathSpec.from_lines("gitignore", values)
    except Exception as error:
        raise PatternError(f"{label} pattern is not valid: {error}") from error
