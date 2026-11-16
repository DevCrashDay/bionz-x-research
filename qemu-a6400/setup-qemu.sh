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

if [ "$(git -C "$Q" rev-parse HEAD)" != "f8b2f64e2336a28bf0d50b6ef8a7d8c013e9bcf3" ]; then
    echo "Expected the QEMU v10.1.0 checkout; refusing to modify $Q" >&2
    exit 1
fi

if git -C "$Q" apply --check "$E/patches/qemu-v10.1.0-sony-a6400.patch" 2>/dev/null; then
    git -C "$Q" apply "$E/patches/qemu-v10.1.0-sony-a6400.patch"
elif git -C "$Q" apply --reverse --check "$E/patches/qemu-v10.1.0-sony-a6400.patch" 2>/dev/null; then
    echo "Sony patch already applied."
else
    echo "Sony patch conflicts with $Q; refusing to report a usable build tree." >&2
    git -C "$Q" apply --check "$E/patches/qemu-v10.1.0-sony-a6400.patch"
    exit 1
fi

cp -R "$E/qemu-overlay/." "$Q/"
mkdir -p "$Q/build"
echo "QEMU tree ready at: $Q"
echo "Build with:"
echo "  cd \"$Q/build\" && ../configure --target-list=arm-softmmu --enable-sdl --disable-werror && ninja qemu-system-arm qemu-img"
