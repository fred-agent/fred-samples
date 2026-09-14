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
The whole difference between GitHub and GitLab.

Both speak the same Git protocol over HTTPS and differ in two details: where
the repository lives, and which user name a token is presented under. Reading
a repository through its forge's REST API instead would have made these two
separate implementations — GitLab's compare endpoint does not even return the
content identity GitHub's does.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class Provider(StrEnum):
    github = "github"
    gitlab = "gitlab"


# A token is presented as a password under a user name the forge expects. These
# are the names each documents for a personal access token over HTTPS.
_USERNAMES = {Provider.github: "x-access-token", Provider.gitlab: "oauth2"}
_HOSTS = {Provider.github: "github.com", Provider.gitlab: "gitlab.com"}


class RepositoryNameError(ValueError):
    """A repository that cannot be turned into a location to clone from."""


@dataclass(frozen=True, slots=True)
class Forge:
    """One repository on one forge, named the way that forge names it."""

    provider: Provider
    host: str
    repository: str

    @property
    def clone_url(self) -> str:
        return f"https://{self.host}/{self.repository}.git"

    @property
    def username(self) -> str:
        """Who a token is presented as. Unused when there is no token."""
        return _USERNAMES[self.provider]

    @property
    def web_url(self) -> str:
        """Where a person would look — for a log line or a run's metrics."""
        return f"https://{self.host}/{self.repository}"


def forge(*, provider: Provider, host: str = "", repository: str) -> Forge:
    """Resolve a configured repository, accepting a pasted URL as well as a name.

    Pasting the address from the browser is the likeliest thing an operator
    filling in this form will do, so it is read rather than refused.
    """
    repository = repository.strip()
    host = host.strip().rstrip("/")
    if not repository:
        raise RepositoryNameError("A repository is required.")

    if "://" in repository or repository.startswith("git@"):
        host, repository = _from_url(repository, host)

    repository = repository.strip("/")
    repository = repository.removesuffix(".git")
    _validate(provider, repository)
    return Forge(
        provider=provider, host=host or _HOSTS[provider], repository=repository
    )


def _from_url(raw: str, configured_host: str) -> tuple[str, str]:
    """Split a pasted repository address into the host and the path on it."""
    if raw.startswith("git@"):
        # scp-style: git@host:group/project.git — no SSH here, but the address
        # is recognisable and worth reading rather than rejecting.
        remainder = raw[len("git@") :]
        host, _, path = remainder.partition(":")
        return configured_host or host, path
    split = urlsplit(raw)
    if split.scheme not in ("http", "https") or not split.netloc:
        raise RepositoryNameError(f"{raw!r} is not an https repository address.")
    # A pasted address may carry a token in front of the host. Dropping it here
    # is what keeps it from reaching a log line through the repository's name.
    _, _, host = split.netloc.rpartition("@")
    return configured_host or host, split.path


def _validate(provider: Provider, repository: str) -> None:
    segments = repository.split("/")
    if any(not segment or segment in (".", "..") for segment in segments):
        raise RepositoryNameError(f"{repository!r} is not a repository path.")
    if any(character.isspace() for character in repository):
        raise RepositoryNameError("A repository path must not contain spaces.")
    if len(segments) < 2:
        raise RepositoryNameError(
            f"{repository!r} names no owner — give it as 'owner/name'."
        )
    # GitLab nests projects in groups and subgroups; GitHub never does.
    if provider is Provider.github and len(segments) > 2:
        raise RepositoryNameError(
            f"{repository!r} is not a GitHub repository — expected 'owner/name'."
        )
