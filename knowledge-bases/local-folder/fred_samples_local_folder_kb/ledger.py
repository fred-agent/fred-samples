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
instance, mapping relative path to content hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

type Ledger = dict[str, str]

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
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in payload.items()
    ):
        raise LedgerError(f"ledger {path} is not a mapping of path to hash")
    return payload


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
            json.dump(ledger, stream, indent=2, sort_keys=True)
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
