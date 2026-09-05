#!/usr/bin/env bash
set -euo pipefail

EXPECTED_RELEASE="6.12.108-eaidk310-zramfix1"
DEFAULT_WSL_ROOT="/home/Fog/eaidk310-kernel"
BUNDLE_NAME="eaidk310-linux-$EXPECTED_RELEASE"
APPEND_LINE="root=UUID=bd2a6dbf-c55f-4d5e-8738-797e581ee7c9 rootwait rootfstype=ext4 net.ifnames=0 earlycon console=ttyS2,1500000n8 console=tty1 consoleblank=0 loglevel=4"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
WSL_ROOT="$DEFAULT_WSL_ROOT"
BUNDLE_PARENT="$WSL_ROOT/artifacts"
BUNDLE_ROOT="$BUNDLE_PARENT/$BUNDLE_NAME"
ARCHIVE_DIR="$PROJECT_DIR/artifacts"
DRY_RUN=0

fail() {
	printf 'PACKAGE_GATE=FAIL: %s\n' "$*" >&2
	exit 1
}

usage() {
	printf 'usage: %s [--dry-run] [--bundle-root PATH] [--archive-dir PATH]\n' "$0"
}

while (($#)); do
	case "$1" in
		--dry-run) DRY_RUN=1; shift ;;
		--bundle-root) BUNDLE_ROOT="${2:?missing value for --bundle-root}"; shift 2 ;;
		--archive-dir) ARCHIVE_DIR="${2:?missing value for --archive-dir}"; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) fail "unknown argument: $1" ;;
	esac
done

IMAGE="$WSL_ROOT/build-zramfix1/arch/arm64/boot/Image"
DTB="$WSL_ROOT/build-zramfix1/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dtb"
UINITRD="$WSL_ROOT/initramfs-zramfix1/uInitrd-$EXPECTED_RELEASE"
MODULE_DIR="$WSL_ROOT/stage-zramfix1/lib/modules/$EXPECTED_RELEASE"

for source in "$IMAGE" "$DTB" "$UINITRD" "$MODULE_DIR/modules.dep" \
	"$MODULE_DIR/modules.builtin" "$MODULE_DIR/modules.order"; do
	[[ -s "$source" ]] || fail "required input is missing or empty: $source"
done

printf 'write=%s\n' "$([[ "$DRY_RUN" == 1 ]] && printf false || printf true)"
printf '%s\n' \
	"target=boot/Image-$EXPECTED_RELEASE" \
	"target=boot/uInitrd-$EXPECTED_RELEASE" \
	"target=boot/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb" \
	"target=root/lib/modules/$EXPECTED_RELEASE/"
((DRY_RUN)) && exit 0

for tool in find mktemp python3 realpath rsync sha256sum tar zstd; do
	command -v "$tool" >/dev/null || fail "required tool is missing: $tool"
done

