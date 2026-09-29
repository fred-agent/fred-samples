from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import app as sample
import httpx
import pytest
import review
from fastapi import HTTPException
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from test_app import _client, _mcp_http_client

pytest_plugins = ["test_app"]

BEARER = {"authorization": "Bearer user-token"}
RESULT = {
    "decision": "Relevant document",
    "rationale": "The quoted text establishes the purpose.",
}


class FakeCorpus:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.denied: set[str] = set()
        self.text = (
            "# Project note\nThe launch requires an owner and an approved schedule."
        )
        self.tag_id = "folder-1"
        self.redirect = False
        self.file_info = {"file_type": "md", "mime_type": "text/markdown"}

    def transport(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == "Bearer user-token"
        assert request.url.host == "knowledge-flow"
        if self.redirect:
            return httpx.Response(302, headers={"location": "https://evil.test/token"})
        path = request.url.path
        if path.endswith("/tags"):
            assert request.url.params["team_id"] == "team-1"
            assert request.url.params["owner_filter"] == "team"
            return httpx.Response(
                200, json=[{"id": "folder-1", "name": "Documents", "path": "Corpus"}]
            )
        if path.endswith("/metadata/browse"):
            return httpx.Response(
                200,
                json={
                    "documents": [self.metadata("doc-1"), self.metadata("doc-2")],
                    "total": 900,
                },
            )
        uid = path.rsplit("/", 1)[-1]
        if uid in self.denied:
            return httpx.Response(403)
        if "/metadata/" in path:
            return httpx.Response(200, json=self.metadata(uid))
        if "/markdown/" in path:
            return httpx.Response(200, json={"content": self.text})
        raise AssertionError(path)

    def metadata(self, uid: str) -> dict[str, Any]:
        return {
            "identity": {"document_uid": uid, "document_name": f"{uid}.md"},
            "tags": {"tag_ids": [self.tag_id]},
            "file": self.file_info,
        }


@pytest.fixture
def corpus(configured_app: Any, monkeypatch: pytest.MonkeyPatch) -> FakeCorpus:
    fake = FakeCorpus()
    monkeypatch.setattr(
        sample, "KNOWLEDGE_FLOW", "http://knowledge-flow/knowledge-flow/v1"
    )
    sample.app.state.knowledge_client = httpx.AsyncClient(
        transport=httpx.MockTransport(fake.transport)
    )
    return fake


async def create(client: httpx.AsyncClient, uid: str = "doc-1") -> str:
    response = await client.post(
        "/teams/team-1/tasks",
        headers=BEARER,
        json={
            "title": "Review the project",
            "document": {"tag_id": "folder-1", "document_uid": uid},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["task_id"]


async def claim(
    client: httpx.AsyncClient, task_id: str, **kwargs: Any
) -> httpx.Response:
    return await client.post(
        f"/teams/team-1/tasks/{task_id}/review/claim",
        headers=BEARER,
        json={"claim_id": "execution-1", **kwargs},
    )


async def start(
    client: httpx.AsyncClient, task_id: str, run: dict[str, Any], stage: str
) -> httpx.Response:
    return await client.post(
        f"/teams/team-1/tasks/{task_id}/review/{run['run_id']}/stages/{stage}/start",
        headers=BEARER,
        json={"claim_id": run["claim_id"]},
    )


async def save(
    client: httpx.AsyncClient,
    task_id: str,
    run: dict[str, Any],
    stage: str,
    result: dict[str, Any] | None = None,
) -> httpx.Response:
    return await client.post(
        f"/teams/team-1/tasks/{task_id}/review/{run['run_id']}/stages/{stage}/result",
        headers=BEARER,
        json={"claim_id": run["claim_id"], "result": result or RESULT},
    )


@pytest.mark.asyncio
async def test_corpus_filters_forged_sources_and_forwards_only_user_bearer(
    corpus: FakeCorpus,
) -> None:
    async with _client() as client:
        folders = await client.get("/teams/team-1/folders", headers=BEARER)
        assert folders.json() == {
            "items": [{"tag_id": "folder-1", "path": "Corpus/Documents"}]
        }
        corpus.denied.add("doc-2")
        documents = await client.get(
            "/teams/team-1/documents?tag_id=folder-1", headers=BEARER
        )
        assert documents.json()["items"] == [
            {"document_uid": "doc-1", "document_name": "doc-1.md"}
        ]
        assert documents.json()["total"] is None
        forged = await client.post(
            "/teams/team-1/tasks",
            headers=BEARER,
            json={
                "title": "Forged",
                "document": {"tag_id": "foreign-folder", "document_uid": "doc-1"},
            },
        )
        assert forged.status_code == 404
        corpus.tag_id = "other-folder"
        forged = await client.post(
            "/teams/team-1/tasks",
            headers=BEARER,
            json={
                "title": "Forged",
                "document": {"tag_id": "folder-1", "document_uid": "doc-1"},
            },
        )
        assert forged.status_code == 404


@pytest.mark.asyncio
async def test_full_ordered_run_persists_real_decisions_and_statistics(
    corpus: FakeCorpus, configured_app: Any
) -> None:
    _, storage = configured_app
    async with _client() as client:
        task_id = await create(client)
        claimed = await claim(client, task_id)
        run = claimed.json()["review"]["runs"][-1]
        assert claimed.json()["progress_percent"] == 0
        assert (await start(client, task_id, run, "risk_reviewer")).status_code == 409
        assert (await save(client, task_id, run, "analyst")).status_code == 409
        for stage in review.STAGES:
            assert (await start(client, task_id, run, stage)).status_code == 200
            result = dict(RESULT)
            if stage == "risk_reviewer":
                result["findings"] = [
                    {
                        "title": "Missing owner",
                        "severity": "high",
                        "detail": "No named accountable owner",
                        "evidence": [
                            {"location": "paragraph 1", "excerpt": "requires an owner"}
                        ],
                    }
                ]
            if stage == "summary":
                result["outcome"] = "needs_attention"
            saved = await save(client, task_id, run, stage, result)
            assert saved.status_code == 200, saved.text
            context = await client.get(
                f"/teams/team-1/tasks/{task_id}/review/{run['run_id']}/context?claim_id=execution-1",
                headers=BEARER,
            )
            assert context.json()["content"] == corpus.text
            assert (
                next(
                    s for s in context.json()["run"]["stages"] if s["stage_id"] == stage
                )["result"]["decision"]
                == RESULT["decision"]
            )
        detail = (
            await client.get(f"/teams/team-1/tasks/{task_id}", headers=BEARER)
        ).json()
        assert detail["status"] == "completed"
        assert detail["progress_percent"] == 100
        assert detail["review"]["active_run_id"] is None
        stats = (await client.get("/teams/team-1/tasks", headers=BEARER)).json()[
            "statistics"
        ]
        assert stats["task_status_counts"] == {"completed": 1}
        assert stats["severity_counts"] == {"high": 1}
        assert stats["outcome_counts"] == {"needs_attention": 1}
        assert stats["completion_timeline"][0]["count"] == 1
        assert all(stage["completed_count"] == 1 for stage in stats["stage_durations"])
    stored = json.dumps(storage.documents[sample.TASKS_INDEX][task_id])
    assert corpus.text not in stored
    assert "Bearer" not in stored
    assert "source_hash" in stored and "stage_completed" in stored


def _finding(title: str, severity: str) -> dict[str, Any]:
    return {
        "title": title,
        "severity": severity,
        "detail": f"{title} needs attention",
        "evidence": [{"location": "paragraph 1", "excerpt": "requires an owner"}],
    }


@pytest.mark.asyncio
async def test_severity_counts_cover_every_specialist_and_ignore_the_summary(
    corpus: FakeCorpus, configured_app: Any
) -> None:
    """Severity counts must total the specialists, each finding exactly once.

    Analyst, risk reviewer and action planner all report findings; only the
    summary restates what they found. Counting a single stage hides most of the
    review — including, in the worst case, every `high` it raised — while
    counting the summary as well reports each restated finding twice.
    """
    reported = {
        "analyst": [_finding("Missing owner", "high"), _finding("No schedule", "low")],
        "risk_reviewer": [_finding("Unclear scope", "medium")],
        "action_planner": [_finding("Assign a reviewer", "high")],
    }
    async with _client() as client:
        task_id = await create(client)
        run = (await claim(client, task_id)).json()["review"]["runs"][-1]
        for stage in review.STAGES:
            assert (await start(client, task_id, run, stage)).status_code == 200
            result = dict(RESULT)
            if stage == "summary":
                # The summary restates the action planner verbatim.
                result["findings"] = reported["action_planner"]
                result["outcome"] = "needs_attention"
            else:
                result["findings"] = reported[stage]
            saved = await save(client, task_id, run, stage, result)
            assert saved.status_code == 200, saved.text
        stats = (await client.get("/teams/team-1/tasks", headers=BEARER)).json()[
            "statistics"
        ]

    assert stats["severity_counts"] == {"high": 2, "medium": 1, "low": 1}


@pytest.mark.asyncio
async def test_idempotency_and_no_legacy_reporter_bypass(corpus: FakeCorpus) -> None:
    async with _client() as client:
        task_id = await create(client)
        run = (await claim(client, task_id)).json()["review"]["runs"][-1]
        again = await claim(client, task_id)
        assert len(again.json()["review"]["runs"]) == 1
        assert again.json()["review"]["runs"][-1]["run_id"] == run["run_id"]
        assert (await claim(client, task_id, claim_id="execution-2")).status_code == 409
        assert (await start(client, task_id, run, "analyst")).status_code == 200
        first = await save(client, task_id, run, "analyst")
        assert (await save(client, task_id, run, "analyst")).json() == first.json()
        assert (
            await save(
                client, task_id, run, "analyst", {**RESULT, "decision": "Replacement"}
            )
        ).status_code == 409
        bypass = await client.put(
            f"/teams/team-1/tasks/{task_id}/reporters/analyst",
            headers=BEARER,
            json={"status": "completed", "progress_percent": 100},
        )
        assert bypass.status_code == 409


@pytest.mark.asyncio
async def test_revocation_hides_results_lists_statistics_and_mutations(
    corpus: FakeCorpus,
) -> None:
    async with _client() as client:
        task_id = await create(client)
        run = (await claim(client, task_id)).json()["review"]["runs"][-1]
        corpus.denied.add("doc-1")
        assert (
            await client.get(f"/teams/team-1/tasks/{task_id}", headers=BEARER)
        ).status_code == 403
        listed = (await client.get("/teams/team-1/tasks", headers=BEARER)).json()
        assert listed["items"] == []
        assert listed["statistics"]["scope"]["tasks_included"] == 0
        assert (await start(client, task_id, run, "analyst")).status_code == 403
        assert (
            await client.post(
                f"/teams/team-1/tasks/{task_id}/cancel",
                headers=BEARER,
                json={"run_id": run["run_id"]},
            )
        ).status_code == 403


@pytest.mark.asyncio
async def test_explicit_resume_preserves_completed_stages_and_fences_old_worker(
    corpus: FakeCorpus,
) -> None:
    async with _client() as client:
        task_id = await create(client)
        run = (await claim(client, task_id)).json()["review"]["runs"][-1]
        await start(client, task_id, run, "analyst")
        await save(client, task_id, run, "analyst")
        await start(client, task_id, run, "risk_reviewer")
        interrupted = await client.post(
            f"/teams/team-1/tasks/{task_id}/review/{run['run_id']}/interrupt",
            headers=BEARER,
            json={
                "claim_id": run["claim_id"],
                "error": "Model unavailable. Resume from a fresh turn.",
            },
        )
        assert interrupted.json()["status"] == "failed"
        resumed = (
            await claim(
                client,
                task_id,
                mode="resume",
                run_id=run["run_id"],
                claim_id="execution-2",
            )
        ).json()["review"]["runs"][-1]
        assert resumed["stages"][0]["status"] == "completed"
        assert resumed["stages"][1]["status"] == "queued"
        assert resumed["run_id"] == run["run_id"]
        assert (await save(client, task_id, run, "risk_reviewer")).status_code == 409
        assert (
            await start(client, task_id, resumed, "risk_reviewer")
        ).status_code == 200


@pytest.mark.asyncio
async def test_source_change_refuses_saved_decision_and_resume(
    corpus: FakeCorpus, configured_app: Any
) -> None:
    _, storage = configured_app
    async with _client() as client:
        task_id = await create(client)
        run = (await claim(client, task_id)).json()["review"]["runs"][-1]
        await start(client, task_id, run, "analyst")
        corpus.text = "New revision"
        assert (await save(client, task_id, run, "analyst")).json()[
            "detail"
        ] == "source_document_changed"
        storage.documents[sample.TASKS_INDEX][task_id]["review"]["runs"][-1][
            "status"
        ] = "failed"
        assert (
            await claim(
                client,
                task_id,
                mode="resume",
                run_id=run["run_id"],
                claim_id="execution-2",
            )
        ).json()["detail"] == "source_document_changed"
        restarted = await claim(
            client,
            task_id,
            mode="restart",
            run_id=run["run_id"],
            claim_id="execution-2",
        )
        assert restarted.status_code == 200
        assert (
            restarted.json()["review"]["runs"][-1]["source_hash"] != run["source_hash"]
        )


@pytest.mark.asyncio
async def test_stale_lease_projects_interruption_then_resumes(
    corpus: FakeCorpus, configured_app: Any
) -> None:
    _, storage = configured_app
    async with _client() as client:
        task_id = await create(client)
        run = (await claim(client, task_id)).json()["review"]["runs"][-1]
        await start(client, task_id, run, "analyst")
        storage.documents[sample.TASKS_INDEX][task_id]["review"]["runs"][-1][
            "updated_at"
        ] = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
        detail = await client.get(f"/teams/team-1/tasks/{task_id}", headers=BEARER)
        assert detail.json()["status"] == "interrupted"
        assert (await save(client, task_id, run, "analyst")).status_code == 409
        resumed = await claim(
            client, task_id, mode="resume", run_id=run["run_id"], claim_id="execution-2"
        )
        assert resumed.json()["status"] == "running"


@pytest.mark.asyncio
async def test_cancel_fences_writes_and_preserves_completed_evidence(
    corpus: FakeCorpus,
) -> None:
    async with _client() as client:
        task_id = await create(client)
        run = (await claim(client, task_id)).json()["review"]["runs"][-1]
        await start(client, task_id, run, "analyst")
        await save(client, task_id, run, "analyst")
        cancelled = await client.post(
            f"/teams/team-1/tasks/{task_id}/cancel",
            headers=BEARER,
            json={"run_id": run["run_id"]},
        )
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["review"]["runs"][-1]["stages"][0]["result"] is not None
        assert (await start(client, task_id, run, "risk_reviewer")).status_code == 409
        assert (
            await claim(
                client,
                task_id,
                mode="resume",
                run_id=run["run_id"],
                claim_id="execution-2",
            )
        ).status_code == 409


@pytest.mark.asyncio(loop_scope="session")
async def test_mcp_claim_start_and_save_use_same_bearer_boundary(
    corpus: FakeCorpus,
) -> None:
    async with _client() as client:
        task_id = await create(client)
    async with (
        _mcp_http_client(headers=BEARER) as mcp_client,
        streamable_http_client("http://sample/mcp", http_client=mcp_client) as (
            read_stream,
            write_stream,
            _,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        claimed = await session.call_tool(
            "claim_document_review",
            arguments={
                "team_id": "team-1",
                "task_id": task_id,
                "claim_id": "execution-mcp",
                "mode": "start",
            },
        )
        assert not claimed.isError
        run = json.loads(claimed.content[0].text)["review"]["runs"][-1]
        args = {
            "team_id": "team-1",
            "task_id": task_id,
            "run_id": run["run_id"],
            "claim_id": "execution-mcp",
            "stage_id": "analyst",
        }
        started = await session.call_tool("start_document_review_stage", arguments=args)
        assert not started.isError
        saved = await session.call_tool(
            "save_document_review_stage", arguments={**args, "result": RESULT}
        )
        assert not saved.isError, saved.content
        assert json.loads(saved.content[0].text)["stages"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_response_and_document_size_bounds_and_redirect_refusal(
    corpus: FakeCorpus,
) -> None:
    async with _client() as client:
        task_id = await create(client)
        corpus.text = "x" * (review.MAX_CONTENT_BYTES + 1)
        assert (await claim(client, task_id)).status_code == 413
        corpus.text = "x" * 1_000_001
        assert (await claim(client, task_id)).json()[
            "detail"
        ] == "knowledge_flow_response_too_large"
        corpus.redirect = True
        assert (
            await client.get("/teams/team-1/folders", headers=BEARER)
        ).status_code == 502
        assert all(request.url.host == "knowledge-flow" for request in corpus.requests)


@pytest.mark.asyncio
async def test_volatile_signed_urls_do_not_change_source_hash(
    corpus: FakeCorpus,
) -> None:
    async with _client() as client:
        task_id = await create(client)
        corpus.text = "Image ![](https://files.test/image.png?X-Amz-Signature=first)"
        run = (await claim(client, task_id)).json()["review"]["runs"][-1]
        corpus.text = "Image ![](https://files.test/image.png?X-Amz-Signature=second)"
        context = await client.get(
            f"/teams/team-1/tasks/{task_id}/review/{run['run_id']}/context?claim_id=execution-1",
            headers=BEARER,
        )
        assert context.status_code == 200
        assert "Signature" not in context.json()["content"]


@pytest.mark.asyncio
async def test_compare_and_swap_does_not_overwrite_concurrent_state(
    corpus: FakeCorpus, configured_app: Any
) -> None:
    _, storage = configured_app
    async with _client() as client:
        task_id = await create(client)
    version = await review.load_version(storage, sample.TASKS_INDEX, task_id)
    winner = dict(version["_source"], title="Winner")
    await review.save_version(storage, sample.TASKS_INDEX, task_id, winner, version)
    with pytest.raises(HTTPException) as error:
        await review.save_version(
            storage, sample.TASKS_INDEX, task_id, dict(winner, title="Loser"), version
        )
    assert error.value.status_code == 409
    assert storage.documents[sample.TASKS_INDEX][task_id]["title"] == "Winner"


@pytest.mark.asyncio
async def test_additive_mapping_update_preserves_legacy_index() -> None:
    calls: list[tuple[str, Any]] = []

    class Indices:
        async def exists(self, **kwargs: Any) -> bool:
            return True

        async def put_mapping(self, **kwargs: Any) -> None:
            calls.append((kwargs["index"], kwargs["body"]))

    class Search:
        indices = Indices()

    await sample._ensure_indices(Search())
    assert calls == [
        (
            sample.TASKS_INDEX,
            {
                "properties": {
                    "document": {"type": "object", "enabled": False},
                    "review": {"type": "object", "enabled": False},
                }
            },
        )
    ]


@pytest.mark.asyncio
async def test_mcp_grant_checked_before_review_storage(
    corpus: FakeCorpus, configured_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rebac, storage = configured_app
    async with _client() as client:
        task_id = await create(client)

        async def denied(*args: Any, **kwargs: Any) -> None:
            raise HTTPException(403, "mcp_not_granted")

        monkeypatch.setattr(rebac, "check_team_capability", denied)
        corpus.requests.clear()
        denied_response = await claim(client, task_id)
        assert denied_response.status_code == 403
        assert corpus.requests == []
        assert storage.documents[sample.TASKS_INDEX][task_id]["review"]["runs"] == []


@pytest.mark.asyncio
async def test_completed_review_is_immutable_and_restart_requires_latest_reference(
    corpus: FakeCorpus,
) -> None:
    async with _client() as client:
        task_id = await create(client)
        run = (await claim(client, task_id)).json()["review"]["runs"][-1]
        for stage in review.STAGES:
            await start(client, task_id, run, stage)
            await save(
                client,
                task_id,
                run,
                stage,
                {**RESULT, "outcome": "accepted"} if stage == "summary" else None,
            )
        assert (
            await client.post(
                f"/teams/team-1/tasks/{task_id}/cancel",
                headers=BEARER,
                json={"run_id": run["run_id"]},
            )
        ).status_code == 409
        assert (await claim(client, task_id, claim_id="execution-2")).status_code == 409
        assert (
            await claim(
                client,
                task_id,
                claim_id="execution-2",
                mode="restart",
                run_id="run-" + "0" * 32,
            )
        ).status_code == 409
        assert (
            await claim(
                client,
                task_id,
                claim_id="execution-2",
                mode="restart",
                run_id=run["run_id"],
            )
        ).status_code == 200
        assert (
            await save(
                client, task_id, run, "summary", {**RESULT, "outcome": "accepted"}
            )
        ).status_code == 409


@pytest.mark.asyncio
async def test_knowledge_flow_401_is_not_hidden_as_empty_results(
    corpus: FakeCorpus,
) -> None:
    async with _client() as client:
        await create(client)
        sample.app.state.knowledge_client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(401))
        )
        response = await client.get("/teams/team-1/tasks", headers=BEARER)
        assert response.status_code == 401
        assert response.json()["detail"] == "knowledge_flow_user_token_expired"


@pytest.mark.asyncio
async def test_one_listing_carries_team_statistics_after_one_folder_lookup(
    corpus: FakeCorpus,
) -> None:
    """A dashboard poll is one listing, and each Knowledge Flow folder lookup
    fans out into several authorization queries there."""
    async with _client() as client:
        for number in range(3):
            await create(client, f"doc-{number}")
        legacy = await client.post(
            "/teams/team-1/tasks", headers=BEARER, json={"title": "Legacy reporter"}
        )
        assert legacy.status_code == 201
        corpus.requests.clear()
        listed = await client.get("/teams/team-1/tasks", headers=BEARER)

    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 4
    statistics = listed.json()["statistics"]
    assert statistics["scope"]["tasks_included"] == 3
    assert statistics["task_status_counts"] == {"queued": 3}
    folder_lookups = [r for r in corpus.requests if r.url.path.endswith("/tags")]
    assert len(folder_lookups) == 1


@pytest.mark.asyncio
async def test_listed_task_names_the_run_its_status_describes(
    corpus: FakeCorpus,
) -> None:
    """Two completed runs share status and progress; the run id tells them apart."""

    async def complete(client: httpx.AsyncClient, task_id: str, run: Any) -> Any:
        for stage in review.STAGES:
            await start(client, task_id, run, stage)
            outcome = {"outcome": "accepted"} if stage == "summary" else {}
            saved = await save(client, task_id, run, stage, {**RESULT, **outcome})
            assert saved.status_code == 200, saved.text
        listed = await client.get("/teams/team-1/tasks", headers=BEARER)
        return listed.json()["items"][0]

    async with _client() as client:
        task_id = await create(client)
        unclaimed = (await client.get("/teams/team-1/tasks", headers=BEARER)).json()
        first = (await claim(client, task_id)).json()["review"]["runs"][-1]
        before = await complete(client, task_id, first)
        restarted = await claim(
            client,
            task_id,
            claim_id="execution-2",
            mode="restart",
            run_id=first["run_id"],
        )
        second = restarted.json()["review"]["runs"][-1]
        after = await complete(client, task_id, second)
        detail = (
            await client.get(f"/teams/team-1/tasks/{task_id}", headers=BEARER)
        ).json()

    assert unclaimed["items"][0]["run_id"] is None
    assert (before["status"], before["progress_percent"]) == ("completed", 100)
    assert (after["status"], after["progress_percent"]) == ("completed", 100)
    assert before["run_id"] == first["run_id"]
    assert after["run_id"] == second["run_id"] != first["run_id"]
    assert detail["run_id"] == after["run_id"]


@pytest.mark.asyncio
async def test_access_checks_are_deduplicated_and_bounded(
    corpus: FakeCorpus, configured_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client() as client:
        for number in range(17):
            await create(client, f"doc-{number}")
        await create(client, "doc-1")
    active = high_water = checked = 0
    original = review.Corpus.document

    async def delayed(
        self: review.Corpus, team_id: str, document: review.DocumentSelection
    ) -> review.DocumentReference:
        nonlocal active, high_water, checked
        active += 1
        checked += 1
        high_water = max(high_water, active)
        await asyncio.sleep(0)
        try:
            return await original(self, team_id, document)
        finally:
            active -= 1

    monkeypatch.setattr(review.Corpus, "document", delayed)
    async with _client() as client:
        listed = await client.get("/teams/team-1/tasks", headers=BEARER)
    assert len(listed.json()["items"]) == 18
    assert checked == 17
    assert 1 < high_water <= 8


@pytest.mark.asyncio
async def test_meaningful_source_link_query_changes_are_not_normalized(
    corpus: FakeCorpus,
) -> None:
    async with _client() as client:
        task_id = await create(client)
        corpus.text = "Reference https://example.test/document?revision=1"
        run = (await claim(client, task_id)).json()["review"]["runs"][-1]
        corpus.text = "Reference https://example.test/document?revision=2"
        changed = await client.get(
            f"/teams/team-1/tasks/{task_id}/review/{run['run_id']}/context?claim_id=execution-1",
            headers=BEARER,
        )
        assert changed.status_code == 409


@pytest.mark.asyncio
async def test_signed_media_version_changes_are_not_hidden(corpus: FakeCorpus) -> None:
    async with _client() as client:
        task_id = await create(client)
        corpus.text = (
            '<img src="https://files.test/image.png?versionId=1&X-Amz-Signature=first">'
        )
        run = (await claim(client, task_id)).json()["review"]["runs"][-1]
        context = await client.get(
            f"/teams/team-1/tasks/{task_id}/review/{run['run_id']}/context?claim_id=execution-1",
            headers=BEARER,
        )
        assert (
            context.json()["content"]
            == '<img src="https://files.test/image.png?versionId=1">'
        )
        corpus.text = '<img src="https://files.test/image.png?versionId=2&X-Amz-Signature=second">'
        changed = await client.get(
            f"/teams/team-1/tasks/{task_id}/review/{run['run_id']}/context?claim_id=execution-1",
            headers=BEARER,
        )
        assert changed.status_code == 409


@pytest.mark.asyncio
async def test_cross_team_lookup_does_not_call_corpus(corpus: FakeCorpus) -> None:
    async with _client() as client:
        task_id = await create(client)
        corpus.requests.clear()
        response = await client.get(f"/teams/team-2/tasks/{task_id}", headers=BEARER)
        assert response.status_code == 404
        assert corpus.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "file_info",
    [
        {"file_type": "csv", "mime_type": "text/plain", "row_count": 1000},
        {"file_type": "other", "mime_type": "text/csv", "row_count": 1000},
        {
            "file_type": "xlsx",
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
    ],
)
async def test_tabular_preview_cannot_be_reviewed_as_complete_document(
    corpus: FakeCorpus, configured_app: Any, file_info: dict[str, Any]
) -> None:
    _, storage = configured_app
    async with _client() as client:
        existing_id = await create(client)
        corpus.file_info = file_info
        corpus.text = "| row | value |\n| 1 | only an incomplete preview |"
        response = await client.post(
            "/teams/team-1/tasks",
            headers=BEARER,
            json={
                "title": "Large table",
                "document": {"tag_id": "folder-1", "document_uid": "doc-2"},
            },
        )
        assert response.status_code == 422
        assert (
            response.json()["detail"]
            == "source_format_not_supported_for_complete_review"
        )
        assert (await claim(client, existing_id)).status_code == 422
        documents = await client.get(
            "/teams/team-1/documents?tag_id=folder-1", headers=BEARER
        )
        assert documents.json()["items"] == []
        assert not any("/markdown/" in request.url.path for request in corpus.requests)
        assert (
            storage.documents[sample.TASKS_INDEX][existing_id]["review"]["runs"] == []
        )


@pytest.mark.asyncio
async def test_a_delegated_corpus_call_carries_the_person_beside_its_own_bearer() -> (
    None
):
    """Under delegation the bearer is this service's own, not the person's.

    The receiver therefore learns who to act for only from the grant, and it
    has to reach every call without displacing the query that call already
    carries: `params` replaces a URL query rather than merging with it.
    """
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    grant = {"person": "person-1", "run": "run-1", "agent": "doc-review"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        delegated = review.Corpus(
            "http://knowledge-flow/knowledge-flow/v1", client, "Bearer workload", grant
        )
        await delegated.request("GET", "/tags", params={"team_id": "t1", "limit": 10})
        carried = seen[-1].url.params
        assert carried["person"] == "person-1"
        assert carried["run"] == "run-1"
        assert carried["agent"] == "doc-review"
        # The call's own query survives the grant travelling with it.
        assert carried["team_id"] == "t1" and carried["limit"] == "10"

        # Without a grant the caller acts for itself and nothing is added.
        plain = review.Corpus(
            "http://knowledge-flow/knowledge-flow/v1", client, "Bearer user-token"
        )
        await plain.request("GET", "/tags", params={"team_id": "t1"})
        assert "person" not in seen[-1].url.params


async def _one_completed_run(client: httpx.AsyncClient, task_id: str) -> str:
    """Drive a task through every stage and return the finished run id."""
    run = (await claim(client, task_id)).json()["review"]["runs"][-1]
    for stage in review.STAGES:
        await start(client, task_id, run, stage)
        result = dict(RESULT)
        if stage == "analyst":
            result["findings"] = [
                {
                    "title": "Missing owner",
                    "severity": "high",
                    "detail": "No named accountable owner",
                    "evidence": [{"location": "p1", "excerpt": "requires an owner"}],
                }
            ]
            result["actions"] = [
                {"title": "Name an owner", "priority": "high", "detail": "Assign one"}
            ]
        if stage == "summary":
            # The summary restates the specialists, so its findings must not add up.
            result["outcome"] = "needs_attention"
            result["findings"] = [
                {
                    "title": "Restated",
                    "severity": "critical",
                    "detail": "Echo of the analyst finding",
                    "evidence": [{"location": "p1", "excerpt": "requires an owner"}],
                }
            ]
        await save(client, task_id, run, stage, result)
    return str(run["run_id"])


@pytest.mark.asyncio
async def test_a_runs_own_numbers_count_its_specialists_and_not_its_summary(
    corpus: FakeCorpus,
) -> None:
    async with _client() as client:
        task_id = await create(client)
        run_id = await _one_completed_run(client, task_id)
        stats = (
            await client.get(
                f"/teams/team-1/tasks/{task_id}/review/{run_id}/statistics",
                headers=BEARER,
            )
        ).json()

    assert stats["scope"]["run_id"] == run_id
    assert stats["scope"]["status"] == "completed"
    assert stats["scope"]["completed_stages"] == len(review.STAGES)
    # The summary's restated `critical` finding is not a second discovery.
    assert stats["severity_counts"] == {"high": 1}
    assert stats["action_priority_counts"] == {"high": 1}
    assert stats["outcome"] == "needs_attention"


def test_an_earlier_run_is_readable_after_a_later_one_replaces_it() -> None:
    """Selecting a run is a read, so it is not bound to the live claim.

    `require_run` refuses anything but the latest run under its own claim,
    which is right for writing and useless for looking at history.
    """

    def run(run_id: str, severity: str | None) -> review.ReviewRun:
        stages = [review.ReviewStage(stage_id=s) for s in review.STAGES]
        if severity is not None:
            stages[0].status = "completed"
            stages[0].result = review.StageResult(
                decision="Relevant document",
                rationale="The quoted text establishes the purpose.",
                findings=[
                    review.Finding(
                        title="Missing owner",
                        severity=severity,
                        detail="No named accountable owner",
                        evidence=[
                            review.Evidence(location="p1", excerpt="requires an owner")
                        ],
                    )
                ],
            )
        return review.ReviewRun(
            run_id=run_id,
            claim_id="execution-1",
            source_hash="hash",
            started_at=review.now(),
            updated_at=review.now(),
            reported_by="someone",
            status="completed" if severity else "running",
            stages=stages,
        )

    earlier, latest = run("run-" + "a" * 32, "high"), run("run-" + "b" * 32, None)
    state = review.ReviewState(runs=[earlier, latest])

    # The earlier run keeps its own numbers although a newer run exists.
    assert review.run_statistics(review.find_run(state, earlier.run_id))[
        "severity_counts"
    ] == {"high": 1}
    assert (
        review.run_statistics(review.find_run(state, latest.run_id))["severity_counts"]
        == {}
    )


@pytest.mark.asyncio
async def test_an_unknown_run_is_not_found_rather_than_an_empty_report(
    corpus: FakeCorpus,
) -> None:
    async with _client() as client:
        task_id = await create(client)
        response = await client.get(
            f"/teams/team-1/tasks/{task_id}/review/run-{'0' * 32}/statistics",
            headers=BEARER,
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "review_run_not_found"


def test_a_heartbeat_keeps_a_working_run_alive_without_rewriting_its_history() -> None:
    """The lease is short, so a silent step has to say it is still working.

    Recording each beat would be worse than the problem: run events are capped,
    so the beats would evict the audit trail they were meant to sit beside.
    """
    stages = [review.ReviewStage(stage_id=s) for s in review.STAGES]
    run = review.ReviewRun(
        run_id="run-" + "a" * 32,
        claim_id="execution-1",
        source_hash="hash",
        started_at=review.now(),
        updated_at=(
            datetime.now(UTC) - timedelta(seconds=review.LEASE_SECONDS - 20)
        ).isoformat(),
        reported_by="someone",
        status="running",
        stages=stages,
    )
    state = review.ReviewState(runs=[run], active_run_id=run.run_id)
    was = run.updated_at
    events_before = len(run.events)

    review.touch_run(state, run.run_id, "execution-1")

    assert run.updated_at > was
    assert not review.is_stale(run)
    # Beats are not history: the capped event list is left alone.
    assert len(run.events) == events_before


def test_only_the_claim_holder_may_keep_a_run_alive() -> None:
    """Otherwise anyone could hold an abandoned review open indefinitely."""
    stages = [review.ReviewStage(stage_id=s) for s in review.STAGES]
    run = review.ReviewRun(
        run_id="run-" + "a" * 32,
        claim_id="execution-1",
        source_hash="hash",
        started_at=review.now(),
        updated_at=datetime.now(UTC).isoformat(),
        reported_by="someone",
        status="running",
        stages=stages,
    )
    state = review.ReviewState(runs=[run], active_run_id=run.run_id)
    was = run.updated_at

    with pytest.raises(HTTPException) as refused:
        review.touch_run(state, run.run_id, "someone-elses-claim")

    assert refused.value.status_code == 409
    assert run.updated_at == was


def test_the_lease_outlives_a_heartbeat_but_not_many() -> None:
    """A lease shorter than the beat interval would expire under a live holder."""
    assert review.LEASE_SECONDS > 30
