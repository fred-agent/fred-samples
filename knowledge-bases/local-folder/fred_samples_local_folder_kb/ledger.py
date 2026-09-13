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
The synchronization state this implementation owns, and Fred never sees.

The contract reports counters, not state: deciding what "already synchronized"
means is the implementation's business. Here that is a JSON sidecar per
instance, mapping each relative path to what was published for it — the content
hash that detects a change, and the identifier Fred gave the document, which is
the only handle a later run has to take it back out of the library.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class LedgerEntry:
    """What a previous run published for one path.

    `document_uid` is what Fred called it, and the only handle a later run has
    to take it back out of the library. A ledger written before documents were
    really published carries none, so it is optional.
    """

    content_hash: str
    document_uid: str | None = None


type Ledger = dict[str, LedgerEntry]

# The state directory is the sample's own choice; the run context carries no
# workspace, so an override is the only way tests and demos can redirect it.
STATE_DIR_ENV = "FRED_SAMPLES_KB_STATE_DIR"

_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


class LedgerError(RuntimeError):
    """Raised when an existing ledger cannot be read back."""


def state_dir() -> Path:
    """Where this implementation keeps its ledgers, XDG-style by default."""
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "state"
    return root / "fred-samples-local-folder-kb"


def ledger_path_for(instance_id: str) -> Path:
    """One ledger per Knowledge Base instance, named after it.

    The id lands in a filename on a developer's laptop, so unsafe characters
    are folded — and since folding is lossy, a folded id keeps a digest of the
    original so two instances can never share one ledger.
    """
    safe = _UNSAFE_NAME_RE.sub("_", instance_id)
    if safe != instance_id:
        digest = hashlib.sha256(instance_id.encode("utf-8")).hexdigest()[:8]
        safe = f"{safe}-{digest}"
    return state_dir() / f"{safe}.json"


def load_ledger(path: Path) -> Ledger:
    """Read the previous run's state. A missing ledger simply means "first run"."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LedgerError(f"ledger {path} is unreadable: {error}") from error
    if not isinstance(payload, dict):
        raise LedgerError(f"ledger {path} is not a mapping of path to entry")

    ledger: Ledger = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise LedgerError(f"ledger {path} is not a mapping of path to entry")
        # A bare hash is how ledgers were written before documents reached Fred;
        # reading it keeps an existing folder from republishing wholesale.
        if isinstance(value, str):
            ledger[key] = LedgerEntry(content_hash=value)
        elif isinstance(value, dict) and isinstance(value.get("content_hash"), str):
            uid = value.get("document_uid")
            ledger[key] = LedgerEntry(
                content_hash=value["content_hash"],
                document_uid=uid if isinstance(uid, str) else None,
            )
        else:
            raise LedgerError(f"ledger {path} is not a mapping of path to entry")
    return ledger


def save_ledger(path: Path, ledger: Ledger) -> None:
    """Replace the ledger in one step, so no run ever reads a half-written one.

    The temporary file is unique: two runs of the same instance may overlap, and
    a shared temporary name would let one truncate the other's write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {key: asdict(entry) for key, entry in ledger.items()},
                stream,
                indent=2,
                sort_keys=True,
            )
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
