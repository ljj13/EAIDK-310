#!/usr/bin/env python3
"""Generate a kernel source-lock candidate for a new upstream version.

The production source-lock.json files are review gates; this tool never
touches them.  It fetches the upstream tarball + detached signature,
verifies SHA-256 and the GPG signature, and writes a candidate lock:

    python3 tools/update-kernel-lock.py 6.18.60
    # -> ./source-lock.candidate.json  (review, then move into place)

The candidate omits the legacy wsl_root field on purpose: the release
pipeline (tools/release-kernel.py) derives its workspace from
EAIDK_WORK_ROOT and never depends on a machine-specific path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

DEFAULT_KEYSERVER = "hkps://keyserver.ubuntu.com"


def download(url: str, target: Path) -> None:
    print(f"fetching {url}")
    with urllib.request.urlopen(url, timeout=180) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gpg_validsig(homedir: Path, signature: Path, archive: Path) -> str:
    # kernel.org signs the UNCOMPRESSED tar; stream it through xz into gpg
    proc = subprocess.run(
        ["bash", "-o", "pipefail", "-c",
         f"xz -dc {shlex.quote(str(archive))} | gpg --homedir {shlex.quote(str(homedir))} "
         f"--batch --status-fd=1 --verify {shlex.quote(str(signature))} -"],
        capture_output=True, text=True)
    fingerprint = ""
    for line in proc.stdout.splitlines():
        if line.startswith("[GNUPG:] VALIDSIG "):
            fingerprint = line.split()[2]
    if proc.returncode != 0 or not fingerprint:
        raise SystemExit(f"GPG verification failed:\n{proc.stdout}\n{proc.stderr}")
    return fingerprint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="upstream kernel version, e.g. 6.18.60")
    parser.add_argument("--out", default="source-lock.candidate.json",
                        help="candidate output path (default: ./source-lock.candidate.json)")
    parser.add_argument("--downloads-dir", default=os.environ.get(
        "EAIDK_CACHE_ROOT", os.path.join(os.environ.get("XDG_CACHE_HOME",
                                                         os.path.expanduser("~/.cache")),
                                         "eaidk310", "downloads")))
    parser.add_argument("--gnupg-home", default=os.environ.get(
        "EAIDK_CACHE_ROOT", os.path.join(os.environ.get("XDG_CACHE_HOME",
                                                         os.path.expanduser("~/.cache")),
                                         "eaidk310", "gnupg")))
    parser.add_argument("--localversion", default="zramfix1")
    parser.add_argument("--keyserver", default=DEFAULT_KEYSERVER)
    args = parser.parse_args(argv)

    base = f"https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{args.version}"
    archive_url = f"{base}.tar.xz"
    signature_url = f"{base}.tar.sign"
    downloads = Path(args.downloads_dir)
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / f"linux-{args.version}.tar.xz"
    signature = downloads / f"linux-{args.version}.tar.sign"
    if not archive.is_file():
        download(archive_url, archive)
    else:
        print(f"reusing cached {archive.name}")
    if not signature.is_file():
        download(signature_url, signature)

    digest = sha256_file(archive)
    print(f"sha256 = {digest}")

    keyring = Path(args.gnupg_home)
    keyring.mkdir(parents=True, exist_ok=True)
    import stat as stat_module
    os.chmod(keyring, stat_module.S_IRWXU)
    probe = subprocess.run(["gpg", "--homedir", str(keyring), "--list-keys",
                            "greg@kernel.org"], capture_output=True)
    if probe.returncode != 0:
        print("no kernel.org key in cache keyring; fetching from keyserver")
        subprocess.run(["gpg", "--homedir", str(keyring), "--batch",
                        "--keyserver", args.keyserver, "--recv-keys", "greg@kernel.org"],
                       check=True)
    fingerprint = gpg_validsig(keyring, signature, archive)
    print(f"signer fingerprint = {fingerprint}")

    candidate = {
        "kernel_version": args.version,
        "localversion": args.localversion,
        "expected_release": f"{args.version}-{args.localversion}",
        "archive_url": archive_url,
        "signature_url": signature_url,
        "archive_sha256": digest,
        "signer_fingerprint": fingerprint,
    }
    out = Path(args.out)
    out.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    print(f"wrote candidate lock: {out}")
    print("review it, then move it to kernel/linux-<version>-zramfix1/source-lock.json "
          "(never overwrite an existing production lock without review)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
