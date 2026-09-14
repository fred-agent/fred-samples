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

from collections.abc import Mapping
from dataclasses import dataclass

from fred_samples_git_kb.matching import (
    DEFAULT_INCLUDE,
    PatternError,
    Selection,
    patterns,
)
from fred_samples_git_kb.providers import Forge, Provider, RepositoryNameError, forge

DEFAULT_MAX_FILES = 2000
DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024


class ConfigurationError(ValueError):
    """A configured value this implementation cannot work with."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Settings:
    forge: Forge
    branch: str
    token: str | None
    selection: Selection
    max_files: int | None
    max_file_bytes: int


def read_settings(configuration: Mapping[str, object]) -> Settings:
    """Read one instance's configuration, or say which field is wrong."""
    provider = _provider(configuration.get("provider", Provider.github.value))
    try:
        where = forge(
            provider=provider,
            host=_text(configuration, "host"),
            repository=_text(configuration, "repository"),
        )
    except RepositoryNameError as error:
        raise ConfigurationError("repository_invalid", str(error)) from error

    try:
        selection = Selection(
            subdirectory=_text(configuration, "subdirectory"),
            include=patterns(_text(configuration, "include") or DEFAULT_INCLUDE[0]),
            exclude=patterns(_text(configuration, "exclude")),
        )
    except PatternError as error:
        raise ConfigurationError("selection_invalid", str(error)) from error

    return Settings(
        forge=where,
        # Empty means the branch the repository itself considers default, which
        # is what an operator who did not think about it meant.
        branch=_text(configuration, "branch"),
        token=_text(configuration, "token") or None,
        selection=selection,
        max_files=_bound(configuration, "max_files", DEFAULT_MAX_FILES),
        max_file_bytes=_bound(configuration, "max_file_bytes", DEFAULT_MAX_FILE_BYTES)
        or DEFAULT_MAX_FILE_BYTES,
    )


def _provider(raw: object) -> Provider:
    # A field cleared in a form arrives empty rather than absent, and a cleared
    # choice means the one it was offered with, not a broken instance.
    if raw is None or str(raw).strip() == "":
        return Provider.github
    try:
        return Provider(str(raw).strip())
    except ValueError as error:
        offered = ", ".join(member.value for member in Provider)
        raise ConfigurationError(
            "provider_invalid", f"{raw!r} is not one of: {offered}"
        ) from error


def _text(configuration: Mapping[str, object], key: str) -> str:
    """Read an optional text field, which a cleared form sends as empty."""
    raw = configuration.get(key, "")
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ConfigurationError(f"{key}_invalid", f"{key} must be text, not {raw!r}")
    return raw.strip()


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
