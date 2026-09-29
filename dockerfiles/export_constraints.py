"""Export tested lock constraints without requiring host-only editable paths.

Third-party versions and platform markers come from uv's frozen export. Local
packages are installed separately (published artifacts or explicitly built
wheels), but their versions remain constrained to the same lock file.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import Any

import tomllib


def metadata(path: Path) -> dict[str, Any]:
    with path.open("rb") as source:
        return tomllib.load(source)


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def local_versions(project: Path) -> dict[str, str]:
    manifest = metadata(project / "pyproject.toml")
    lock = metadata(project / "uv.lock")
    versions: dict[str, str] = {}
    for package in lock.get("package", []):
        source = package.get("source", {})
        if not any(key in source for key in ("editable", "directory", "path")):
            continue
        name, version = package["name"], package["version"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version
        ):
            raise ValueError("Invalid local package name or version in uv.lock")
        key = normalized(name)
        if key in versions:
            raise ValueError(f"Ambiguous local package in uv.lock: {key}")
        versions[key] = version
    expected = {normalized(manifest["project"]["name"])}
    expected.update(
        normalized(name)
        for name, source in manifest.get("tool", {})
        .get("uv", {})
        .get("sources", {})
        .items()
        if isinstance(source, dict) and "path" in source
    )
    missing = expected - versions.keys()
    if missing:
        raise ValueError(f"Local packages missing from uv.lock: {sorted(missing)}")
    validate_source(project, versions)
    return versions


def validate_source(package: Path, versions: dict[str, str]) -> None:
    manifest = metadata(package / "pyproject.toml")["project"]
    name = normalized(manifest["name"])
    if versions.get(name) != manifest["version"]:
        raise ValueError(
            f"Stale uv.lock: {name} source version {manifest['version']} "
            f"does not match locked version {versions.get(name)!r}; sync the agents lock first"
        )


def export_constraints(project: Path, component: str, *, uv: str = "uv") -> str:
    versions = local_versions(project)
    if component not in {"agents", "core"}:
        raise ValueError("component must be agents or core")
    if "fred-core" not in versions:
        raise ValueError("fred-core must be a locked local package")
    command = [
        uv,
        "export",
        "--project",
        str(project),
        "--frozen",
        "--offline",
        "--no-cache",
        "--no-dev",
        "--no-emit-local",
        "--no-hashes",
        "--no-annotate",
        "--no-header",
        "--format",
        "requirements.txt",
    ]
    selected = versions
    if component == "core":
        # The applications only depend on core. In particular, do not constrain
        # their separately tested MCP server to the agent client's MCP version.
        project_name = normalized(
            metadata(project / "pyproject.toml")["project"]["name"]
        )
        for name in sorted(versions.keys() - {project_name, "fred-core"}):
            command.extend(("--prune", name))
        selected = {"fred-core": versions["fred-core"]}
    exported = subprocess.run(  # nosec B603: fixed arguments, no shell
        command, check=True, capture_output=True, text=True
    ).stdout
    pins = "\n".join(f"{name}=={version}" for name, version in sorted(selected.items()))
    return (
        "# Generated from agents/uv.lock; do not edit.\n"
        + exported.rstrip()
        + "\n"
        + pins
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--component", choices=("agents", "core"), default="agents")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        versions = local_versions(args.project)
        for source in args.source:
            validate_source(source, versions)
        exported = export_constraints(args.project, args.component)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    args.output.write_text(exported)


if __name__ == "__main__":
    main()
