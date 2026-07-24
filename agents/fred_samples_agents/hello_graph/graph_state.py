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
Input and state models for the Hello Graph sample agent.

This is the smallest state shape a v2 graph agent needs: one field for what the
user said, one field for the routing decision, one field for the answer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HelloGraphInput(BaseModel):
    """
    User message that starts one hello-graph turn.

    Example:
    ```python
    request = HelloGraphInput(message="Hi there!")
    ```
    """

    message: str = Field(..., min_length=1)


class HelloGraphState(BaseModel):
    """
    Workflow state for the hello-graph sample.

    Example:
    ```python
    state = HelloGraphState(
        latest_user_text="Hi there!",
        kind="greeting",
        final_text="Hello! Ask me anything.",
    )
    ```
    """

    latest_user_text: str

    # Set by classify_step; drives the routes= branch in the workflow.
    kind: Literal["greeting", "question"] | None = None

    # Terminal output written by greet_step or answer_step.
    final_text: str | None = None
    done_reason: str | None = None

    # Set by the runtime when a node raises and on_error routing fires.
    node_error: str = ""
