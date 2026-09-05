#!/usr/bin/env python3
"""Prepare file-backed EAIDK310 U-Boot artifacts without raw disk access."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any

import rockchip_loaderimage


LOCKED_BASELINE_SHA256 = (
    "6254986C3E1E12D942D35769A8D8182422A017B6CA392237D0F284B31490A3EB"
)


def _file_path(value: str) -> pathlib.Path:
    normalized = value.lower().replace("/", "\\")
    if (
        normalized.startswith(r"\\.\physicaldrive")
        or normalized.startswith(r"\\?\physicaldrive")
        or value.lower().startswith("/dev/")
    ):
        raise argparse.ArgumentTypeError("raw disk paths are not accepted")
    return pathlib.Path(value)


def _parse_integer(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid integer: {value}") from error


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _region_report(region: bytes, *, require_public_crc: bool = False) -> dict[str, Any]:
    results = rockchip_loaderimage.verify_redundant_uboot_image(
        region, require_public_crc=require_public_crc
    )
    header = results[0].header
    return {
        "slot_count": len(results),
        "load_address": header.load_address,
        "payload_size": header.load_size,
        "sha256": header.sha256.hex(),
        "header_sha256": [result.header.sha256.hex() for result in results],
        "region_sha256": _sha256(region),
        "public_crc_matches": all(result.crc32_matches for result in results),
    }


def _write_bytes(path: pathlib.Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)


def _write_json(path: pathlib.Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _emit(report: dict[str, Any]) -> None:
    print(json.dumps(report, indent=2, sort_keys=True))


def _verify(arguments: argparse.Namespace) -> dict[str, Any]:
    return _region_report(
        arguments.region.read_bytes(),
        require_public_crc=arguments.require_public_crc,
    )


def _pack(arguments: argparse.Namespace) -> dict[str, Any]:
    payload = arguments.payload.read_bytes()
    region = rockchip_loaderimage.build_redundant_uboot_image(
        payload, load_address=arguments.load_address
    )
    report = _region_report(region, require_public_crc=True)
    _write_bytes(arguments.output_region, region)
    report.update(
        {
            "output_region": str(arguments.output_region),
            "source_payload_sha256": _sha256(payload),
        }
    )
    return report


def _compose(arguments: argparse.Namespace) -> dict[str, Any]:
    baseline = arguments.baseline.read_bytes()
    region = arguments.region.read_bytes()
    prefix = rockchip_loaderimage.compose_prefix(
        baseline,
        region,
        LOCKED_BASELINE_SHA256,
        require_public_crc=True,
    )
    region_report = _region_report(region, require_public_crc=True)
    report = {
        **region_report,
        "baseline_sha256": _sha256(baseline),
        "prefix_sha256": _sha256(prefix),
        "changed_ranges": [
            [start, end]
            for start, end in rockchip_loaderimage.changed_ranges(baseline, prefix)
        ],
        # These fields are emitted only after the baseline hash and region CRC
        # gates above have passed, so the manifest is trustworthy as a write
        # source the same way package-artifacts.ps1 marks its own manifests.
        "writable": True,
        "variant": arguments.variant,
    }
    _write_bytes(arguments.output_prefix, prefix)
    _write_json(arguments.manifest, report)
    return report


def _diff(arguments: argparse.Namespace) -> dict[str, Any]:
    before = arguments.before.read_bytes()
    after = arguments.after.read_bytes()
    return {
        "before_sha256": _sha256(before),
        "after_sha256": _sha256(after),
        "changed_ranges": [
            [start, end]
            for start, end in rockchip_loaderimage.changed_ranges(before, after)
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare EAIDK310 file images; raw disks are never accepted."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--region", required=True, type=_file_path)
    verify_parser.add_argument("--require-public-crc", action="store_true")
    verify_parser.set_defaults(handler=_verify)

    pack_parser = subparsers.add_parser("pack")
    pack_parser.add_argument("--payload", required=True, type=_file_path)
    pack_parser.add_argument("--output-region", required=True, type=_file_path)
    pack_parser.add_argument(
        "--load-address", required=True, type=_parse_integer
    )
    pack_parser.set_defaults(handler=_pack)

    compose_parser = subparsers.add_parser("compose")
    compose_parser.add_argument("--baseline", required=True, type=_file_path)
    compose_parser.add_argument("--region", required=True, type=_file_path)
    compose_parser.add_argument("--output-prefix", required=True, type=_file_path)
    compose_parser.add_argument("--manifest", required=True, type=_file_path)
    compose_parser.add_argument(
        "--variant", default="control", help="variant label recorded in the manifest"
    )
    compose_parser.set_defaults(handler=_compose)

    diff_parser = subparsers.add_parser("diff")
    diff_parser.add_argument("--before", required=True, type=_file_path)
    diff_parser.add_argument("--after", required=True, type=_file_path)
    diff_parser.set_defaults(handler=_diff)
    return parser


def main() -> int:
    parser = _build_parser()
    arguments = parser.parse_args()
    try:
        report = arguments.handler(arguments)
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")
    _emit(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
