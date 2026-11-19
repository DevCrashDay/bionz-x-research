#!/bin/bash
# Build RCRAW_W2_Probe as an SMOD v2 (relocatable) mod for Mod Loader v2. Usage: build_v2_wsl.sh OUTPUT_DIR
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=$1
mkdir -p "$OUT"
arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin \
  -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror \
  -fpie -fvisibility=hidden -c mods/rcraw_w2_probe/rcraw_w2_probe.c -o "$OUT/rcraw_w2_probe.o"
if arm-linux-gnueabi-readelf -rW "$OUT/rcraw_w2_probe.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32)"; then
  echo "absolute relocations: not position-independent" >&2; exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/rcraw_w2_probe.o" -o "$OUT/rcraw_w2_probe.elf"
python3 -m tools.research.pack_mod_v2 --elf "$OUT/rcraw_w2_probe.elf" --name RCRAW_W2_Probe \
  --on probe_life --off probe_life --out "$OUT/RCRAW_W2_Probe.mod"
