#!/usr/bin/env bash
set -euo pipefail

RELEASE="6.12.108-eaidk310-zramfix1"
BUNDLE_NAME="eaidk310-linux-$RELEASE"
EXPECTED_ROOT_SOURCE="/dev/mmcblk0p2"
EXPECTED_BOOT_SOURCE="/dev/mmcblk0p1"
EXPECTED_RUNNING_KERNEL="6.8.4-rk3328"
EXPECTED_DEFAULT="rockchip-kernel-6.8.4"
TEST_LABEL="rockchip-kernel-6.12.108-eaidk310-zramfix1-test"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_BUNDLE_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
BUNDLE_ROOT="$DEFAULT_BUNDLE_ROOT"
BUNDLE_ARCHIVE="$DEFAULT_BUNDLE_ROOT.tar.zst"
BUNDLE_SHA256=""
ROOT_PREFIX="/"
APPLY=0

fail() {
	printf 'DEPLOY_GATE=FAIL: %s\n' "$*" >&2
	exit 1
}

usage() {
	cat <<'EOF'
usage: deploy-rescue-tf.sh [--apply] [--bundle-sha256 SHA256]
                           [--bundle-root PATH] [--bundle-archive PATH]
                           [--root-prefix PATH]

Without --apply this performs a read-only dry run. --root-prefix is accepted
only when EAIDK310_TEST_MODE=1 and exists solely for the fake-root test matrix.
EOF
}

while (($#)); do
	case "$1" in
		--apply) APPLY=1; shift ;;
		--bundle-sha256) BUNDLE_SHA256="${2:?missing value for --bundle-sha256}"; shift 2 ;;
		--bundle-root) BUNDLE_ROOT="${2:?missing value for --bundle-root}"; shift 2 ;;
		--bundle-archive) BUNDLE_ARCHIVE="${2:?missing value for --bundle-archive}"; shift 2 ;;
		--root-prefix) ROOT_PREFIX="${2:?missing value for --root-prefix}"; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) fail "unknown argument: $1" ;;
	esac
done

if ((APPLY)); then
	printf 'write=true\n' >&2
else
	printf 'write=false\n' >&2
fi

for tool in awk cmp cp date df diff du find findmnt grep install mkdir mktemp mv \
	python3 realpath sha256sum stat sync tar uname zstd; do
	command -v "$tool" >/dev/null || fail "required tool is missing: $tool"
done

if [[ "$ROOT_PREFIX" != "/" ]]; then
	[[ "${EAIDK310_TEST_MODE:-0}" == 1 ]] || \
		fail "--root-prefix requires EAIDK310_TEST_MODE=1"
	[[ -d "$ROOT_PREFIX" ]] || fail "root prefix is not a directory: $ROOT_PREFIX"
	if ! ROOT_PREFIX="$(realpath -e "$ROOT_PREFIX")"; then
		fail "could not resolve root prefix: $ROOT_PREFIX"
	fi
elif [[ "${EAIDK310_TEST_MODE:-0}" == 1 ]]; then
	fail "test mode requires an explicit non-root --root-prefix"
fi

if ! BUNDLE_ROOT="$(realpath -e "$BUNDLE_ROOT")"; then
	fail "could not resolve bundle root: $BUNDLE_ROOT"
fi
if ! BUNDLE_ARCHIVE="$(realpath -e "$BUNDLE_ARCHIVE")"; then
	fail "could not resolve bundle archive: $BUNDLE_ARCHIVE"
fi
[[ "$(basename -- "$BUNDLE_ROOT")" == "$BUNDLE_NAME" ]] || \
	fail "bundle directory name must be $BUNDLE_NAME"

MANIFEST="$BUNDLE_ROOT/manifest.json"
HELPER="$BUNDLE_ROOT/deploy/kernel_artifacts.py"
BASELINE_LOCK="$BUNDLE_ROOT/deploy/stable-baseline-sha256.txt"
ENTRY_FILE="$BUNDLE_ROOT/deploy/extlinux-entry.conf"
for required in "$MANIFEST" "$HELPER" "$BASELINE_LOCK" "$ENTRY_FILE"; do
	[[ -f "$required" && ! -L "$required" ]] || fail "required bundle file is missing: $required"
