"""Tools for triaging a team's documents — read-only, by design.

This capability makes **no outbound call of its own** and holds no credential.
Everything it touches comes from a port the runtime injects, where the adapter
keeps the access token private:

- ``ctx.services.document_folders``  — resolve a folder, list its documents
- ``ctx.services.document_summarize`` — summarize one document by uid
- ``ctx.services.workspace_fs``      — read the team's triage records

It deliberately never writes. Agents may read team-shared files but may only
mutate inside their own subtree (FILES-04, "agents never share"), so a
capability that wrote the shared board would need a credential of its own and
would be working around a platform boundary rather than with it.

So the division of labour is: the agent reads and *proposes*, and the person
commits the decision in the application, under their own identity. The tools
below say so in their descriptions, so the model does not claim to have saved
anything.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from fred_sdk.contracts.capability import (
    AgentCapability,
    CapabilityContext,
    CapabilityManifest,
    EmptyModel,
)
from langchain_core.tools import BaseTool, tool

logger = logging.getLogger(__name__)

DOCUMENT_TRIAGE_CAPABILITY_ID = "document_triage"

# Where the application keeps its records, relative to the team's shared space.
# The capability reads this; only a person, through the application, writes it.
_BOARD_DIR = "shared/triage"
_SUMMARY_CHARS = 1200


class DocumentTriageCapability(AgentCapability[EmptyModel, EmptyModel, EmptyModel]):
    """Read a team's document triage board and propose triage for a document."""

    manifest = CapabilityManifest(
        id=DOCUMENT_TRIAGE_CAPABILITY_ID,
        version="0.1.0",
        name="capability.document_triage.name",
        description="capability.document_triage.description",
        icon="checklist",
    )
    ConfigModel = EmptyModel

    def tools(
        self, ctx: CapabilityContext[EmptyModel, EmptyModel]
    ) -> Sequence[BaseTool]:
        services = ctx.services
        folders = getattr(services, "document_folders", None)
        summarizer = getattr(services, "document_summarize", None)
        workspace = getattr(services, "workspace_fs", None)

        if folders is None or summarizer is None:
            # Same posture as the API's fail-closed checks: contribute nothing
            # rather than half a toolset that errors at call time.
            logger.warning(
                "[DOCUMENT-TRIAGE] document ports unavailable; contributing no tools"
            )
            return ()

        async def read_board() -> dict[str, dict[str, Any]]:
            """Load the team's triage records, keyed by document name.

            A board nobody has triaged yet has no directory, which surfaces as
            an error from ``ls``. That is an empty board, not a failure.
            """

            if workspace is None:
                return {}
            try:
                entries = await workspace.ls(_BOARD_DIR)
            except Exception:
                logger.info("[DOCUMENT-TRIAGE] no triage records yet")
                return {}

            records: dict[str, dict[str, Any]] = {}
            for entry in entries:
                if getattr(entry, "is_dir", False):
                    continue
                if not entry.path.endswith(".json"):
                    continue
                try:
                    raw = await workspace.read_bytes(entry.path)
                    record = json.loads(raw)
                except Exception:
                    logger.warning("[DOCUMENT-TRIAGE] skipping %s", entry.path)
                    continue
                name = record.get("document_name") if isinstance(record, dict) else None
                if isinstance(name, str):
                    records[name] = record
            return records

        @tool
        async def list_folder_documents(folder: str) -> str:
            """List the documents in one document folder, with their triage state.

            `folder` is the folder path as the team sees it, e.g. "contracts/2026".
            """

            tag_id = await folders.resolve_folder(folder)
            if tag_id is None:
                return f"No document folder matches {folder!r}."
            documents = await folders.list_folder_documents(tag_id)
            if not documents:
                return f"Folder {folder!r} has no documents."

            board = await read_board()
            lines = []
            for document in documents:
                record = board.get(document.document_name, {})
                mark = record.get("mark", "unreviewed")
                note = record.get("note") or ""
                lines.append(
                    f"- {document.document_name} [{mark}]"
                    + (f" — {note}" if note else "")
                )
            return "\n".join(lines)

        @tool
        async def show_triage_board() -> str:
            """Show what the team has already triaged, and what they decided."""

            board = await read_board()
            if not board:
                return "Nothing has been triaged yet."
            lines = []
            for name, record in sorted(board.items()):
                mark = record.get("mark", "unreviewed")
                note = record.get("note") or ""
                at = record.get("updated_at") or ""
                lines.append(
                    f"- {name} [{mark}]"
                    + (f" — {note}" if note else "")
                    + (f" ({at})" if at else "")
                )
            return "\n".join(lines)

        @tool
        async def propose_triage(folder: str, document_name: str) -> str:
            """Read one document and propose how it should be triaged.

            This only proposes. It cannot record the decision: the person marks
            the document as reviewed or needs-work in the Document Triage
            application, so the record is written under their identity rather
            than asserted by an agent. Say so when reporting the proposal.
            """

            tag_id = await folders.resolve_folder(folder)
            if tag_id is None:
                return f"No document folder matches {folder!r}."
            documents = await folders.list_folder_documents(tag_id)
            match = next(
                (d for d in documents if d.document_name == document_name), None
            )
            if match is None:
                available = ", ".join(d.document_name for d in documents[:10])
                return (
                    f"{document_name!r} is not in {folder!r}."
                    + (f" Available: {available}" if available else "")
                )

            result = await summarizer.summarize(
                match.document_uid,
                instruction=(
                    "Summarize for a reviewer deciding whether this document is "
                    "complete and correct. Call out anything missing, stale, or "
                    "contradictory."
                ),
                max_chars=_SUMMARY_CHARS,
            )
            keywords = ", ".join(result.keywords) if result.keywords else ""
            board = await read_board()
            current = board.get(document_name, {}).get("mark", "unreviewed")
            return (
                f"{document_name} (currently {current})\n\n"
                f"{result.summary}\n"
                + (f"\nKeywords: {keywords}\n" if keywords else "")
                + "\nI cannot record this. Mark the document in the Document "
                "Triage application to save a decision."
            )

        return [list_folder_documents, show_triage_board, propose_triage]