BUNDLE_ROOT="$(realpath -m "$BUNDLE_ROOT")"
ARCHIVE_DIR="$(realpath -m "$ARCHIVE_DIR")"
case "$BUNDLE_ROOT" in "$WSL_ROOT"/artifacts/*) ;; *) fail "bundle root must remain below $WSL_ROOT/artifacts" ;; esac
[[ "$(basename -- "$BUNDLE_ROOT")" == "$BUNDLE_NAME" ]] || \
	fail "bundle directory name must be $BUNDLE_NAME"

if [[ -e "$BUNDLE_ROOT" ]]; then
	rm -rf -- "$BUNDLE_ROOT"
fi
mkdir -p -- \
	"$BUNDLE_ROOT/boot/dtb/rockchip" \
	"$BUNDLE_ROOT/root/lib/modules/$EXPECTED_RELEASE" \
	"$BUNDLE_ROOT/deploy" \
	"$ARCHIVE_DIR"

install -m 0644 "$IMAGE" "$BUNDLE_ROOT/boot/Image-$EXPECTED_RELEASE"
install -m 0644 "$UINITRD" "$BUNDLE_ROOT/boot/uInitrd-$EXPECTED_RELEASE"
install -m 0644 "$DTB" \
	"$BUNDLE_ROOT/boot/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb"
rsync -a --delete --exclude build --exclude source \
	"$MODULE_DIR/" "$BUNDLE_ROOT/root/lib/modules/$EXPECTED_RELEASE/"

install -m 0755 "$PROJECT_DIR/scripts/deploy-rescue-tf.sh" \
	"$BUNDLE_ROOT/deploy/deploy-rescue-tf.sh"
install -m 0644 "$PROJECT_DIR/tools/kernel_artifacts.py" \
	"$BUNDLE_ROOT/deploy/kernel_artifacts.py"
cat > "$BUNDLE_ROOT/deploy/stable-baseline-sha256.txt" <<'EOF'
e90c1a5fc340ba605a5edee81315df21369dcb9d82c5af1c9973a1754181010e  boot/Image
a9278654cd7edb07b52fe6796a0a116067d10b6a16af4de6a48e0716b0461cf9  boot/uInitrd
bb3824bc0d07af301413fc2071f76ad4a196da4807ee79c51a6563a480fca614  boot/dtb/rockchip/rk3328-eaidk-310.dtb
e461f108e2eaa1aec71762b1c59e662cc358896eff3671a622db93a49963209a  boot/extlinux/extlinux.conf
EOF
python3 "$PROJECT_DIR/tools/kernel_artifacts.py" render-extlinux \
	--append-line "$APPEND_LINE" \
	--output "$BUNDLE_ROOT/deploy/extlinux-entry.conf" >/dev/null

export REPORT_IMAGE_SHA="$(sha256sum "$IMAGE" | cut -d' ' -f1)"
export REPORT_DTB_SHA="$(sha256sum "$DTB" | cut -d' ' -f1)"
export REPORT_UINITRD_SHA="$(sha256sum "$UINITRD" | cut -d' ' -f1)"
export REPORT_MODULE_FILES="$(find "$MODULE_DIR" -type f -name '*.ko*' | wc -l)"
python3 - "$BUNDLE_ROOT/verification-report.json" <<'PY'
import json
import os
import pathlib
import sys

report = {
    "gate": "PASS",
    "scope": "package-preverification",
    "kernel_release": "6.12.108-eaidk310-zramfix1",
    "image_sha256": os.environ["REPORT_IMAGE_SHA"],
    "dtb_sha256": os.environ["REPORT_DTB_SHA"],
    "uinitrd_sha256": os.environ["REPORT_UINITRD_SHA"],
    "module_files": int(os.environ["REPORT_MODULE_FILES"]),
    "deployment_enabled": True,
}
pathlib.Path(sys.argv[1]).write_text(
    json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

metadata_path="$(mktemp "$WSL_ROOT/task6-metadata.XXXXXX.json")"
trap 'rm -f -- "$metadata_path"' EXIT
python3 - "$PROJECT_DIR/analysis/verify-inputs.json" \
	"$PROJECT_DIR/analysis/build-metadata.json" "$metadata_path" <<'PY'
import json
import pathlib
import sys

inputs = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
build = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
metadata = {
    "kernel_release": "6.12.108-eaidk310-zramfix1",
    "source_archive_sha256": inputs["archive_sha256"],
    "source_signer_fingerprint": inputs["validsig_fingerprint"],
    "source_date_epoch": build["source_date_epoch"],
    "gcc": inputs["tool_versions"]["gcc"],
    "binutils": inputs["tool_versions"]["binutils"],
    "bundle_policy": "versioned-test-only",
    "deployment_enabled": True,
}
pathlib.Path(sys.argv[3]).write_text(
    json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

mapfile -d '' manifest_files < <(
	cd "$BUNDLE_ROOT"
	find . -type f ! -name manifest.json -printf '%P\0' | sort -z
)
manifest_args=()
for relative_path in "${manifest_files[@]}"; do
	manifest_args+=(--file "$relative_path")
done
python3 "$PROJECT_DIR/tools/kernel_artifacts.py" manifest \
	--root "$BUNDLE_ROOT" --output "$BUNDLE_ROOT/manifest.json" \
	--metadata-json "$metadata_path" "${manifest_args[@]}" >/dev/null
python3 "$PROJECT_DIR/tools/kernel_artifacts.py" verify-bundle-layout \
	--root "$BUNDLE_ROOT" --manifest "$BUNDLE_ROOT/manifest.json" >/dev/null

archive_path="$ARCHIVE_DIR/$BUNDLE_NAME.tar.zst"
archive_sha_path="$archive_path.sha256"
rm -f -- "$archive_path" "$archive_sha_path"
tar --sort=name --mtime='@1788352262' --owner=0 --group=0 --numeric-owner \
	-C "$(dirname -- "$BUNDLE_ROOT")" -cf - "$BUNDLE_NAME" | \
	zstd -19 -T0 -q -o "$archive_path"
(
	cd "$ARCHIVE_DIR"
	sha256sum "$(basename -- "$archive_path")" > "$(basename -- "$archive_sha_path")"
)

printf 'PACKAGE_GATE=PASS\n'
printf 'bundle_root=%s\n' "$BUNDLE_ROOT"
printf 'archive=%s\n' "$archive_path"
printf 'archive_sha256=%s\n' "$(sha256sum "$archive_path" | cut -d' ' -f1)"
