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
What a team fills in, read into what this implementation works with.

Values are read, never coerced: a number arriving as text is a configuration
that was not validated, and quietly accepting it hides the fault one layer
further from whoever can fix it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pathspec

from fred_samples_webdav_kb.webdav import Address, AddressError, address

# Markdown is the assumption this starts from. Taking PDFs or Office documents
# as well is this field, not a change here: Knowledge Flow converts what it is
# given, and the media type is read from the file's name.
DEFAULT_INCLUDE = "**/*.md"

DEFAULT_MAX_FILES = 2000
# Several documents are fetched at once and each is held whole before it is
# written, so this bounds a pod's memory as much as it bounds one file. The
# same figure as the sibling git sample, for the same reason.
DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024


class ConfigurationError(ValueError):
    """A configured value this implementation cannot work with."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PatternError(ValueError):
    """A pattern this implementation will not work with."""


class Selection:
    """The rule deciding whether a path on the share belongs in the library.

    The patterns are gitignore's, because that is a vocabulary anyone
    configuring this already has — and because inventing a glob dialect would
    be a worse answer than reusing the one everybody already types.
    """

    def __init__(
        self,
        *,
        include: Sequence[str] = (DEFAULT_INCLUDE,),
        exclude: Sequence[str] = (),
    ) -> None:
        self.include = tuple(include) or (DEFAULT_INCLUDE,)
        self.exclude = tuple(exclude)
        self._include = _compile(self.include, "include")
        self._exclude = _compile(self.exclude, "exclude") if self.exclude else None

    def selects(self, path: str) -> bool:
        if self._exclude is not None and self._exclude.match_file(path):
            return False
        return self._include.match_file(path)


@dataclass(frozen=True, slots=True)
class Settings:
    where: Address
    username: str
    password: str
    trust_any_certificate: bool
    selection: Selection
    max_files: int | None
    max_file_bytes: int


def read_settings(configuration: Mapping[str, object]) -> Settings:
    """Read one instance's configuration, or say which field is wrong."""
    try:
        where = address(_text(configuration, "url"))
    except AddressError as error:
        raise ConfigurationError("url_invalid", str(error)) from error

    try:
        selection = Selection(
            include=_patterns(_text(configuration, "include") or DEFAULT_INCLUDE),
            exclude=_patterns(_text(configuration, "exclude")),
        )
    except PatternError as error:
        raise ConfigurationError("selection_invalid", str(error)) from error

    username = _text(configuration, "username")
    password = _text(configuration, "password")
    if password and not username:
        raise ConfigurationError(
            "username_missing", "A password needs the user name it belongs to."
        )

    return Settings(
        where=where,
        username=username,
        password=password,
        # Never refused here: a closed network with a private certificate
        # authority is a real situation. The run reports it instead, every time,
        # in the result an operator actually reads.
        trust_any_certificate=_flag(configuration, "trust_any_certificate"),
        selection=selection,
        max_files=_bound(configuration, "max_files", DEFAULT_MAX_FILES),
        max_file_bytes=DEFAULT_MAX_FILE_BYTES,
    )


def _patterns(raw: str) -> tuple[str, ...]:
    """Read a form field holding several patterns, by comma or by line."""
    separated = raw.replace("\n", ",").split(",")
    return tuple(pattern.strip() for pattern in separated if pattern.strip())


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


def _text(configuration: Mapping[str, object], key: str) -> str:
    """Read an optional text field, which a cleared form sends as empty."""
    raw = configuration.get(key, "")
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ConfigurationError(f"{key}_invalid", f"{key} must be text, not {raw!r}")
    return raw.strip()


def _flag(configuration: Mapping[str, object], key: str) -> bool:
    raw = configuration.get(key, False)
    if raw is None or raw == "":
        return False
    if not isinstance(raw, bool):
        raise ConfigurationError(
            f"{key}_invalid", f"{key} must be true or false, not {raw!r}"
        )
    return raw


def _bound(configuration: Mapping[str, object], key: str, default: int) -> int | None:
    """Read a positive whole number, or None when an operator cleared it."""
    if key not in configuration:
        return default
    raw = configuration[key]
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ConfigurationError(
            f"{key}_invalid", f"{key} must be a positive whole number, not {raw!r}"
        )
    return raw
