#!/usr/bin/env python3
"""Copy the maintained EAIDK-310 build inputs into the public checkout."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


PROJECTS = {
    "kernel-6.12.108-zramfix1": {
        "destination": Path("kernel/linux-6.12.108-zramfix1"),
        "copy": {
            "README.md",
            "baseline",
            "config",
            "dts",
            "initramfs",
            "patches",
            "scripts",
            "source-lock.json",
            "tests",
            "tools",
        },
        "ignore": {"analysis", "artifacts", "snapshots", "__pycache__"},
        "selective": {"analysis/board-port-matrix.json"},
        "exclude": {"tests/test_documentation_contract.py"},
    },
    "u-boot-eaidk310": {
        "destination": Path("bootloader/u-boot-eaidk310"),
        "copy": {"README.md", "patches", "scripts", "source-lock.json", "tests"},
        "ignore": {"__pycache__"},
        "selective": set(),
        "exclude": set(),
    },
}

BOARD_TOOL_FILES = {
    "build_eaidk310_st7789_test.py",
    "capture-eaidk-boot.ps1",
    "eaidk-uboot-write-lib.ps1",
    "eaidk310-emmc-excludes.txt",
    "eaidk310_emmc.py",
    "eaidk310_emmc_lib.py",
    "prepare_eaidk310_uboot.py",
    "rockchip_loaderimage.py",
    "serial-console.ps1",
    "serial-run.ps1",
    "st7789_framebuffer_test.py",
    "test-capture-eaidk-boot.ps1",
    "test-serial-console.ps1",
    "test-write-uboot-proper-to-tf.ps1",
    "test_build_eaidk310_st7789.py",
    "test_eaidk310_emmc_lib.py",
    "test_st7789_framebuffer_test.py",
    "write-uboot-proper-to-tf.ps1",
    "write-uboot-to-tf.ps1",
}

ST7789_BASE_DTS = Path("wifi-test1/current-rk3328-eaidk-310.dts")
ST7789_PUBLIC_DTS = Path("hardware/st7789/current-rk3328-eaidk-310.dts")


def _remove_controlled_destination(public: Path, destination: Path) -> None:
    resolved_public = public.resolve()
    resolved_destination = (public / destination).resolve()
    try:
        resolved_destination.relative_to(resolved_public)
    except ValueError as error:
        raise ValueError(f"destination escapes public root: {destination}") from error
    if resolved_destination == resolved_public:
        raise ValueError("refusing to replace the public repository root")
    if resolved_destination.exists():
        shutil.rmtree(resolved_destination)


def _copy_entry(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def sync(workspace: Path, public: Path) -> list[str]:
    """Synchronize allowlisted project inputs and return copied file paths."""
    workspace = workspace.resolve()
    public = public.resolve()
    if not workspace.is_dir() or not public.is_dir():
        raise ValueError("workspace and public roots must both be directories")

    for source_name, policy in PROJECTS.items():
        source_root = workspace / source_name
        if not source_root.is_dir():
            raise ValueError(f"missing maintained source root: {source_name}")
        observed = {entry.name for entry in source_root.iterdir()}
        known = policy["copy"] | policy["ignore"]
        unknown = sorted(observed - known)
        if unknown:
            raise ValueError(f"unknown entries below {source_name}: {', '.join(unknown)}")
        missing = sorted(policy["copy"] - observed)
        if missing:
            raise ValueError(f"missing required entries below {source_name}: {', '.join(missing)}")

        destination_root = public / policy["destination"]
        _remove_controlled_destination(public, policy["destination"])
        destination_root.mkdir(parents=True)
        for name in sorted(policy["copy"]):
            _copy_entry(source_root / name, destination_root / name)
        for relative_name in sorted(policy["selective"]):
            source_file = source_root / relative_name
            if not source_file.is_file():
                raise ValueError(f"missing selectively published file: {source_name}/{relative_name}")
            _copy_entry(source_file, destination_root / relative_name)
        for relative_name in sorted(policy["exclude"]):
            excluded = destination_root / relative_name
            if excluded.exists():
                excluded.unlink()

    source_tools = workspace / "tools"
    missing_tools = sorted(name for name in BOARD_TOOL_FILES if not (source_tools / name).is_file())
    if missing_tools:
        raise ValueError(f"missing required board tools: {', '.join(missing_tools)}")
    board_destination = Path("tools")
    legacy_board_destination = public / "tools" / "board"
    if legacy_board_destination.exists():
        resolved_legacy = legacy_board_destination.resolve()
        resolved_legacy.relative_to(public)
        shutil.rmtree(resolved_legacy)
    for name in sorted(BOARD_TOOL_FILES):
        _copy_entry(source_tools / name, public / board_destination / name)

    source_dts = workspace / ST7789_BASE_DTS
    if not source_dts.is_file():
        raise ValueError(f"missing required ST7789 base DTS: {ST7789_BASE_DTS.as_posix()}")
    _copy_entry(source_dts, public / ST7789_PUBLIC_DTS)

    return sorted(
        path.relative_to(public).as_posix()
        for path in public.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(public).parts
    )


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=repository.parent.parent)
    parser.add_argument("--public", type=Path, default=repository)
    args = parser.parse_args()
    copied = sync(args.workspace, args.public)
    print(f"SYNC_PUBLIC_TREE=PASS files={len(copied)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
