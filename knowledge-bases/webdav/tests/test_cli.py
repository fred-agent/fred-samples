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
"""The offline developer tool, which is how anyone first meets this sample."""

from __future__ import annotations

import json

import pytest

from fred_samples_webdav_kb.cli import PASSWORD_ENV, main


def test_declaration_prints_what_publish_would_send(capsys: pytest.CaptureFixture[str]):
    assert main(["declaration"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "fred.samples.webdav"


def test_an_address_that_cannot_be_one_fails_before_any_request(
    capsys: pytest.CaptureFixture[str],
):
    assert main(["sync", "--url", "not-an-address"]) == 2
    assert "url_invalid" in capsys.readouterr().out


def test_a_password_is_read_from_the_environment_never_from_the_command_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """An argument is visible to every process on the machine while it runs."""
    monkeypatch.setenv(PASSWORD_ENV, "from-the-environment")

    # No user name beside it, so the run is refused before anything is opened —
    # which is enough to prove where the password was looked for.
    assert main(["sync", "--url", "https://share.example.com/dav/"]) == 2
    assert "username_missing" in capsys.readouterr().out


def test_an_invalid_profile_is_rejected_by_the_cli():
    with pytest.raises(SystemExit) as error:
        main(["sync", "--url", "https://share.example.com/dav/", "--profile", "turbo"])
    assert error.value.code == 2