done

if [[ -z "$BUNDLE_SHA256" ]]; then
	if ((APPLY)); then
		fail "--apply requires --bundle-sha256"
	fi
	sha_file="$BUNDLE_ARCHIVE.sha256"
	[[ -f "$sha_file" ]] || fail "provide --bundle-sha256 for this dry run"
	read -r BUNDLE_SHA256 _ < "$sha_file"
fi
[[ "$BUNDLE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "bundle SHA-256 must be 64 lowercase hex characters"
actual_archive_sha="$(sha256sum "$BUNDLE_ARCHIVE" | awk '{print $1}')"
[[ "$actual_archive_sha" == "$BUNDLE_SHA256" ]] || \
	fail "bundle SHA-256 mismatch: expected $BUNDLE_SHA256, got $actual_archive_sha"

SCRATCH="$(mktemp -d)"
trap 'rm -rf -- "$SCRATCH"' EXIT
if ! zstd -dc -- "$BUNDLE_ARCHIVE" | \
	tar -xOf - "$BUNDLE_NAME/manifest.json" > "$SCRATCH/archive-manifest.json"; then
	fail "could not read manifest from the bundle archive"
fi
cmp -s "$MANIFEST" "$SCRATCH/archive-manifest.json" || \
	fail "bundle manifest differs from the hash-bound archive"
if ! python3 "$HELPER" verify-bundle-layout \
	--root "$BUNDLE_ROOT" --manifest "$MANIFEST" > "$SCRATCH/bundle-gate.json"; then
	fail "bundle manifest verification failed"
fi
python3 - "$MANIFEST" <<'PY' || fail "bundle manifest does not enable deployment"
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if manifest.get("metadata", {}).get("deployment_enabled") is not True:
    raise SystemExit(1)
PY

ROOT_SOURCE="$(findmnt -n -o SOURCE /)"
BOOT_SOURCE="$(findmnt -n -o SOURCE /boot)"
[[ "$ROOT_SOURCE" == "$EXPECTED_ROOT_SOURCE" ]] || \
	fail "root source must be $EXPECTED_ROOT_SOURCE, got $ROOT_SOURCE"
[[ "$BOOT_SOURCE" == "$EXPECTED_BOOT_SOURCE" ]] || \
	fail "boot source must be $EXPECTED_BOOT_SOURCE, got $BOOT_SOURCE"
if findmnt -rn -o SOURCE | grep -Eq '^/dev/mmcblk2([p0-9]|$)'; then
	fail "eMMC /dev/mmcblk2 is mounted; deployment is restricted to the rescue TF"
fi
CURRENT_KERNEL="$(uname -r)"
[[ "$CURRENT_KERNEL" == "$EXPECTED_RUNNING_KERNEL" ]] || \
	fail "running kernel must be $EXPECTED_RUNNING_KERNEL, got $CURRENT_KERNEL"

BOOT_ROOT="$ROOT_PREFIX/boot"
MODULES_ROOT="$ROOT_PREFIX/lib/modules"
CURRENT_EXTLINUX="$BOOT_ROOT/extlinux/extlinux.conf"
[[ -f "$CURRENT_EXTLINUX" && ! -L "$CURRENT_EXTLINUX" ]] || \
	fail "extlinux configuration is missing or unsafe"
mapfile -t current_defaults < <(
	awk 'tolower($1)=="default" {print $2}' "$CURRENT_EXTLINUX"
)
[[ "${#current_defaults[@]}" == 1 && "${current_defaults[0]}" == "$EXPECTED_DEFAULT" ]] || \
	fail "extlinux default must remain exactly $EXPECTED_DEFAULT"

declare -A EXPECTED_STABLE=()
allowed_stable=(
	"boot/Image"
	"boot/uInitrd"
	"boot/dtb/rockchip/rk3328-eaidk-310.dtb"
	"boot/extlinux/extlinux.conf"
)
while read -r expected_hash relative_path extra; do
	[[ -n "${expected_hash:-}" ]] || continue
	[[ -z "${extra:-}" ]] || fail "invalid stable baseline lock line"
	[[ "$expected_hash" =~ ^[0-9a-f]{64}$ ]] || fail "invalid stable SHA-256 for $relative_path"
	case "$relative_path" in
		boot/Image|boot/uInitrd|boot/dtb/rockchip/rk3328-eaidk-310.dtb|boot/extlinux/extlinux.conf) ;;
		*) fail "unexpected stable baseline path: $relative_path" ;;
	esac
	[[ -z "${EXPECTED_STABLE[$relative_path]+x}" ]] || fail "duplicate stable baseline path: $relative_path"
	EXPECTED_STABLE["$relative_path"]="$expected_hash"
