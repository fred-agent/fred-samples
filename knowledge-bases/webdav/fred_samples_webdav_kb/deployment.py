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
What the operator deploying this pod decides, and how the pod stops.

Two kinds of setting, kept apart on purpose. What a *team* chooses — which
share, which files, which profile — is a field in its form and is read in
`settings.py`. What the *operator* decides — how hard this pod leans on a share
and on Fred's ingestion — is the same for every team the pod serves, is
reviewed with the rest of the deployment, and so lives in `configuration.yaml`
under this sample's own `webdav:` section, parsed by an extension of the SDK's
own model and loaded the SDK's own way.
"""

from __future__ import annotations

import logging
import signal
from types import FrameType
from typing import cast

from fred_sdk.knowledge_base import MissingPodConfiguration
from fred_sdk.knowledge_base.configuration import PodConfiguration
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class WebDavDeployment(BaseModel):
    """The `webdav:` section of `configuration.yaml`. Every key is optional."""

    # A misspelt key is refused rather than ignored: an operator who wrote
    # `concurency: 16` believes it applies, and a default silently kept instead
    # is the one outcome nobody would notice.
    model_config = ConfigDict(extra="forbid")

    concurrency: int = Field(
        default=4,
        ge=1,
        le=64,
        description=(
            "Documents one run fetches and hands to Fred at once. Each is held "
            "whole in memory while it is written, so this times the 10 MiB file "
            "bound is a run's memory ceiling. Raise it for a faster first run, "
            "as far as Knowledge Flow's ingestion keeps up."
        ),
    )
    ingestion_wait_seconds: int = Field(
        default=600,
        ge=1,
        description=(
            "How long a run follows one document's ingestion. Past it the "
            "document is reported as still ingesting, not as failed: Fred keeps "
            "working on it, and the next run finds it and does not write it again."
        ),
    )


class WebDavPodConfiguration(PodConfiguration):
    """The SDK's pod configuration, plus this sample's own section."""

    webdav: WebDavDeployment = Field(default_factory=WebDavDeployment)


def load_deployment() -> WebDavPodConfiguration | None:
    """This pod's configuration, or None where there is no Fred at all.

    An invalid file stops the process with the SDK's banner, which is why the
    entry point calls this once before serving anything: a wrong value is then
    the pod that does not start, not a run that dies halfway.
    """
    try:
        # The SDK's loader builds whichever class it is called on; only its
        # annotation names the base class.
        return cast(WebDavPodConfiguration, WebDavPodConfiguration.load())
    except MissingPodConfiguration as error:
        logger.info("No Fred configuration (%s): deployment defaults apply", error)
        return None


def stop_on_sigterm() -> None:
    """Make the orchestrator's SIGTERM stop the pod the way Ctrl-C does.

    Python installs no SIGTERM handler, and a container's PID 1 ignores any
    signal it has no handler for — so without this, Kubernetes waits out its
    grace period and kills the pod outright. The run it was serving is then
    still "running" as far as the workflow engine knows, until its activity
    timeout of several hours expires, and the schedule skips every occurrence
    in between.

    Ctrl-C is the stop the engine already handles: `asyncio.run` cancels the
    worker, the worker reports the interrupted run, and the engine hands its
    next attempt to another pod at once.
    """
    signal.signal(signal.SIGTERM, _as_ctrl_c)


def _as_ctrl_c(_signum: int, _frame: FrameType | None) -> None:
    signal.raise_signal(signal.SIGINT)


__all__ = [
    "WebDavDeployment",
    "WebDavPodConfiguration",
    "load_deployment",
    "stop_on_sigterm",
]
