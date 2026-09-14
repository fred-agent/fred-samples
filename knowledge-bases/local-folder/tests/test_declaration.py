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
Offline tests for what the image publishes: the Knowledge Base and the
declaration projected from it.

None of these touch the filesystem beyond the package itself — the declaration
is a pure projection, which is exactly why it is safe to publish.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fred_sdk.knowledge_base import (
    KnowledgeBase,
    KnowledgeBaseDeclaration,
    KnowledgeBaseDeclarationError,
)

from fred_samples_local_folder_kb.knowledge_base import DEFAULT_GLOB, kb, synchronize


def test_declaration_is_valid():
    assert kb.id == "fred.samples.local-folder"
    assert kb.version and kb.name and kb.description

    fields = {field.key: field for field in kb.configuration_fields}
    assert set(fields) == {"root_path", "glob", "max_files"}
    assert fields["root_path"].required is True
    assert fields["glob"].default == DEFAULT_GLOB
    assert fields["max_files"].type == "integer"


def test_malformed_id_is_rejected():
    with pytest.raises(KnowledgeBaseDeclarationError):
        KnowledgeBase(
            id="local folder!",
            version="1.0.0",
            name="Local folder",
            description="Malformed identifier.",
        )


def test_the_handler_is_registered_and_resolvable():
    assert kb.resolve_handler() is synchronize
    assert asyncio.iscoroutinefunction(synchronize)


def test_resolving_without_a_handler_fails_clearly():
    undeclared = KnowledgeBase(
        id="fred.samples.no-handler",
        version="1.0.0",
        name="No handler",
        description="Declares nothing to run.",
    )
    with pytest.raises(KnowledgeBaseDeclarationError, match="no synchronization"):
        undeclared.resolve_handler()


def test_the_declaration_round_trips_through_json():
    declaration = KnowledgeBaseDeclaration.of(kb)
    payload = declaration.model_dump(mode="json")

    restored = KnowledgeBaseDeclaration.model_validate(json.loads(json.dumps(payload)))

    assert restored == declaration


def test_the_declaration_carries_no_configured_value():
    payload = KnowledgeBaseDeclaration.of(kb).model_dump(mode="json")

    assert set(payload) == {
        "id",
        "version",
        "name",
        "description",
        "configuration_fields",
    }
    # A declared default is part of the artifact; an instance's value never is,
    # so the projected fields must carry exactly what the declaration holds.
    assert {
        field["key"]: field["default"] for field in payload["configuration_fields"]
    } == {field.key: field.default for field in kb.configuration_fields}
    for field in payload["configuration_fields"]:
        assert "value" not in field
        assert field["type"] != "secret"


def test_the_published_payload_is_compact_and_round_trips():
    declaration = KnowledgeBaseDeclaration.of(kb)

    payload = declaration.to_payload()

    assert KnowledgeBaseDeclaration.model_validate(payload) == declaration
    # Only what the author declared survives: no defaulted UI hints, no nulls.
    for field in payload["configuration_fields"]:
        assert "ui" not in field
        assert None not in field.values()