done < "$BASELINE_LOCK"
[[ "${#EXPECTED_STABLE[@]}" == "${#allowed_stable[@]}" ]] || fail "stable baseline lock is incomplete"

for relative_path in "${allowed_stable[@]}"; do
	target="$ROOT_PREFIX/$relative_path"
	[[ -f "$target" && ! -L "$target" ]] || fail "stable file is missing or unsafe: /$relative_path"
	actual_hash="$(sha256sum "$target" | awk '{print $1}')"
	[[ "$actual_hash" == "${EXPECTED_STABLE[$relative_path]}" ]] || \
		fail "stable file hash mismatch: /$relative_path"
done

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$RUN_ID" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || fail "invalid run identifier"
BACKUP_DIR="$BOOT_ROOT/eaidk310-backups/$RUN_ID"
[[ ! -e "$BACKUP_DIR" ]] || fail "backup directory already exists: $BACKUP_DIR"

for directory in "$MODULES_ROOT" "$BOOT_ROOT" "$BOOT_ROOT/dtb/rockchip" "$BOOT_ROOT/extlinux"; do
	[[ -d "$directory" ]] || fail "required target directory is missing: $directory"
done
if find "$MODULES_ROOT" -maxdepth 1 -name ".$RELEASE.*.tmp" -print -quit | grep -q . || \
	find "$BOOT_ROOT" -maxdepth 1 \( -name ".Image-$RELEASE.*.tmp" -o -name ".uInitrd-$RELEASE.*.tmp" \) -print -quit | grep -q . || \
	find "$BOOT_ROOT/dtb/rockchip" -maxdepth 1 -name '.rk3328-eaidk-310-6.12.108.dtb.*.tmp' -print -quit | grep -q . || \
	find "$BOOT_ROOT/extlinux" -maxdepth 1 -name '.extlinux.conf.*.tmp' -print -quit | grep -q .; then
	fail "interrupted deployment temporary path is present"
fi

SOURCE_IMAGE="$BUNDLE_ROOT/boot/Image-$RELEASE"
SOURCE_UINITRD="$BUNDLE_ROOT/boot/uInitrd-$RELEASE"
SOURCE_DTB="$BUNDLE_ROOT/boot/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb"
SOURCE_MODULES="$BUNDLE_ROOT/root/lib/modules/$RELEASE"
DEST_IMAGE="$BOOT_ROOT/Image-$RELEASE"
DEST_UINITRD="$BOOT_ROOT/uInitrd-$RELEASE"
DEST_DTB="$BOOT_ROOT/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb"
DEST_MODULES="$MODULES_ROOT/$RELEASE"
for source in "$SOURCE_IMAGE" "$SOURCE_UINITRD" "$SOURCE_DTB" "$SOURCE_MODULES/modules.dep"; do
	[[ -f "$source" ]] || fail "bundle source is missing: $source"
done

