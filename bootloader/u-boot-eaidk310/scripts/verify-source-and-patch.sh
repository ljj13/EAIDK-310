#!/usr/bin/env bash
set -euo pipefail

readonly locked_commit="38ea74d6d5c05224acdb03f799897c1bdd56f8cc"
readonly script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
readonly project_dir="$(dirname -- "$script_dir")"
readonly patch_path="$project_dir/patches/0001-arm-dts-add-eaidk310-variants.patch"

if [ "$#" -ne 1 ]; then
	echo "usage: $0 SOURCE_DIR" >&2
	exit 2
fi

source_dir="$(CDPATH= cd -- "$1" && pwd)"
actual_commit="$(git -C "$source_dir" rev-parse HEAD)"

if [ "$actual_commit" != "$locked_commit" ]; then
	echo "source commit mismatch: expected $locked_commit, got $actual_commit" >&2
	exit 1
fi

if [ -n "$(git -C "$source_dir" status --porcelain)" ]; then
	echo "source tree is not clean" >&2
	exit 1
fi

git -C "$source_dir" diff --quiet --ignore-submodules HEAD --
git -C "$source_dir" apply --check "$patch_path"
printf 'Verified source %s and patch applicability.\n' "$locked_commit"
