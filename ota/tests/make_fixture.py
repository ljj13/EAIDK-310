#!/usr/bin/env python3
"""Fixture bundle builder for eaidk-ota tests. Runs inside WSL (needs tar+zstd).

Usage:
  python3 make_fixture.py --out DIR --release RELEASE --variant VARIANT

Variants:
  valid | wrong-sha | corrupt-size | missing-dtb | wrong-dtb | wrong-release
  polluted | traversal
"""
import argparse
import hashlib
import io
import json
import pathlib
import subprocess
import tarfile


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(out: pathlib.Path, release: str, variant: str) -> pathlib.Path:
    top = f"eaidk310-linux-{release}"
    root = out / top
    boot = root / "boot"
    dtbd = boot / "dtb" / "rockchip"
    mods = root / "root" / "lib" / "modules" / release / "kernel"
    for d in (boot, dtbd, mods):
        d.mkdir(parents=True, exist_ok=True)

    def blob(name: str) -> bytes:
        payload = release.encode()
        filler = 0x01 if variant == "valid-modified" else 0x00
        return payload + bytes([filler]) * ((6 << 20) - len(payload) - 1)

    img = boot / f"Image-{release}"
    img.write_bytes(blob(release))
    uin = boot / f"uInitrd-{release}"
    uin.write_bytes(blob(release))
    dtb = dtbd / f"rk3328-eaidk-310-{release}.dtb"
    marker = (b"openailab,eaidk-310\0rockchip,rk3328\0"
              if variant != "wrong-dtb"
              else b"acme,not-a-board\0rockchip,rk3399\0")
    dtb.write_bytes(b"\0" * 512 + marker + b"\0" * 512)
    ko = mods / "board_test.ko"
    ko.write_bytes(b"fake-module")

    entries = []

    def add(p: pathlib.Path):
        entries.append({"path": str(p.relative_to(root)),
                        "sha256": sha(p), "size": p.stat().st_size})

    if variant != "missing-dtb":
        add(dtb)
    add(img)
    add(uin)
    add(ko)

    report = {"gate": "PASS", "kernel_release": release, "module_files": 1,
              "image_sha256": sha(img), "uinitrd_sha256": sha(uin),
              "dtb_sha256": sha(dtb), "deployment_enabled": True,
              "scope": "fixture"}
    if variant == "wrong-release":
        report["kernel_release"] = "0.0.1-fake"
    vr = root / "verification-report.json"
    vr.write_text(json.dumps(report, indent=2) + "\n")
    add(vr)

    if variant == "polluted":
        old = root / "root" / "lib" / "modules" / "6.12.108-eaidk310-zramfix1" / "kernel" / "old.ko"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b"old-module")
        add(old)

    manifest = {"files": entries}
    if variant == "wrong-sha":
        for e in manifest["files"]:
            if e["path"].endswith(f"Image-{release}"):
                e["sha256"] = "0" * 64
    if variant == "corrupt-size":
        for e in manifest["files"]:
            if e["path"].endswith(f"Image-{release}"):
                e["size"] += 1
    mf = root / "manifest.json"
    mf.write_text(json.dumps(manifest, indent=2) + "\n")

    tar_path = out / f"eaidk310-linux-{release}.tar"
    with tarfile.open(tar_path, "w") as tf:
        tf.add(root, arcname=top)
        if variant == "traversal":
            payload = b"pwned-by-path-traversal\n"
            info = tarfile.TarInfo("../evil-escape.txt")
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    final = out / f"eaidk310-linux-{release}.tar.zst"
    subprocess.run(["zstd", "-q", "-f", str(tar_path), "-o", str(final)], check=True)
    tar_path.unlink()
    digest = sha(final)
    pathlib.Path(str(final) + ".sha256").write_text(digest + "\n")
    print(str(final))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--release", default="6.18.54-eaidk310-zramfix1")
    ap.add_argument("--variant", default="valid")
    a = ap.parse_args()
    build(pathlib.Path(a.out), a.release, a.variant)
