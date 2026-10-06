#!/usr/bin/env python3
"""Verify custom-src/ mirrors against the production patch stacks.

The repository follows the "pinned upstream + source-lock + patch stack"
model: Linux/U-Boot full source is never vendored, and the project-owned
files are carried as patches.  custom-src/ is a byte-exact, human-readable
mirror of the files this project owns; it is NOT a second implementation.

Two verification tiers:

* Tier 1 (repository-only, always runs -- no network, no workspace):
    - manifest.json schema and sha256 integrity
    - byte comparison of mirrors whose final content is a single new-file
      hunk against the patch body itself
    - kernel board DTS equality across all kernel trees and the mirror
    - stray-file and size guards (nothing accidentally vendored)
    - documentation cross-references
* Tier 2 (full patched-tree comparison, only when the pinned upstream
  source is materialized): apply the production patch stack to the pinned
  upstream commit and compare every mirrored file byte-for-byte.

`--sync` re-extracts the mirrors from the patch stack and refreshes
manifest hashes.  It is a maintainer tool: CI and `make` targets must
only ever call `--check`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUSTOM_SRC = ROOT / "custom-src"
MANIFEST_PATH = CUSTOM_SRC / "manifest.json"
INVENTORY_PATH = ROOT / "docs" / "CUSTOM-SOURCE-INVENTORY.md"
CUSTOM_README_PATH = CUSTOM_SRC / "README.md"

MAX_MIRROR_BYTES = 512 * 1024
MAX_CUSTOM_SRC_BYTES = 2 * 1024 * 1024

COMPONENTS = ("u-boot", "linux")
ROLES = ("production", "alternative", "experimental")
METHODS = ("patch-new-file", "patched-stack", "repo-canonical")

COMPONENT_LAYOUT = {
    "u-boot": {
        "patches_dir": Path("bootloader/u-boot-eaidk310/patches"),
        "lock": Path("bootloader/u-boot-eaidk310/source-lock.json"),
        "env": "EAIDK310_UBOOT_SRC",
        "legacy_ws": Path(".build/upstream/u-boot"),
    },
    "linux": {
        "patches_dir": Path("kernel/linux-6.18.54-zramfix1/patches"),
        "lock": Path("kernel/linux-6.18.54-zramfix1/source-lock.json"),
        "env": "EAIDK310_LINUX_SRC",
        "legacy_ws": Path(".build/upstream/linux"),
    },
}


def cache_workspace(component: str) -> Path:
    """Default materialization target.  Lives OUTSIDE the repository on
    purpose: publication_gate.py scans the whole working tree, and a
    vendored upstream clone (with its upstream test keys) must never be
    visible to it."""
    return Path.home() / ".cache" / "eaidk310" / "upstream" / component

REQUIRED_KEYS = (
    "component",
    "upstream_path",
    "mirror_path",
    "source_patch",
    "role",
    "sha256",
)


# --------------------------------------------------------------------------
# patch parsing (byte-exact; patches are LF text per .gitattributes)
# --------------------------------------------------------------------------

def _patch_section(patch: bytes, upstream_path: str) -> bytes | None:
    """Return the `diff --git` section of one file from a patch, or None."""
    start = f"diff --git a/{upstream_path} b/{upstream_path}".encode() + b"\n"
    lines = patch.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line == start:
            out = [line]
            for later in lines[i + 1:]:
                if later.startswith(b"diff --git "):
                    break
                out.append(later)
            return b"".join(out)
    return None


def extract_new_file_bytes(patch: bytes, upstream_path: str) -> bytes | None:
    """Extract the exact content of a `new file mode` hunk body."""
    section = _patch_section(patch, upstream_path)
    if section is None:
        return None
    lines = section.split(b"\n")
    try:
        plus = lines.index(f"+++ b/{upstream_path}".encode())
    except ValueError:
        return None
    if not any(line.startswith(b"new file mode ") for line in lines[:plus]):
        return None
    out: list[bytes] = []
    for line in lines[plus + 1:]:
        if line.startswith(b"+"):
            out.append(line[1:] + b"\n")
        elif line.startswith(b"\\"):
            # "\ No newline at end of file" always terminates a hunk
            if out:
                out[-1] = out[-1].rstrip(b"\n")
            break
        elif line.startswith(b"@@"):
            continue
        elif line == b"":
            break
        else:
            break
    return b"".join(out)


def patch_declares_new_file(patch: bytes, upstream_path: str) -> bool:
    section = _patch_section(patch, upstream_path)
    return section is not None and b"\nnew file mode " in section


def patch_touches_file(patch: bytes, upstream_path: str) -> bool:
    return _patch_section(patch, upstream_path) is not None


def file_hunk_section(patch: bytes, upstream_path: str) -> bytes | None:
    """Alias of _patch_section for building per-file mini patches."""
    return _patch_section(patch, upstream_path)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=check
    )


def load_lock(component: str) -> dict:
    return json.loads((ROOT / COMPONENT_LAYOUT[component]["lock"]).read_bytes())


def patches_for(component: str) -> list[Path]:
    pdir = ROOT / COMPONENT_LAYOUT[component]["patches_dir"]
    return sorted(pdir.glob("*.patch"))


def workspace_candidates(component: str) -> list[Path]:
    layout = COMPONENT_LAYOUT[component]
    out = []
    env = os.environ.get(layout["env"])
    if env:
        out.append(Path(env))
    out.append(ROOT / layout["legacy_ws"])
    out.append(cache_workspace(component))
    return out


def find_workspace(component: str) -> Path | None:
    for cand in workspace_candidates(component):
        if (cand / ".git").exists() or (cand / "Makefile").exists():
            return cand
    return None


def apply_stack(target: Path, component: str) -> None:
    for patch in patches_for(component):
        run_git(["apply", "--whitespace=nowarn", str(patch)], cwd=target)


def patched_tree_worktree(component: str) -> tuple[Path | None, str | None]:
    """Return (worktree_path, error). Worktree applies the full patch stack
    on top of the pinned commit without mutating the materialized clone."""
    ws = find_workspace(component)
    if ws is None:
        return None, None
    lock = load_lock(component)
    pinned = lock.get("commit") or lock.get("archive_sha256")
    head = run_git(["rev-parse", "HEAD"], cwd=ws, check=False)
    if pinned and head.returncode == 0 and head.stdout.strip() != pinned:
        return None, (
            f"materialized {component} workspace {ws} is at "
            f"{head.stdout.strip()[:12]}, pinned {pinned[:12]}; re-materialize"
        )
    tmp = Path(tempfile.mkdtemp(prefix=f"eaidk310-custom-src-{component}-"))
    wt = run_git(["worktree", "add", "--detach", str(tmp), "HEAD"], cwd=ws, check=False)
    if wt.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, f"cannot create worktree from {ws}: {wt.stderr.strip()}"
    try:
        apply_stack(tmp, component)
    except subprocess.CalledProcessError as exc:
        run_git(["worktree", "remove", "--force", str(tmp)], cwd=ws, check=False)
        shutil.rmtree(tmp, ignore_errors=True)
        return None, f"patch stack does not apply to pinned {component} tree: {exc.stderr.strip()}"
    return tmp, None


def release_worktree(ws: Path | None, tmp: Path | None) -> None:
    if ws is not None and tmp is not None:
        run_git(["worktree", "remove", "--force", str(tmp)], cwd=ws, check=False)
    if tmp is not None:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# tier 1: repository-only checks
# --------------------------------------------------------------------------

def check_manifest(entries: list[dict]) -> list[str]:
    errors: list[str] = []
    if not entries:
        return ["manifest.json declares no entries"]
    seen_mirrors: set[str] = set()
    for entry in entries:
        label = entry.get("mirror_path", "<entry missing mirror_path>")
        for key in REQUIRED_KEYS:
            if key not in entry:
                errors.append(f"{label}: missing required key '{key}'")
        if any(key not in entry for key in ("component", "upstream_path")):
            continue
        component = entry["component"]
        if component not in COMPONENTS:
            errors.append(f"{label}: unknown component '{component}'")
            continue
        if entry.get("role") not in ROLES:
            errors.append(f"{label}: role must be one of {ROLES}")
        method = entry.get("method")
        if method not in METHODS:
            errors.append(f"{label}: method must be one of {METHODS}")
            continue

        mirror_rel = entry.get("mirror_path", "")
        mirror = ROOT / mirror_rel
        try:
            resolved = mirror.resolve()
            resolved.relative_to(CUSTOM_SRC.resolve())
        except ValueError:
            errors.append(f"{label}: mirror_path escapes custom-src/")
            continue
        if mirror_rel in seen_mirrors:
            errors.append(f"{label}: duplicate mirror_path")
        seen_mirrors.add(mirror_rel)
        if not mirror.is_file():
            errors.append(f"{label}: mirror file missing")
            continue
        if mirror.stat().st_size > MAX_MIRROR_BYTES:
            errors.append(f"{label}: mirror larger than {MAX_MIRROR_BYTES} bytes")
        digest = hashlib.sha256(mirror.read_bytes()).hexdigest()
        if entry.get("sha256") != digest:
            errors.append(f"{label}: sha256 mismatch (manifest stale; run --sync)")

        upstream = entry["upstream_path"]
        if method == "repo-canonical":
            src = entry.get("source_path")
            if not src:
                errors.append(f"{label}: repo-canonical entry needs source_path")
            elif not (ROOT / src).is_file():
                errors.append(f"{label}: source_path {src} missing")
            elif (ROOT / src).read_bytes() != mirror.read_bytes():
                errors.append(
                    f"MIRROR_DRIFT:\n  custom-src: {mirror_rel}\n"
                    f"  canonical:  {src}"
                )
            continue

        patch_name = entry.get("source_patch")
        if not patch_name:
            errors.append(f"{label}: patch-backed entry needs source_patch")
            continue
        patch_path = ROOT / COMPONENT_LAYOUT[component]["patches_dir"] / patch_name
        if not patch_path.is_file():
            errors.append(f"{label}: source_patch {patch_name} not found")
            continue
        patch_bytes = patch_path.read_bytes()
        if method == "patch-new-file":
            expected = extract_new_file_bytes(patch_bytes, upstream)
            if expected is None:
                errors.append(
                    f"{label}: source_patch {patch_name} has no new-file hunk for {upstream}"
                )
            elif mirror.read_bytes() != expected:
                errors.append(
                    f"MIRROR_DRIFT:\n  custom-src: {mirror_rel}\n"
                    f"  patch body: {patch_name}::{upstream}"
                )
        else:  # patched-stack
            last_name = entry.get("last_patch")
            if not last_name:
                errors.append(f"{label}: patched-stack entry needs last_patch")
                continue
            last_path = ROOT / COMPONENT_LAYOUT[component]["patches_dir"] / last_name
            if not last_path.is_file():
                errors.append(f"{label}: last_patch {last_name} not found")
                continue
            if not patch_declares_new_file(patch_bytes, upstream):
                errors.append(
                    f"{label}: {patch_name} does not declare {upstream} as a new file"
                )
            if not patch_touches_file(last_path.read_bytes(), upstream):
                errors.append(
                    f"{label}: {last_name} does not touch {upstream}; "
                    "final content should be method=patch-new-file"
                )
            if patches_for(component).index(patch_path) >= patches_for(component).index(last_path):
                errors.append(f"{label}: last_patch must come after source_patch")
    return errors


def check_no_extra_files(entries: list[dict]) -> list[str]:
    allowed = {"custom-src/README.md", "custom-src/manifest.json"}
    allowed |= {e["mirror_path"] for e in entries if "mirror_path" in e}
    errors: list[str] = []
    for path in CUSTOM_SRC.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel not in allowed:
            errors.append(f"unexpected file in custom-src/: {rel} (not declared in manifest.json)")
    total = sum(p.stat().st_size for p in CUSTOM_SRC.rglob("*") if p.is_file())
    if total > MAX_CUSTOM_SRC_BYTES:
        errors.append(f"custom-src/ totals {total} bytes (> {MAX_CUSTOM_SRC_BYTES}); vendored source?")
    return errors


def check_linux_dts_family(entries: list[dict]) -> list[str]:
    errors: list[str] = []
    mirror = next(
        (e for e in entries if e.get("component") == "linux" and e.get("method") == "repo-canonical"),
        None,
    )
    if mirror is None:
        return ["manifest.json has no linux board-DTS entry"]
    expected = (ROOT / mirror["mirror_path"]).read_bytes()
    trees = sorted(ROOT.glob("kernel/linux-*/dts/rk3328-eaidk-310.dts"))
    if not trees:
        return ["no kernel/linux-*/dts/rk3328-eaidk-310.dts build inputs found"]
    for tree in trees:
        if tree.read_bytes() != expected:
            errors.append(
                f"MIRROR_DRIFT:\n  custom-src: {mirror['mirror_path']}\n"
                f"  build input: {tree.relative_to(ROOT).as_posix()}"
            )
    return errors


def check_docs(entries: list[dict]) -> list[str]:
    errors: list[str] = []
    if not INVENTORY_PATH.is_file():
        return ["docs/CUSTOM-SOURCE-INVENTORY.md is missing"]
    if not CUSTOM_README_PATH.is_file():
        return ["custom-src/README.md is missing"]
    inventory = INVENTORY_PATH.read_text(encoding="utf-8")
    readme = CUSTOM_README_PATH.read_text(encoding="utf-8")
    if "Patch-only modifications" not in readme:
        errors.append("custom-src/README.md is missing the 'Patch-only modifications' table")
    for entry in entries:
        rel = entry.get("mirror_path", "")
        if rel and rel not in inventory:
            errors.append(f"docs/CUSTOM-SOURCE-INVENTORY.md does not mention {rel}")
        patch_name = entry.get("source_patch")
        if patch_name and patch_name not in readme:
            errors.append(f"custom-src/README.md does not reference {patch_name}")
    return errors


def check_tier1() -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not MANIFEST_PATH.is_file():
        print("tier 1 (repository-only): FAIL")
        print("  custom-src/manifest.json is missing")
        return False, ["manifest.json missing"]
    manifest = json.loads(MANIFEST_PATH.read_bytes())
    if manifest.get("schema") != 1:
        errors.append("manifest schema must be 1")
    entries = manifest.get("entries", [])
    manifest_errors = check_manifest(entries)
    errors += manifest_errors
    errors += check_no_extra_files(entries)
    errors += check_linux_dts_family(entries)
    errors += check_docs(entries)

    patch_body_count = sum(1 for e in entries if e.get("method") == "patch-new-file")
    print("tier 1 (repository-only): " + ("FAIL" if errors else "OK"))
    print(f"  manifest entries: {len(entries)}")
    print(f"  patch-body byte compare: {patch_body_count} mirrors checked against patch hunks")
    if not errors:
        trees = len(sorted(ROOT.glob('kernel/linux-*/dts/rk3328-eaidk-310.dts')))
        print(f"  kernel board DTS equality: {trees} kernel trees + mirror byte-identical")
    for err in errors:
        print(f"  {err}")
    return not errors, errors


# --------------------------------------------------------------------------
# tier 2: full patched-tree comparison (requires materialized upstream)
# --------------------------------------------------------------------------

def check_tier2_component(component: str, entries: list[dict]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    mine = [e for e in entries if e.get("component") == component]
    if not mine:
        return True, []
    ws = find_workspace(component)
    if ws is None:
        print(f"tier 2 [{component}]: SKIP_FULL_SOURCE_COMPARE({component}): workspace-unavailable")
        print(f"  (materialize with: python tools/verify_custom_source.py --sync)")
        return True, []
    tmp, err = patched_tree_worktree(component)
    if err:
        print(f"tier 2 [{component}]: FAIL")
        print(f"  {err}")
        return False, [f"{component}: {err}"]
    try:
        for entry in mine:
            mirror = ROOT / entry["mirror_path"]
            patched = tmp / entry["upstream_path"]
            if not patched.is_file():
                errors.append(
                    f"MIRROR_DRIFT:\n  custom-src: {entry['mirror_path']}\n"
                    f"  patched upstream: {entry['upstream_path']} (missing after patch stack)"
                )
                continue
            if patched.read_bytes() != mirror.read_bytes():
                errors.append(
                    f"MIRROR_DRIFT:\n  custom-src: {entry['mirror_path']}\n"
                    f"  patched upstream: {component}:{entry['upstream_path']}"
                )
    finally:
        release_worktree(ws, tmp)
    print(f"tier 2 [{component}]: " + ("FAIL" if errors else f"PASS (patched tree at {ws})"))
    for err in errors:
        print(f"  {err}")
    return not errors, errors


# --------------------------------------------------------------------------
# --sync: maintainer-only mirror refresh
# --------------------------------------------------------------------------

def materialize(component: str) -> Path:
    lock = load_lock(component)
    target = cache_workspace(component)
    if target.exists():
        head = run_git(["rev-parse", "HEAD"], cwd=target, check=False)
        if head.returncode == 0 and lock.get("commit") and head.stdout.strip() == lock["commit"]:
            return target
        print(f"sync [{component}]: cache at {target} is stale; re-cloning")
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    url = lock["repository"]
    tag = lock["tag"]
    print(f"sync [{component}]: cloning {url} ({tag}, shallow) ...")
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", tag, url, str(target)],
        check=True,
    )
    head = run_git(["rev-parse", "HEAD"], cwd=target, check=True).stdout.strip()
    if lock.get("commit") and head != lock["commit"]:
        raise SystemExit(
            f"sync [{component}]: cloned HEAD {head[:12]} != pinned {lock['commit'][:12]}"
        )
    return target


def reconstruct_offline(entry: dict) -> bytes:
    """Reconstruct final content by applying the per-file patch hunks in a
    throwaway git repo.  This applies the real production patch stack
    section-by-section; used only when no materialized tree is available."""
    component = entry["component"]
    upstream = entry["upstream_path"]
    all_patches = patches_for(component)
    start_idx = next(i for i, p in enumerate(all_patches) if p.name == entry["source_patch"])
    end_name = entry.get("last_patch") or entry["source_patch"]
    end_idx = next(i for i, p in enumerate(all_patches) if p.name == end_name)
    stack = all_patches[start_idx:end_idx + 1]
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        run_git(["init", "-q"], cwd=repo)
        run_git(["config", "user.email", "verify@custom-src.invalid"], cwd=repo)
        run_git(["config", "user.name", "custom-src offline reconstruction"], cwd=repo)
        first = stack[0]
        content = extract_new_file_bytes(first.read_bytes(), upstream)
        if content is None:
            raise SystemExit(f"sync: {first.name} has no new-file hunk for {upstream}")
        target = repo / upstream
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        run_git(["add", upstream], cwd=repo)
        run_git(["commit", "-qm", "base"], cwd=repo)
        for patch in stack[1:]:
            section = file_hunk_section(patch.read_bytes(), upstream)
            if section is None:
                raise SystemExit(f"sync: {patch.name} does not touch {upstream}")
            mini = repo / "mini.patch"
            mini.write_bytes(section)
            try:
                run_git(["apply", "--whitespace=nowarn", "mini.patch"], cwd=repo)
            except subprocess.CalledProcessError as exc:
                raise SystemExit(
                    f"sync: offline stack reconstruction failed for {upstream} "
                    f"at {patch.name}: {exc.stderr.strip()}"
                )
        return target.read_bytes()


def cmd_sync() -> int:
    manifest = json.loads(MANIFEST_PATH.read_bytes())
    entries = manifest.get("entries", [])
    changed = []
    boot_entries = [e for e in entries if e["component"] == "u-boot"]
    if boot_entries:
        methods = {e.get("method") for e in boot_entries}
        ws: Path | None = None
        if "patched-stack" in methods or "patch-new-file" in methods:
            try:
                ws = materialize("u-boot")
            except (subprocess.CalledProcessError, SystemExit) as exc:
                print(f"sync [u-boot]: materialization unavailable ({exc}); "
                      "falling back to offline patch-stack reconstruction")
                ws = None
        for entry in boot_entries:
            if ws is not None:
                tmp, err = patched_tree_worktree("u-boot")
                if err:
                    print(f"sync [u-boot]: {err}")
                    return 1
                try:
                    patched = tmp / entry["upstream_path"]
                    if not patched.is_file():
                        print(f"sync [u-boot]: {entry['upstream_path']} missing in patched tree")
                        return 1
                    data = patched.read_bytes()
                finally:
                    release_worktree(ws, tmp)
            else:
                data = reconstruct_offline(entry)
            mirror = ROOT / entry["mirror_path"]
            mirror.parent.mkdir(parents=True, exist_ok=True)
            mirror.write_bytes(data)
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            changed.append(entry["mirror_path"])
    for entry in [e for e in entries if e.get("method") == "repo-canonical"]:
        src = ROOT / entry["source_path"]
        mirror = ROOT / entry["mirror_path"]
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_bytes(src.read_bytes())
        entry["sha256"] = hashlib.sha256(mirror.read_bytes()).hexdigest()
        changed.append(entry["mirror_path"])
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"sync: refreshed {len(changed)} mirrors and manifest hashes")
    for rel in changed:
        print(f"  {rel}")
    ok, _ = check_all()
    return 0 if ok else 1


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def check_all() -> tuple[bool, list[str]]:
    print("== EAIDK-310 custom-src verification ==")
    ok1, errors = check_tier1()
    ok = ok1
    if ok1 and MANIFEST_PATH.is_file():
        entries = json.loads(MANIFEST_PATH.read_bytes()).get("entries", [])
        for component in COMPONENTS:
            okc, errs = check_tier2_component(component, entries)
            ok = ok and okc
            errors += errs
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    return ok, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true",
                       help="verify mirrors (default; safe for CI/make)")
    group.add_argument("--sync", action="store_true",
                       help="maintainer only: re-extract mirrors from the patch stack "
                            "and refresh manifest hashes; may fetch pinned upstream")
    args = parser.parse_args(argv)
    if args.sync:
        return cmd_sync()
    ok, _ = check_all()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
