#!/bin/bash
# Build OpenGate_5400_Test as an SMOD v2 (relocatable) mod for Mod Loader v2. Usage: build_v2_wsl.sh OUTPUT_DIR
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=$1
mkdir -p "$OUT"
arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin \
  -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror \
  -fpie -fvisibility=hidden -c mods/opengate_5400_test/opengate_5400_test.c -o "$OUT/opengate_5400_test.o"
if arm-linux-gnueabi-readelf -rW "$OUT/opengate_5400_test.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32)"; then
  echo "absolute relocations: not position-independent" >&2; exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld "$OUT/opengate_5400_test.o" -o "$OUT/opengate_5400_test.elf"
python3 -m tools.research.pack_mod_v2 --elf "$OUT/opengate_5400_test.elf" --name OpenGate_5400_Test \
  --on og5400_on --off og5400_off --out "$OUT/OpenGate_5400_Test.mod"