check_file_conflict() {
	local source="$1" destination="$2"
	if [[ -e "$destination" || -L "$destination" ]]; then
		[[ -f "$destination" && ! -L "$destination" ]] || fail "versioned destination conflict: $destination"
		cmp -s "$source" "$destination" || fail "versioned destination conflict: $destination"
	fi
}
check_file_conflict "$SOURCE_IMAGE" "$DEST_IMAGE"
check_file_conflict "$SOURCE_UINITRD" "$DEST_UINITRD"
check_file_conflict "$SOURCE_DTB" "$DEST_DTB"
if [[ -e "$DEST_MODULES" || -L "$DEST_MODULES" ]]; then
	[[ -d "$DEST_MODULES" && ! -L "$DEST_MODULES" ]] || fail "versioned destination conflict: $DEST_MODULES"
	diff -qr "$SOURCE_MODULES" "$DEST_MODULES" >/dev/null || fail "versioned destination conflict: $DEST_MODULES"
fi

grep -Eq "^[[:space:]]*label[[:space:]]+$TEST_LABEL[[:space:]]*$" "$CURRENT_EXTLINUX" && \
	fail "versioned extlinux test label already exists"
CANDIDATE_EXTLINUX="$SCRATCH/extlinux.conf"
cp -- "$CURRENT_EXTLINUX" "$CANDIDATE_EXTLINUX"
printf '\n' >> "$CANDIDATE_EXTLINUX"
cat "$ENTRY_FILE" >> "$CANDIDATE_EXTLINUX"

if ! python3 "$HELPER" verify-extlinux-deployment \
	--config "$CANDIDATE_EXTLINUX" --boot-root "$BOOT_ROOT" \
	--expected-default "$EXPECTED_DEFAULT" \
	--planned-reference "/Image-$RELEASE=$SOURCE_IMAGE" \
	--planned-reference "/uInitrd-$RELEASE=$SOURCE_UINITRD" \
	--planned-reference "/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb=$SOURCE_DTB" \
	> "$SCRATCH/extlinux-gate.json"; then
	fail "candidate extlinux verification failed"
fi

MODULE_BYTES="$(du -sb "$SOURCE_MODULES" | awk '{print $1}')"
ROOT_COPY_BYTES=0
[[ -e "$DEST_MODULES" ]] || ROOT_COPY_BYTES="$MODULE_BYTES"
BOOT_COPY_BYTES=0
for pair in "$SOURCE_IMAGE|$DEST_IMAGE" "$SOURCE_UINITRD|$DEST_UINITRD" "$SOURCE_DTB|$DEST_DTB"; do
	source="${pair%%|*}"
	destination="${pair#*|}"
	[[ -e "$destination" ]] || BOOT_COPY_BYTES=$((BOOT_COPY_BYTES + $(stat -c %s "$source")))
done
ROOT_REQUIRED_BYTES=$((67108864 + ROOT_COPY_BYTES))
BOOT_REQUIRED_BYTES=$((16777216 + BOOT_COPY_BYTES + 2 * $(stat -c %s "$CANDIDATE_EXTLINUX")))
ROOT_AVAILABLE_BYTES="$(df -PB1 "$ROOT_PREFIX" | awk 'NR==2 {print $4}')"
BOOT_AVAILABLE_BYTES="$(df -PB1 "$BOOT_ROOT" | awk 'NR==2 {print $4}')"
[[ "$ROOT_AVAILABLE_BYTES" =~ ^[0-9]+$ && "$BOOT_AVAILABLE_BYTES" =~ ^[0-9]+$ ]] || \
	fail "could not determine available space"
((ROOT_AVAILABLE_BYTES >= ROOT_REQUIRED_BYTES)) || fail "insufficient root filesystem space"
((BOOT_AVAILABLE_BYTES >= BOOT_REQUIRED_BYTES)) || fail "insufficient boot filesystem space"

