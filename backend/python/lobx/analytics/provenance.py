"""Runtime provenance for reproducible research artifacts."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


def git_provenance(cwd: str | Path = ".") -> dict[str, Any]:
    """Return commit and dirty-tree state without failing non-git callers."""
    root = str(cwd)
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip())
    except (OSError, subprocess.CalledProcessError):
        commit = None
        dirty = None
    return {"git_commit": commit, "git_dirty": dirty}


def parameter_hash(parameters: dict[str, Any]) -> str:
    payload = json.dumps(parameters, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = ["git_provenance", "parameter_hash"]
