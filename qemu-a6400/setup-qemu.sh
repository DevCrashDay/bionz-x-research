#!/bin/bash
# Create QEMU build tree = upstream QEMU v10.1.0 + the sony-a6400 machine:
#   1. Clone the v10.1.0 tag (skipped if target dir already exists)
#   2. Copy qemu-overlay/ (all new files) into it
#   3. Apply patches/qemu-v10.1.0-sony-a6400.patch (minimal upstream edits)
# Note: QEMU's configure rejects paths containing spaces, so if the repo path
# has spaces we default to $HOME/sony-a6400-qemu (override via A6400_QEMU_DIR).
set -euo pipefail
E="$(cd "$(dirname "$0")" && pwd)"
if [ -n "${A6400_QEMU_DIR:-}" ]; then
    Q="$A6400_QEMU_DIR"
elif [[ "$E" == *" "* ]]; then
    Q="$HOME/sony-a6400-qemu"
else
    Q="$E/qemu"
fi

if [ ! -d "$Q" ]; then
    git clone --branch v10.1.0 --depth 1 https://gitlab.com/qemu-project/qemu.git "$Q"
fi

cp -R "$E/qemu-overlay/." "$Q/"

if git -C "$Q" apply --check "$E/patches/qemu-v10.1.0-sony-a6400.patch" 2>/dev/null; then
    git -C "$Q" apply "$E/patches/qemu-v10.1.0-sony-a6400.patch"
else
    echo "Patch already applied or conflicts; skipping git apply."
fi

mkdir -p "$Q/build"
echo "QEMU tree ready at: $Q"
echo "Build with:"
echo "  cd \"$Q/build\" && ../configure --target-list=arm-softmmu --enable-sdl --disable-werror && ninja qemu-system-arm"
