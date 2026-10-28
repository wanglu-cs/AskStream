from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git_provenance() -> dict[str, object]:
    root = Path(__file__).resolve().parents[2]
    source_paths = sorted(root.joinpath("eval").rglob("*.py"))
    source_hashes = {
        str(path.relative_to(root)): _sha256_file(path)
        for path in source_paths
        if path.is_file()
    }
    source_digest = hashlib.sha256(
        json.dumps(
            source_hashes,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    try:
        commit = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                [
                    "git", "-c", f"safe.directory={root}", "status",
                    "--porcelain", "--untracked-files=no",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        worktree_dirty = bool(
            subprocess.run(
                [
                    "git", "-c", f"safe.directory={root}", "status",
                    "--porcelain",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return {
            "commit": commit,
            "tracked_files_dirty": dirty,
            "worktree_dirty": worktree_dirty,
            "evaluator_source_sha256": source_digest,
            "source_files": source_hashes,
        }
    except (OSError, subprocess.SubprocessError):
        return {
            "commit": None,
            "tracked_files_dirty": None,
            "worktree_dirty": None,
            "evaluator_source_sha256": source_digest,
            "source_files": source_hashes,
        }


