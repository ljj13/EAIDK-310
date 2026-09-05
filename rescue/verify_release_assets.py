#!/usr/bin/env python3
"""Verify EAIDK-310 release assets against a schema-versioned manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlparse


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPRODUCIBILITY_CLASSES = {"binary-recovery", "source-rebuild"}
REQUIRED_FIELDS = {"name", "size", "sha256", "source_url", "reproducibility"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username


def verify_assets(manifest_path: Path, directory: Path) -> list[str]:
    """Return all manifest/schema/content errors without modifying either input."""
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return [f"cannot read manifest: {error}"]

    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        return ["manifest schema_version must equal 1"]
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        return ["manifest assets must be a non-empty list"]

    seen: set[str] = set()
    for index, asset in enumerate(assets):
        prefix = f"asset[{index}]"
        if not isinstance(asset, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing = REQUIRED_FIELDS - set(asset)
        if missing:
            errors.append(f"{prefix} missing fields: {', '.join(sorted(missing))}")
            continue

        name = asset["name"]
        if not isinstance(name, str) or not name or Path(name).name != name or "/" in name or "\\" in name:
            errors.append(f"{prefix} name must be a plain basename")
            continue
        if name in seen:
            errors.append(f"duplicate asset basename: {name}")
            continue
        seen.add(name)

        size = asset["size"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            errors.append(f"{prefix} size must be a non-negative integer")
        expected_hash = asset["sha256"]
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            errors.append(f"{prefix} sha256 must be 64 lowercase hexadecimal characters")
        if not _valid_https_url(asset["source_url"]):
            errors.append(f"{prefix} source_url must be an HTTPS URL without embedded credentials")
        if asset["reproducibility"] not in REPRODUCIBILITY_CLASSES:
            errors.append(
                f"{prefix} reproducibility must be binary-recovery or source-rebuild"
            )

        path = directory / name
        if not path.is_file():
            errors.append(f"missing asset file: {name}")
            continue
        if isinstance(size, int) and not isinstance(size, bool) and path.stat().st_size != size:
            errors.append(f"size mismatch for {name}: expected {size}, got {path.stat().st_size}")
        if isinstance(expected_hash, str) and SHA256_RE.fullmatch(expected_hash):
            actual_hash = _sha256(path)
            if actual_hash != expected_hash:
                errors.append(
                    f"sha256 mismatch for {name}: expected {expected_hash}, got {actual_hash}"
                )
    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    errors = verify_assets(args.manifest, args.directory)
    if errors:
        for error in errors:
            print(f"ASSET_GATE=FAIL {error}", file=sys.stderr)
        return 1
    print("ASSET_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