diff -u "$CURRENT_EXTLINUX" "$CANDIDATE_EXTLINUX" > "$SCRATCH/extlinux.diff" || true
export DEPLOY_MODE="$([[ "$APPLY" == 1 ]] && printf apply || printf dry-run)"
export DEPLOY_BUNDLE_SHA256="$BUNDLE_SHA256"
export DEPLOY_CURRENT_KERNEL="$CURRENT_KERNEL"
export DEPLOY_ROOT_SOURCE="$ROOT_SOURCE"
export DEPLOY_BOOT_SOURCE="$BOOT_SOURCE"
export DEPLOY_ROOT_REQUIRED_BYTES="$ROOT_REQUIRED_BYTES"
export DEPLOY_BOOT_REQUIRED_BYTES="$BOOT_REQUIRED_BYTES"
export DEPLOY_ROOT_AVAILABLE_BYTES="$ROOT_AVAILABLE_BYTES"
export DEPLOY_BOOT_AVAILABLE_BYTES="$BOOT_AVAILABLE_BYTES"
export DEPLOY_DIFF_PATH="$SCRATCH/extlinux.diff"
export DEPLOY_RUN_ID="$RUN_ID"
export DEPLOY_BASELINE_LOCK="$BASELINE_LOCK"
export DEPLOY_SOURCE_IMAGE="$SOURCE_IMAGE"
export DEPLOY_SOURCE_UINITRD="$SOURCE_UINITRD"
export DEPLOY_SOURCE_DTB="$SOURCE_DTB"
export DEPLOY_DEST_IMAGE="$DEST_IMAGE"
export DEPLOY_DEST_UINITRD="$DEST_UINITRD"
export DEPLOY_DEST_DTB="$DEST_DTB"
export DEPLOY_CURRENT_EXTLINUX="$CURRENT_EXTLINUX"
export DEPLOY_CANDIDATE_EXTLINUX="$CANDIDATE_EXTLINUX"

