#!/usr/bin/env python3
"""Pure helpers and CLI gates for EAIDK-310 kernel artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
from typing import Any, Iterable, Sequence


REQUIRED_LOCK_KEYS = {
    "kernel_version",
    "localversion",
    "expected_release",
    "archive_url",
    "signature_url",
    "archive_sha256",
    "signer_fingerprint",
    "wsl_root",
    "baseline_files",
}

REQUIRED_BUILTIN_CONFIG = {
    "CONFIG_ARM64",
    "CONFIG_ARCH_ROCKCHIP",
    "CONFIG_BLK_DEV_INITRD",
    "CONFIG_DEVTMPFS",
    "CONFIG_EXT4_FS",
    "CONFIG_MMC",
    "CONFIG_MMC_DW",
    "CONFIG_MMC_DW_ROCKCHIP",
    "CONFIG_SERIAL_8250",
    "CONFIG_SERIAL_8250_CONSOLE",
    "CONFIG_SERIAL_8250_DW",
    "CONFIG_SPI_ROCKCHIP",
    "CONFIG_MFD_RK8XX",
    "CONFIG_REGULATOR_RK808",
    "CONFIG_RTC_DRV_RK808",
    "CONFIG_DRM_ROCKCHIP",
    "CONFIG_PINCTRL_ROCKCHIP",
    "CONFIG_GPIOLIB",
    "CONFIG_I2C_RK3X",
    "CONFIG_TUN",
}

REQUIRED_DRIVER_CONFIG = {
    "CONFIG_STMMAC_ETH",
    "CONFIG_DWMAC_ROCKCHIP",
    "CONFIG_USB_DWC2",
    "CONFIG_USB_DWC3",
    "CONFIG_DRM_LIMA",
    "CONFIG_SND_SOC_ROCKCHIP",
    "CONFIG_FB_TFT_ST7789V",
    "CONFIG_BRCMFMAC",
    "CONFIG_BT_HCIUART",
    "CONFIG_ZRAM",
    "CONFIG_ZRAM_BACKEND_LZ4",
    "CONFIG_NF_TABLES",
}

CONFIG_DIFF_SECTIONS = (
    ("removed_upstream", "Removed upstream"),
    ("default_changed", "Default changed"),
    ("dependency_changed", "Dependency changed"),
    ("intentional_eaidk_setting", "Intentional EAIDK setting"),
)

REQUIRED_PORT_SUBSYSTEMS = {
    "identity",
    "aliases",
    "chosen",
    "cpu-supply",
    "gpu-supply",
    "rk805",
    "regulators",
    "io-domains",
    "sdmmc",
    "sdio",
    "emmc",
    "gmac2io",
    "integrated-phy",
    "usb2",
    "usb3",
    "hdmi",
    "drm",
    "audio",
    "thermal",
    "gpio-leds",
    "uart2",
    "bluetooth",
    "i2c",
    "spi",
    "pwm",
    "known-failures",
}

PORT_MATRIX_FIELDS = {
    "subsystem",
    "legacy_path",
    "upstream_label",
    "reference_files",
    "disposition",
    "properties",
    "rationale",
}

PORT_DISPOSITIONS = {"inherit", "port", "disable", "omit-known-failure"}

REQUIRED_INPUT_OBSERVATIONS = {
    "source_dir",
    "build_dir",
    "kernel_version",
    "gcc_target",
    "validsig_fingerprint",
    "tool_versions",
}

KERNEL_RELEASE = "6.12.108-eaidk310-zramfix1"
BUNDLE_REQUIRED_FILES = {
    f"boot/Image-{KERNEL_RELEASE}",
    f"boot/uInitrd-{KERNEL_RELEASE}",
    "boot/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb",
    f"root/lib/modules/{KERNEL_RELEASE}/modules.dep",
    f"root/lib/modules/{KERNEL_RELEASE}/modules.builtin",
    f"root/lib/modules/{KERNEL_RELEASE}/modules.order",
    "deploy/deploy-rescue-tf.sh",
    "deploy/kernel_artifacts.py",
    "deploy/extlinux-entry.conf",
    "deploy/stable-baseline-sha256.txt",
    "verification-report.json",
}
FORBIDDEN_STABLE_BOOT_PATHS = {
    "boot/Image",
    "boot/uInitrd",
    "boot/dtb/rockchip/rk3328-eaidk-310.dtb",
}
EARLY_BOOT_DEPENDENCIES = {
    "ext4": "kernel/fs/ext4/ext4.ko",
    "mmc-block": "kernel/drivers/mmc/core/mmc_block.ko",
    "mmc-host": "kernel/drivers/mmc/host/dw_mmc-rockchip.ko",
    "rk805-regulator": "kernel/drivers/regulator/rk808-regulator.ko",
    "rockchip-pinctrl": "kernel/drivers/pinctrl/pinctrl-rockchip.ko",
}


def load_lock(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("source lock must be a JSON object")
    missing = sorted(REQUIRED_LOCK_KEYS - data.keys())
    if missing:
        raise ValueError(f"source lock is missing keys: {', '.join(missing)}")
    return data


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_locked_file(path: pathlib.Path, expected_sha256: str) -> None:
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256.lower():
        raise ValueError(
            f"SHA-256 mismatch for {path}: expected {expected_sha256.lower()}, "
            f"got {actual_sha256}"
        )


def _require_posix_path_below(value: str, root: str, field: str) -> None:
    path = pathlib.PurePosixPath(value)
    locked_root = pathlib.PurePosixPath(root)
    if path == locked_root or locked_root not in path.parents:
        raise ValueError(f"{field} must remain below {root}: {value}")


def build_input_report(
    lock: dict[str, Any],
    project_root: pathlib.Path,
    archive: pathlib.Path,
    observations: dict[str, Any],
) -> dict[str, Any]:
    missing = sorted(REQUIRED_INPUT_OBSERVATIONS - observations.keys())
    if missing:
        raise ValueError(f"input observations are missing: {', '.join(missing)}")

    if observations["kernel_version"] != lock["kernel_version"]:
        raise ValueError("observed kernel version does not match the source lock")
    if observations["gcc_target"] != "aarch64-linux-gnu":
        raise ValueError("GCC target must be aarch64-linux-gnu")
    if observations["validsig_fingerprint"] != lock["signer_fingerprint"]:
        raise ValueError("VALIDSIG fingerprint does not match the source lock")

    tool_versions = observations["tool_versions"]
    if (
        not isinstance(tool_versions, dict)
        or not tool_versions
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(version, str)
            or not version.strip()
            for name, version in tool_versions.items()
        )
    ):
        raise ValueError("tool_versions must be a non-empty string mapping")

    _require_posix_path_below(
        observations["source_dir"], lock["wsl_root"], "source_dir"
    )
    _require_posix_path_below(
        observations["build_dir"], lock["wsl_root"], "build_dir"
    )

    verify_locked_file(archive, lock["archive_sha256"])
    baseline_hashes: dict[str, str] = {}
    for relative_value, expected_sha256 in sorted(lock["baseline_files"].items()):
        relative_path = _normalize_relative_path(relative_value)
        baseline_path = project_root.joinpath(*relative_path.parts)
        verify_locked_file(baseline_path, expected_sha256)
        baseline_hashes[relative_path.as_posix()] = expected_sha256.lower()

    return {
        "gate": "PASS",
        "kernel_version": lock["kernel_version"],
        "kernel_release": lock["expected_release"],
        "archive": str(archive),
        "archive_sha256": lock["archive_sha256"].lower(),
        "validsig_fingerprint": observations["validsig_fingerprint"],
        "source_dir": observations["source_dir"],
        "build_dir": observations["build_dir"],
        "gcc_target": observations["gcc_target"],
        "tool_versions": dict(sorted(tool_versions.items())),
        "baseline_files_verified": len(baseline_hashes),
        "baseline_sha256": baseline_hashes,
    }


def parse_kconfig(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            values[line[2 : -len(" is not set")]] = "n"
    return values


def check_config(path: pathlib.Path) -> dict[str, Any]:
    values = parse_kconfig(path)
    wrong_builtin = {
        symbol: values.get(symbol)
        for symbol in sorted(REQUIRED_BUILTIN_CONFIG)
        if values.get(symbol) != "y"
    }
    if wrong_builtin:
        raise ValueError(f"required built-in config mismatch: {wrong_builtin}")
    wrong_driver = {
        symbol: values.get(symbol)
        for symbol in sorted(REQUIRED_DRIVER_CONFIG)
        if values.get(symbol) not in {"y", "m"}
    }
    if wrong_driver:
        raise ValueError(f"required driver config mismatch: {wrong_driver}")
    if values.get("CONFIG_LOCALVERSION") != '"-eaidk310-zramfix1"':
        raise ValueError("CONFIG_LOCALVERSION must be \"-eaidk310-zramfix1\"")
    if values.get("CONFIG_LOCALVERSION_AUTO") != "n":
        raise ValueError("CONFIG_LOCALVERSION_AUTO must be disabled")
    return {
        "config": str(path),
        "kernel_release": "6.12.108-eaidk310-zramfix1",
        "required_builtin_symbols": len(REQUIRED_BUILTIN_CONFIG),
        "required_driver_symbols": len(REQUIRED_DRIVER_CONFIG),
    }


def classify_config_diff_line(line: str) -> str:
    if line.startswith("-"):
        return "removed_upstream"
    symbol = line.lstrip(" +-\t").split(maxsplit=1)[0]
    if symbol in {"BASE_SMALL", "CRYPTO_ARCH_HAVE_LIB_POLY1305"}:
        return "dependency_changed"
    if symbol in {"LOCALVERSION", "LOCALVERSION_AUTO"}:
        return "intentional_eaidk_setting"
    return "default_changed"


def render_config_diff(lines: Iterable[str]) -> tuple[str, dict[str, int]]:
    buckets = {key: [] for key, _ in CONFIG_DIFF_SECTIONS}
    for line in lines:
        if not line.strip():
            continue
        buckets[classify_config_diff_line(line)].append(line)

    output = ["# Linux 6.8.4 to 6.12.108 config migration", ""]
    for key, title in CONFIG_DIFF_SECTIONS:
        output.extend((f"## {title} ({len(buckets[key])})", "", "```text"))
        output.extend(buckets[key] or ["(none)"])
        output.extend(("```", ""))
    output.extend(
        (
            "## Final release settings",
            "",
            "```text",
            'CONFIG_LOCALVERSION="-eaidk310-zramfix1"',
            "# CONFIG_LOCALVERSION_AUTO is not set",
            "```",
            "",
        )
    )
    counts = {key: len(buckets[key]) for key, _ in CONFIG_DIFF_SECTIONS}
    return "\n".join(output), counts


def validate_port_matrix(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError("port matrix must be a JSON object")
    if data.get("schema_version") != 1:
        raise ValueError("unsupported port matrix schema_version")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("port matrix entries must be a JSON array")

    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"port matrix entry {index} must be a JSON object")
        missing = sorted(PORT_MATRIX_FIELDS - entry.keys())
        extra = sorted(entry.keys() - PORT_MATRIX_FIELDS)
        if missing:
            raise ValueError(f"port matrix entry {index} is missing: {', '.join(missing)}")
        if extra:
            raise ValueError(f"port matrix entry {index} has unknown fields: {', '.join(extra)}")

        subsystem = entry["subsystem"]
        if not isinstance(subsystem, str) or not subsystem.strip():
            raise ValueError(f"port matrix entry {index} has an invalid subsystem")
        if subsystem in seen:
            raise ValueError(f"duplicate port matrix subsystem: {subsystem}")
        seen.add(subsystem)

        for field in ("legacy_path", "upstream_label", "rationale"):
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise ValueError(f"port matrix {subsystem} has an invalid {field}")
        for field in ("reference_files", "properties"):
            values = entry[field]
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) or not value.strip() for value in values)
            ):
                raise ValueError(f"port matrix {subsystem} has an invalid {field}")
        if entry["disposition"] not in PORT_DISPOSITIONS:
            raise ValueError(
                f"port matrix {subsystem} has invalid disposition: {entry['disposition']}"
            )

    missing_subsystems = sorted(REQUIRED_PORT_SUBSYSTEMS - seen)
    unexpected_subsystems = sorted(seen - REQUIRED_PORT_SUBSYSTEMS)
    if missing_subsystems or unexpected_subsystems:
        raise ValueError(
            "port matrix subsystem mismatch: "
            f"missing={missing_subsystems}, unexpected={unexpected_subsystems}"
        )

    serialized = json.dumps(data, sort_keys=True).lower()
    unresolved_markers = [marker for marker in ("undecided", "todo", "tbd") if marker in serialized]
    if unresolved_markers:
        raise ValueError(
            f"port matrix contains unresolved markers: {', '.join(unresolved_markers)}"
        )


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_port_matrix(data: dict[str, Any]) -> str:
    validate_port_matrix(data)
    lines = [
        "# EAIDK-310 Linux 6.12 board-port matrix",
        "",
        "Generated from `board-port-matrix.json`; edit the JSON source, not this table.",
        "",
        "| Subsystem | Legacy path | Upstream label | Disposition | Properties | References | Rationale |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in sorted(data["entries"], key=lambda item: item["subsystem"]):
        cells = (
            entry["subsystem"],
            entry["legacy_path"],
            entry["upstream_label"],
            entry["disposition"],
            ", ".join(entry["properties"]),
            ", ".join(entry["reference_files"]),
            entry["rationale"],
        )
        lines.append("| " + " | ".join(_markdown_cell(cell) for cell in cells) + " |")
    lines.append("")
    return "\n".join(lines)


def _normalize_relative_path(value: str) -> pathlib.PurePosixPath:
    normalized = pathlib.PurePosixPath(value.replace("\\", "/"))
    if normalized.is_absolute() or not normalized.parts or normalized == pathlib.PurePosixPath("."):
        raise ValueError(f"manifest path must be relative: {value}")
    if ".." in normalized.parts:
        raise ValueError(f"manifest path must not contain '..': {value}")
    return normalized


def build_manifest(
    root: pathlib.Path,
    relative_paths: Iterable[str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise ValueError("manifest metadata must be a JSON object")
    normalized_paths = [_normalize_relative_path(value) for value in relative_paths]
    path_strings = [path.as_posix() for path in normalized_paths]
    if len(path_strings) != len(set(path_strings)):
        raise ValueError("manifest paths must be unique")

    files = []
    for relative_path in sorted(normalized_paths, key=lambda path: path.as_posix()):
        full_path = root.joinpath(*relative_path.parts)
        if not full_path.is_file():
            raise ValueError(f"manifest input is not a file: {relative_path.as_posix()}")
        files.append(
            {
                "path": relative_path.as_posix(),
                "sha256": sha256_file(full_path),
                "size": full_path.stat().st_size,
            }
        )
    return {"schema_version": 1, "metadata": metadata, "files": files}


def verify_manifest(root: pathlib.Path, manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema_version")
    if not isinstance(manifest.get("metadata"), dict):
        raise ValueError("manifest metadata must be a JSON object")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("manifest files must be a JSON array")

    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("manifest file entry must be a JSON object")
        path_value = entry.get("path")
        if not isinstance(path_value, str):
            raise ValueError("manifest file path must be a string")
        relative_path = _normalize_relative_path(path_value)
        normalized = relative_path.as_posix()
        if normalized in seen:
            raise ValueError(f"duplicate manifest path: {normalized}")
        seen.add(normalized)
        full_path = root.joinpath(*relative_path.parts)
        if not full_path.is_file():
            raise ValueError(f"manifest file is missing: {normalized}")
        expected_size = entry.get("size")
        if full_path.stat().st_size != expected_size:
            raise ValueError(f"size mismatch for {normalized}")
        expected_sha256 = entry.get("sha256")
        if not isinstance(expected_sha256, str):
            raise ValueError(f"SHA-256 is missing for {normalized}")
        verify_locked_file(full_path, expected_sha256)


def render_extlinux(append_line: str) -> str:
    if not append_line.strip() or "\n" in append_line or "\r" in append_line:
        raise ValueError("extlinux APPEND value must be a non-empty single line")
    return (
        "    label rockchip-kernel-6.12.108-eaidk310-zramfix1-test\n"
        "    LINUX  /Image-6.12.108-eaidk310-zramfix1\n"
        "    FDT    /dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb\n"
        "    INITRD /uInitrd-6.12.108-eaidk310-zramfix1\n"
        f"    APPEND {append_line}\n"
    )


def verify_extlinux_deployment(
    config: pathlib.Path,
    boot_root: pathlib.Path,
    expected_default: str,
    planned_references: dict[str, pathlib.Path] | None = None,
) -> dict[str, Any]:
    planned_references = planned_references or {}
    defaults: list[str] = []
    labels: list[str] = []
    entries: dict[str, dict[str, str]] = {}
    current_label: str | None = None
    for line_number, raw_line in enumerate(
        config.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keyword, separator, value = stripped.partition(" ")
        if not separator:
            raise ValueError(f"extlinux line {line_number} has no value")
        keyword = keyword.lower()
        value = value.strip()
        if not value:
            raise ValueError(f"extlinux line {line_number} has an empty value")
        if keyword == "default":
            defaults.append(value)
            continue
        if keyword == "label":
            if value in entries:
                raise ValueError(f"duplicate extlinux label: {value}")
            current_label = value
            labels.append(value)
            entries[value] = {}
            continue
        if keyword in {"linux", "fdt", "initrd"}:
            if current_label is None:
                raise ValueError(f"extlinux {keyword} appears before any label")
            if keyword in entries[current_label]:
                raise ValueError(
                    f"duplicate extlinux {keyword} for label {current_label}"
                )
            entries[current_label][keyword] = value

    if defaults != [expected_default]:
        raise ValueError(
            f"extlinux default must be exactly {expected_default}: {defaults}"
        )
    if expected_default not in entries:
        raise ValueError("extlinux default label does not exist")

    test_label = "rockchip-kernel-6.12.108-eaidk310-zramfix1-test"
    expected_test_entry = {
        "linux": "/Image-6.12.108-eaidk310-zramfix1",
        "fdt": "/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb",
        "initrd": "/uInitrd-6.12.108-eaidk310-zramfix1",
    }
    if entries.get(test_label) != expected_test_entry:
        raise ValueError("versioned extlinux test entry is missing or incorrect")

    boot_root_resolved = boot_root.resolve(strict=True)
    referenced_files = 0
    for label, entry in entries.items():
        missing = sorted({"linux", "fdt", "initrd"} - entry.keys())
        if missing:
            raise ValueError(
                f"extlinux label {label} is missing: {', '.join(missing)}"
            )
        for directive, reference in entry.items():
            if any(character.isspace() for character in reference):
                raise ValueError(f"extlinux {directive} reference contains whitespace")
            relative = pathlib.PurePosixPath(reference)
            if not relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe extlinux file reference: {reference}")
            if reference in planned_references:
                resolved = planned_references[reference].resolve(strict=True)
            else:
                full_path = boot_root.joinpath(*relative.parts[1:])
                resolved = full_path.resolve(strict=True)
                if boot_root_resolved != resolved and boot_root_resolved not in resolved.parents:
                    raise ValueError(f"extlinux reference escapes boot root: {reference}")
            if not resolved.is_file():
                raise ValueError(f"extlinux reference is not a file: {reference}")
            referenced_files += 1

    return {
        "default": expected_default,
        "gate": "PASS",
        "labels": labels,
        "referenced_files": referenced_files,
    }


def verify_bundle_layout(
    root: pathlib.Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    verify_manifest(root, manifest)
    manifest_paths = {entry["path"] for entry in manifest["files"]}
    forbidden = sorted(FORBIDDEN_STABLE_BOOT_PATHS & manifest_paths)
    if forbidden:
        raise ValueError(f"forbidden stable boot path: {', '.join(forbidden)}")

    missing = sorted(BUNDLE_REQUIRED_FILES - manifest_paths)
    if missing:
        raise ValueError(f"bundle is missing required files: {', '.join(missing)}")

    module_prefix = f"root/lib/modules/{KERNEL_RELEASE}/kernel/"
    if not any(path.startswith(module_prefix) for path in manifest_paths):
        raise ValueError("bundle has no versioned kernel module files")
    if "manifest.json" in manifest_paths:
        raise ValueError("manifest must not list itself")

    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    expected_paths = manifest_paths | {"manifest.json"}
    unmanifested = sorted(actual_paths - expected_paths)
    if unmanifested:
        raise ValueError(f"bundle contains unmanifested files: {', '.join(unmanifested)}")
    absent = sorted(expected_paths - actual_paths)
    if absent:
        raise ValueError(f"bundle manifest paths are absent: {', '.join(absent)}")
    return {
        "gate": "PASS",
        "kernel_release": KERNEL_RELEASE,
        "manifest_files": len(manifest_paths),
    }


def verify_early_boot_dependencies(module_dir: pathlib.Path) -> dict[str, Any]:
    builtin_path = module_dir / "modules.builtin"
    dependencies_path = module_dir / "modules.dep"
    builtin = set(builtin_path.read_text(encoding="utf-8").splitlines())
    modular = {
        line.split(":", 1)[0]
        for line in dependencies_path.read_text(encoding="utf-8").splitlines()
        if ":" in line
    }
    providers: dict[str, dict[str, str]] = {}
    missing = []
    for capability, relative_path in sorted(EARLY_BOOT_DEPENDENCIES.items()):
        if relative_path in builtin:
            providers[capability] = {"kind": "builtin", "path": relative_path}
        elif relative_path in modular:
            full_path = module_dir.joinpath(*pathlib.PurePosixPath(relative_path).parts)
            if not full_path.is_file():
                raise ValueError(f"module provider is missing: {relative_path}")
            providers[capability] = {"kind": "module", "path": relative_path}
        else:
            missing.append(capability)
    if missing:
        raise ValueError(f"early boot dependencies are missing: {', '.join(missing)}")
    return {"gate": "PASS", "providers": providers}


def _write_json_atomic(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def _write_text_atomic(path: pathlib.Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(value, encoding="utf-8")
    os.replace(temporary_path, path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_lock = subparsers.add_parser("verify-lock")
    verify_lock.add_argument("path", type=pathlib.Path)

    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--root", required=True, type=pathlib.Path)
    manifest.add_argument("--output", required=True, type=pathlib.Path)
    manifest.add_argument("--metadata-json", required=True, type=pathlib.Path)
    manifest.add_argument("--file", required=True, action="append", dest="files")

    verify_manifest_parser = subparsers.add_parser("verify-manifest")
    verify_manifest_parser.add_argument("--root", required=True, type=pathlib.Path)
    verify_manifest_parser.add_argument("--manifest", required=True, type=pathlib.Path)

    check_config_parser = subparsers.add_parser("check-config")
    check_config_parser.add_argument("--config", required=True, type=pathlib.Path)

    render_config_diff_parser = subparsers.add_parser("render-config-diff")
    render_config_diff_parser.add_argument("--input", required=True, type=pathlib.Path)
    render_config_diff_parser.add_argument("--output", required=True, type=pathlib.Path)

    render_port_matrix_parser = subparsers.add_parser("render-port-matrix")
    render_port_matrix_parser.add_argument("--input", required=True, type=pathlib.Path)
    render_port_matrix_parser.add_argument("--output", required=True, type=pathlib.Path)

    input_report_parser = subparsers.add_parser("input-report")
    input_report_parser.add_argument("--lock", required=True, type=pathlib.Path)
    input_report_parser.add_argument("--project-root", required=True, type=pathlib.Path)
    input_report_parser.add_argument("--archive", required=True, type=pathlib.Path)
    input_report_parser.add_argument(
        "--observations-json", required=True, type=pathlib.Path
    )
    input_report_parser.add_argument("--output", required=True, type=pathlib.Path)

    render_extlinux_parser = subparsers.add_parser("render-extlinux")
    render_extlinux_parser.add_argument("--append-line", required=True)
    render_extlinux_parser.add_argument("--output", required=True, type=pathlib.Path)

    verify_bundle_layout_parser = subparsers.add_parser("verify-bundle-layout")
    verify_bundle_layout_parser.add_argument("--root", required=True, type=pathlib.Path)
    verify_bundle_layout_parser.add_argument(
        "--manifest", required=True, type=pathlib.Path
    )

    verify_early_deps_parser = subparsers.add_parser("verify-early-deps")
    verify_early_deps_parser.add_argument(
        "--module-dir", required=True, type=pathlib.Path
    )

    verify_extlinux_parser = subparsers.add_parser("verify-extlinux-deployment")
    verify_extlinux_parser.add_argument("--config", required=True, type=pathlib.Path)
    verify_extlinux_parser.add_argument(
        "--boot-root", required=True, type=pathlib.Path
    )
    verify_extlinux_parser.add_argument("--expected-default", required=True)
    verify_extlinux_parser.add_argument(
        "--planned-reference", action="append", default=[], dest="planned_references"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "verify-lock":
            print(json.dumps(load_lock(args.path), indent=2, sort_keys=True))
            return 0
        if args.command == "manifest":
            metadata = json.loads(args.metadata_json.read_text(encoding="utf-8"))
            value = build_manifest(args.root, args.files, metadata)
            _write_json_atomic(args.output, value)
            print(json.dumps({"files": len(value["files"]), "output": str(args.output)}))
            return 0
        if args.command == "verify-manifest":
            value = json.loads(args.manifest.read_text(encoding="utf-8"))
            verify_manifest(args.root, value)
            print(json.dumps({"verified_files": len(value["files"])}))
            return 0
        if args.command == "check-config":
            print(json.dumps(check_config(args.config), indent=2, sort_keys=True))
            return 0
        if args.command == "render-config-diff":
            report, counts = render_config_diff(
                args.input.read_text(encoding="utf-8").splitlines()
            )
            _write_text_atomic(args.output, report)
            print(json.dumps({"counts": counts, "output": str(args.output)}, sort_keys=True))
            return 0
        if args.command == "render-port-matrix":
            data = json.loads(args.input.read_text(encoding="utf-8"))
            report = render_port_matrix(data)
            _write_text_atomic(args.output, report)
            subsystems = sorted(entry["subsystem"] for entry in data["entries"])
            print(
                json.dumps(
                    {
                        "entries": len(data["entries"]),
                        "output": str(args.output),
                        "subsystems": subsystems,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "input-report":
            lock = load_lock(args.lock)
            observations = json.loads(args.observations_json.read_text(encoding="utf-8"))
            if not isinstance(observations, dict):
                raise ValueError("input observations must be a JSON object")
            report = build_input_report(
                lock, args.project_root, args.archive, observations
            )
            _write_json_atomic(args.output, report)
            print(json.dumps(report, sort_keys=True))
            return 0
        if args.command == "render-extlinux":
            report = render_extlinux(args.append_line)
            _write_text_atomic(args.output, report)
            print(json.dumps({"output": str(args.output)}, sort_keys=True))
            return 0
        if args.command == "verify-bundle-layout":
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            report = verify_bundle_layout(args.root, manifest)
            print(json.dumps(report, sort_keys=True))
            return 0
        if args.command == "verify-early-deps":
            report = verify_early_boot_dependencies(args.module_dir)
            print(json.dumps(report, sort_keys=True))
            return 0
        if args.command == "verify-extlinux-deployment":
            planned_references: dict[str, pathlib.Path] = {}
            for value in args.planned_references:
                reference, separator, source = value.partition("=")
                if not separator or not reference.startswith("/") or not source:
                    raise ValueError(
                        "planned reference must have the form /BOOT/REFERENCE=SOURCE"
                    )
                if reference in planned_references:
                    raise ValueError(f"duplicate planned reference: {reference}")
                planned_references[reference] = pathlib.Path(source)
            report = verify_extlinux_deployment(
                args.config,
                args.boot_root,
                args.expected_default,
                planned_references,
            )
            print(json.dumps(report, sort_keys=True))
            return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
