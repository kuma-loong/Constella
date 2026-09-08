"""Build reviewable release archives without touching a running source deployment."""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def tracked_sources(root: Path) -> list[Path]:
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    sources = []
    for name in filter(None, names):
        source = root / name
        if not source.is_file():
            raise ValueError(f"Tracked source is missing: {name}")
        if source.is_symlink():
            raise ValueError(f"Refusing to package a tracked symlink: {name}")
        sources.append(Path(name))
    return sources


def build(out_dir: Path) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("Output directory must be empty; existing archives are never removed")
    sources = tracked_sources(ROOT)
    out_dir.mkdir(parents=True, exist_ok=True)
    (ROOT / "run").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="preview-package-", dir=ROOT / "run") as temporary:
        staging = Path(temporary)
        for relative in sources:
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        subprocess.run(["npm", "ci", "--prefix", str(staging / "frontend")], check=True)
        for edition in ("build:package", "build:lab"):
            subprocess.run(["npm", "run", edition, "--prefix", str(staging / "frontend")], check=True)
        artifacts = staging / "dist"
        subprocess.run(
            ["uv", "build", "--all-packages", "--out-dir", str(artifacts)],
            cwd=staging, check=True,
        )
        archives = sorted([*artifacts.glob("*.whl"), *artifacts.glob("*.tar.gz")])
        if len(archives) != 10:
            raise ValueError(f"Expected ten release archives, found {len(archives)}")
        checksums = []
        for archive in archives:
            shutil.copyfile(archive, out_dir / archive.name)
            checksums.append(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}")
        (out_dir / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
    print(f"Prepared ten archives and SHA256SUMS in {out_dir}")
    print("Nothing was uploaded, tagged, or deployed.")


if __name__ == "__main__":
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "dist" / version)
    args = parser.parse_args()
    build(args.out_dir.resolve())