write_report() {
	local output="$1"
	python3 - "$output" <<'PY'
import json
import hashlib
import os
import pathlib
import sys

def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

stable_before = {}
for line in pathlib.Path(os.environ["DEPLOY_BASELINE_LOCK"]).read_text(
    encoding="utf-8"
).splitlines():
    digest, relative = line.split()
    stable_before[relative.removeprefix("boot/")] = digest

source_hashes = {
    "Image-6.12.108-eaidk310-zramfix1": sha256(os.environ["DEPLOY_SOURCE_IMAGE"]),
    "uInitrd-6.12.108-eaidk310-zramfix1": sha256(os.environ["DEPLOY_SOURCE_UINITRD"]),
    "dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb": sha256(
        os.environ["DEPLOY_SOURCE_DTB"]
    ),
}
is_apply = os.environ["DEPLOY_MODE"] == "apply"
versioned_after = None
extlinux_after = None
if is_apply:
    versioned_after = {
        "Image-6.12.108-eaidk310-zramfix1": sha256(os.environ["DEPLOY_DEST_IMAGE"]),
        "uInitrd-6.12.108-eaidk310-zramfix1": sha256(os.environ["DEPLOY_DEST_UINITRD"]),
        "dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb": sha256(
            os.environ["DEPLOY_DEST_DTB"]
        ),
    }
    extlinux_after = sha256(os.environ["DEPLOY_CURRENT_EXTLINUX"])

report = {
    "gate": "PASS",
    "mode": os.environ["DEPLOY_MODE"],
    "write": os.environ["DEPLOY_MODE"] == "apply",
    "bundle_sha256": os.environ["DEPLOY_BUNDLE_SHA256"],
    "current_kernel": os.environ["DEPLOY_CURRENT_KERNEL"],
    "root_source": os.environ["DEPLOY_ROOT_SOURCE"],
    "boot_source": os.environ["DEPLOY_BOOT_SOURCE"],
    "run_id": os.environ["DEPLOY_RUN_ID"],
    "stable_before_sha256": stable_before,
    "versioned_source_sha256": source_hashes,
    "versioned_after_sha256": versioned_after,
    "candidate_extlinux_sha256": sha256(os.environ["DEPLOY_CANDIDATE_EXTLINUX"]),
    "extlinux_after_sha256": extlinux_after,
    "space": {
        "root_required_bytes": int(os.environ["DEPLOY_ROOT_REQUIRED_BYTES"]),
        "root_available_bytes": int(os.environ["DEPLOY_ROOT_AVAILABLE_BYTES"]),
        "boot_required_bytes": int(os.environ["DEPLOY_BOOT_REQUIRED_BYTES"]),
        "boot_available_bytes": int(os.environ["DEPLOY_BOOT_AVAILABLE_BYTES"]),
    },
    "planned_destinations": [
        "/boot/Image-6.12.108-eaidk310-zramfix1",
        "/boot/uInitrd-6.12.108-eaidk310-zramfix1",
        "/boot/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb",
        "/lib/modules/6.12.108-eaidk310-zramfix1",
        "/boot/extlinux/extlinux.conf",
    ],
    "extlinux_diff": pathlib.Path(os.environ["DEPLOY_DIFF_PATH"]).read_text(
        encoding="utf-8"
    ),
}
path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

if ((!APPLY)); then
	write_report "$SCRATCH/deployment-report.json"
	cat "$SCRATCH/deployment-report.json"
	printf 'DEPLOY_GATE=PASS: dry-run only; no target files changed\n' >&2
	exit 0
fi

mkdir -p -- "$BACKUP_DIR"
cp --preserve=all -- "$CURRENT_EXTLINUX" "$BACKUP_DIR/extlinux.conf"
cp --preserve=all -- "$BASELINE_LOCK" "$BACKUP_DIR/baseline-sha256.txt"

if [[ ! -e "$DEST_MODULES" ]]; then
	MODULE_TMP="$MODULES_ROOT/.$RELEASE.$RUN_ID.tmp"
	mkdir -- "$MODULE_TMP"
	cp -a -- "$SOURCE_MODULES/." "$MODULE_TMP/"
	diff -qr "$SOURCE_MODULES" "$MODULE_TMP" >/dev/null || fail "temporary module verification failed"
	mv -T -- "$MODULE_TMP" "$DEST_MODULES"
fi

install_versioned_file() {
	local source="$1" destination="$2" temporary
	[[ -e "$destination" ]] && return 0
	temporary="$(dirname -- "$destination")/.$(basename -- "$destination").$RUN_ID.tmp"
	install -m 0644 -- "$source" "$temporary"
	cmp -s "$source" "$temporary" || fail "temporary file verification failed: $destination"
	mv -T -- "$temporary" "$destination"
}
install_versioned_file "$SOURCE_IMAGE" "$DEST_IMAGE"
install_versioned_file "$SOURCE_UINITRD" "$DEST_UINITRD"
install_versioned_file "$SOURCE_DTB" "$DEST_DTB"

EXTLINUX_TMP="$BOOT_ROOT/extlinux/.extlinux.conf.$RUN_ID.tmp"
install -m 0644 -- "$CANDIDATE_EXTLINUX" "$EXTLINUX_TMP"
python3 "$HELPER" verify-extlinux-deployment \
	--config "$EXTLINUX_TMP" --boot-root "$BOOT_ROOT" \
	--expected-default "$EXPECTED_DEFAULT" >/dev/null || fail "installed extlinux verification failed"
mv -T -- "$EXTLINUX_TMP" "$CURRENT_EXTLINUX"

for relative_path in boot/Image boot/uInitrd boot/dtb/rockchip/rk3328-eaidk-310.dtb; do
	actual_hash="$(sha256sum "$ROOT_PREFIX/$relative_path" | awk '{print $1}')"
	[[ "$actual_hash" == "${EXPECTED_STABLE[$relative_path]}" ]] || \
		fail "stable boot file changed during deployment: /$relative_path"
done
cmp -s "$SOURCE_IMAGE" "$DEST_IMAGE" || fail "deployed Image verification failed"
cmp -s "$SOURCE_UINITRD" "$DEST_UINITRD" || fail "deployed initramfs verification failed"
cmp -s "$SOURCE_DTB" "$DEST_DTB" || fail "deployed DTB verification failed"
diff -qr "$SOURCE_MODULES" "$DEST_MODULES" >/dev/null || fail "deployed modules verification failed"
sync

REPORT_TMP="$BACKUP_DIR/.deployment-report.json.tmp"
write_report "$REPORT_TMP"
mv -T -- "$REPORT_TMP" "$BACKUP_DIR/deployment-report.json"
sync
cat "$BACKUP_DIR/deployment-report.json"
printf 'DEPLOY_GATE=PASS: versioned test kernel installed; stable default preserved\n' >&2
