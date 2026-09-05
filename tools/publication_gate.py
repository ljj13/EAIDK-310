#!/usr/bin/env python3
"""Reject files that do not belong in the public EAIDK-310 Git history."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


GIT_OBJECT_LIMIT = 100 * 1024 * 1024
RELEASE_ONLY_SUFFIXES = (
    ".deb",
    ".img",
    ".img.xz",
    ".iso",
    ".tar.zst",
)
SECRET_PATTERNS = (
    ("private key", re.compile(rb"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(rb"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("OpenAI-style token", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}")),
    (
        "literal password assignment",
        re.compile(rb"(?i)\b(?:password|passwd)\s*[:=]\s*['\"]?[^\s'\"]{4,}"),
    ),
)


def _is_ignored(relative: Path) -> bool:
    return any(part in {".git", "__pycache__"} for part in relative.parts)


def _has_release_only_suffix(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in RELEASE_ONLY_SUFFIXES)


def scan_repository(root: Path) -> list[str]:
    """Return deterministic publication violations below *root*."""
    root = root.resolve()
    if not root.is_dir():
        return [f"repository root is not a directory: {root}"]

    errors: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        relative = path.relative_to(root)
        if _is_ignored(relative):
            continue
        if path.is_symlink():
            try:
                path.resolve(strict=False).relative_to(root)
            except ValueError:
                errors.append(f"escaping symlink: {relative.as_posix()}")
            continue
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size >= GIT_OBJECT_LIMIT:
            errors.append(f"file reaches GitHub 100 MiB hard limit: {relative.as_posix()}")
        if _has_release_only_suffix(path):
            errors.append(f"release-only binary is inside Git history: {relative.as_posix()}")
        if size > 16 * 1024 * 1024:
            continue
        data = path.read_bytes()
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(data):
                errors.append(f"{label} material found: {relative.as_posix()}")
    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=Path.cwd())
    args = parser.parse_args(argv)
    errors = scan_repository(args.root)
    if errors:
        for error in errors:
            print(f"PUBLICATION_GATE=FAIL {error}", file=sys.stderr)
        return 1
    print("PUBLICATION_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
