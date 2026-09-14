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
"""The whole difference between the two forges, and the addresses people paste."""

from __future__ import annotations

import pytest

from fred_samples_git_kb.providers import Provider, RepositoryNameError, forge


def test_github_and_gitlab_differ_by_a_host_and_a_user_name():
    github = forge(provider=Provider.github, repository="ThalesGroup/fred")
    gitlab = forge(provider=Provider.gitlab, repository="group/project")

    assert github.clone_url == "https://github.com/ThalesGroup/fred.git"
    assert gitlab.clone_url == "https://gitlab.com/group/project.git"
    assert github.username == "x-access-token"
    assert gitlab.username == "oauth2"


def test_an_on_premises_host_replaces_only_the_host():
    where = forge(
        provider=Provider.gitlab, host="gitlab.internal.example", repository="a/b"
    )

    assert where.clone_url == "https://gitlab.internal.example/a/b.git"


def test_gitlab_nests_projects_in_groups_and_github_does_not():
    assert forge(provider=Provider.gitlab, repository="group/sub/project")

    with pytest.raises(RepositoryNameError):
        forge(provider=Provider.github, repository="owner/name/extra")


@pytest.mark.parametrize(
    "pasted",
    [
        "https://github.com/ThalesGroup/fred",
        "https://github.com/ThalesGroup/fred.git",
        "https://github.com/ThalesGroup/fred/",
        "git@github.com:ThalesGroup/fred.git",
    ],
)
def test_an_address_copied_from_the_browser_is_read_rather_than_refused(pasted: str):
    where = forge(provider=Provider.github, repository=pasted)

    assert where.repository == "ThalesGroup/fred"
    assert where.host == "github.com"


def test_credentials_pasted_with_an_address_do_not_survive_into_the_host():
    """Otherwise a token would reach the first log line that names the source."""
    where = forge(
        provider=Provider.github,
        repository="https://user:ghp_secret@github.com/owner/name.git",  # pragma: allowlist secret
    )

    assert where.host == "github.com"
    assert "ghp_secret" not in where.clone_url
    assert "ghp_secret" not in where.web_url


@pytest.mark.parametrize(
    "repository", ["", "   ", "name", "owner//name", "owner/../name", "owner/na me"]
)
def test_a_repository_that_names_nothing_is_refused(repository: str):
    with pytest.raises(RepositoryNameError):
        forge(provider=Provider.github, repository=repository)
