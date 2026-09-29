"""Build samples from local Fred wheels, without publishing or deploying them."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from export_constraints import export_constraints, local_versions, validate_source

APPS = (
    "review-board",
    "document-triage",
    "progress-tracker",
)


def run(command: list[str], root: Path) -> None:
    """Run a fixed build command; failures stop before any image import or rollout."""
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=root, check=True)  # nosec B603: fixed executables, no shell


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fred-root", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--component", choices=("all", "agents", *APPS), default="all")
    parser.add_argument("--wheels-only", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", args.tag):
        parser.error("--tag must be a valid Docker tag, without a registry or slash")
    root = Path(__file__).resolve().parent.parent
    fred = args.fred_root.resolve()
    packages = [
        fred / "libs" / name
        for name in ("fred-pod", "fred-core", "fred-sdk", "fred-runtime")
    ]
    packages.append(root / "agents")
    if any(not (package / "pyproject.toml").is_file() for package in packages):
        parser.error("--fred-root must point to the matching Fred source checkout")
    try:
        versions = local_versions(root / "agents")
        for package in packages:
            validate_source(package, versions)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    uv = shutil.which("uv")
    docker = shutil.which("docker")
    if uv is None or (docker is None and not args.wheels_only):
        parser.error("uv and (unless --wheels-only) Docker are required")
    wheel_root = root / "wheels"
    wheel_root.mkdir(exist_ok=True)
    # A fresh directory prevents an old wheel from winning dependency resolution.
    wheels = Path(tempfile.mkdtemp(prefix="local-", dir=wheel_root))
    for component in ("agents", "core"):
        constraints = export_constraints(root / "agents", component, uv=uv)
        (wheels / f"{component}-constraints.txt").write_text(constraints)
    # Building through the sdist keeps a stale in-tree build/ out of the wheel.
    for package in packages:
        run(
            [
                uv,
                "build",
                str(package),
                "--quiet",
                "--no-sources",
                "--out-dir",
                str(wheels),
            ],
            root,
        )
    for sdist in wheels.glob("*.tar.gz"):
        sdist.unlink()
    print(f"Local wheels: {wheels}", flush=True)
    if args.wheels_only:
        return
    assert docker is not None
    selected = ("agents", *APPS) if args.component == "all" else (args.component,)
    for component in selected:
        image = "fred-samples-agents" if component == "agents" else f"{component}-api"
        target = "agents" if component == "agents" else "application"
        run(
            [
                docker,
                "build",
                "-f",
                "dockerfiles/Dockerfile.wheels",
                "--target",
                target,
                "--build-arg",
                f"FRED_WHEELS={wheels.relative_to(root)}",
                "--build-arg",
                f"SAMPLE_APP={component}",
                "-t",
                f"{image}:{args.tag}",
                ".",
            ],
            root,
        )
        if component != "agents":
            run(
                [
                    docker,
                    "build",
                    "-t",
                    f"{component}-ui:{args.tag}",
                    f"apps/{component}/ui",
                ],
                root,
            )
    print("Built local images only. No images pushed, imported, or deployed.")


if __name__ == "__main__":
    main()
