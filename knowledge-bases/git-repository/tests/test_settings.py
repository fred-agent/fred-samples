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
"""What a team's form produces, read into what the run works with."""

from __future__ import annotations

import pytest

from fred_samples_git_kb.settings import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_FILES,
    ConfigurationError,
    read_settings,
)


def test_a_repository_is_all_that_is_required():
    settings = read_settings({"repository": "owner/name"})

    assert settings.forge.clone_url == "https://github.com/owner/name.git"
    assert settings.branch == ""
    assert settings.token is None
    assert settings.max_files == DEFAULT_MAX_FILES
    assert settings.max_file_bytes == DEFAULT_MAX_FILE_BYTES
    assert settings.selection.selects("README.md")


def test_a_public_repository_needs_no_token():
    settings = read_settings({"repository": "owner/name", "token": ""})

    assert settings.token is None


def test_a_cleared_bound_means_no_bound():
    settings = read_settings({"repository": "owner/name", "max_files": None})

    assert settings.max_files is None


def test_a_number_arriving_as_text_is_refused_rather_than_coerced():
    """A value that reached here unvalidated is a fault worth surfacing."""
    with pytest.raises(ConfigurationError) as refused:
        read_settings({"repository": "owner/name", "max_files": "500"})

    assert refused.value.code == "max_files_invalid"


def test_an_unknown_provider_names_the_ones_there_are():
    with pytest.raises(ConfigurationError) as refused:
        read_settings({"repository": "owner/name", "provider": "bitbucket"})

    assert refused.value.code == "provider_invalid"
    assert "github" in str(refused.value)


def test_a_bad_pattern_is_reported_against_its_own_field():
    with pytest.raises(ConfigurationError) as refused:
        read_settings({"repository": "owner/name", "include": "!docs/**"})

    assert refused.value.code == "selection_invalid"


def test_a_repository_that_is_not_text_is_refused():
    with pytest.raises(ConfigurationError) as refused:
        read_settings({"repository": 42})

    assert refused.value.code == "repository_invalid"
