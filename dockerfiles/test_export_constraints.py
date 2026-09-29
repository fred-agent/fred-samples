"""Offline tests for the lock-preserving image build boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from export_constraints import export_constraints, local_versions, validate_source

MANIFEST = """[project]
name = "fred-samples-agents"
version = "0.1.0"
[tool.uv.sources]
fred-core = {path = "../../missing/core", editable = true}
fred-sdk = {path = "../../missing/sdk", editable = true}
"""
LOCK = """version = 1
[[package]]
name = "fred-samples-agents"
version = "0.1.0"
source = {editable = "."}
[[package]]
name = "fred-core"
version = "3.10.0"
source = {editable = "../../missing/core"}
[[package]]
name = "fred-sdk"
version = "3.6.0"
source = {editable = "../../missing/sdk"}
"""


class ExportConstraintsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.project = Path(self.directory.name)
        (self.project / "pyproject.toml").write_text(MANIFEST)
        (self.project / "uv.lock").write_text(LOCK)

    def test_export_pins_local_packages_without_reading_sibling_paths(self) -> None:
        with patch("export_constraints.subprocess.run") as run:
            run.return_value.stdout = (
                "anyio==4.15.0\npywin32==312 ; sys_platform == 'win32'\n"
            )
            result = export_constraints(self.project, "agents")
        self.assertIn("fred-core==3.10.0", result)
        self.assertIn("fred-sdk==3.6.0", result)
        self.assertIn("fred-samples-agents==0.1.0", result)
        self.assertIn("pywin32==312 ; sys_platform == 'win32'", result)
        self.assertNotIn("missing", result)
        command = run.call_args.args[0]
        self.assertIn("--frozen", command)
        self.assertIn("--offline", command)
        self.assertIn("--no-emit-local", command)
        self.assertNotIn("--prune", command)

    def test_core_export_prunes_runtime_packages_not_application_mcp_pins(self) -> None:
        with patch("export_constraints.subprocess.run") as run:
            run.return_value.stdout = "httpx==0.28.1\n"
            result = export_constraints(self.project, "core")
        self.assertIn("fred-core==3.10.0", result)
        self.assertNotIn("fred-sdk==", result)
        self.assertNotIn("fred-samples-agents==", result)
        self.assertNotIn("mcp==", result)
        self.assertEqual(run.call_args.args[0][-2:], ["--prune", "fred-sdk"])

    def test_missing_lock_fails_instead_of_resolving_again(self) -> None:
        (self.project / "uv.lock").unlink()
        with self.assertRaises(FileNotFoundError):
            local_versions(self.project)

    def test_stale_project_version_fails(self) -> None:
        (self.project / "pyproject.toml").write_text(MANIFEST.replace("0.1.0", "0.2.0"))
        with self.assertRaisesRegex(ValueError, "Stale uv.lock"):
            local_versions(self.project)

    def test_missing_declared_local_package_fails(self) -> None:
        (self.project / "uv.lock").write_text(
            LOCK.replace('name = "fred-sdk"', 'name = "other-sdk"')
        )
        with self.assertRaisesRegex(ValueError, "Local packages missing"):
            local_versions(self.project)

    def test_built_source_version_must_match_locked_version(self) -> None:
        source = self.project / "source"
        source.mkdir()
        (source / "pyproject.toml").write_text(
            '[project]\nname="fred-core"\nversion="3.9.0"\n'
        )
        with self.assertRaisesRegex(ValueError, "Stale uv.lock"):
            validate_source(source, local_versions(self.project))

    def test_built_source_matching_locked_version_is_accepted(self) -> None:
        source = self.project / "source"
        source.mkdir()
        (source / "pyproject.toml").write_text(
            '[project]\nname="fred-core"\nversion="3.10.0"\n'
        )
        validate_source(source, local_versions(self.project))


if __name__ == "__main__":
    unittest.main()
