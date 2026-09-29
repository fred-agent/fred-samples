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
What the operator decides, and how the pod stops.

The settings are read from the same file, by the same loader, as the rest of
the pod's configuration — so these tests go through that file rather than
around it. The stop is tested for the one property that matters: SIGTERM ends
a running pod by cancelling it, which is what lets the workflow engine hand the
interrupted run to another pod rather than wait hours for it.
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from fred_samples_webdav_kb.deployment import (
    WebDavDeployment,
    WebDavPodConfiguration,
    load_deployment,
    stop_on_sigterm,
)

SHIPPED = Path(__file__).resolve().parent.parent / "config" / "configuration.yaml"


def test_the_shipped_configuration_states_the_defaults_it_documents():
    """The file an operator copies must parse, and say what the code does."""
    pod = WebDavPodConfiguration.model_validate(_yaml(SHIPPED.read_text()))

    assert pod.webdav == WebDavDeployment()


def test_a_configuration_without_the_section_takes_the_defaults():
    body = _without_webdav(SHIPPED.read_text())
    pod = WebDavPodConfiguration.model_validate(_yaml(body))

    assert pod.webdav == WebDavDeployment()


def test_the_section_is_read_through_the_pod_s_own_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = tmp_path / "configuration.yaml"
    config.write_text(
        _without_webdav(SHIPPED.read_text())
        + "\nwebdav:\n  concurrency: 12\n  ingestion_wait_seconds: 1800\n"
    )
    monkeypatch.setenv("CONFIG_FILE", str(config))

    pod = load_deployment()

    assert pod is not None
    assert pod.webdav.concurrency == 12
    assert pod.webdav.ingestion_wait_seconds == 1800


def test_no_configuration_at_all_is_a_developer_tool_not_an_error():
    # conftest points $CONFIG_FILE at a path that does not exist.
    assert load_deployment() is None


@pytest.mark.parametrize(
    "section",
    [
        {"concurency": 12},  # misspelt: refused rather than silently ignored
        {"concurrency": 0},
        {"concurrency": 65},
        {"ingestion_wait_seconds": 0},
        {"concurrency": "many"},
    ],
)
def test_a_value_the_operator_got_wrong_is_refused(section: dict[str, object]):
    with pytest.raises(ValidationError):
        WebDavDeployment.model_validate(section)


def test_sigterm_stops_a_running_pod_the_way_ctrl_c_does():
    """Cancelled, not killed: the worker gets to hand its run back."""
    previous = signal.getsignal(signal.SIGTERM)
    cancelled: list[bool] = []

    async def serve() -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    stop_on_sigterm()
    try:
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(serve())
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert cancelled == [True]


def _yaml(text: str) -> dict[str, object]:
    return yaml.safe_load(text)


def _without_webdav(text: str) -> str:
    return text.split("\nwebdav:", 1)[0]
