#!/bin/bash
# Boot the main (nflasha3) a6400 firmware on the sony-a6400 QEMU machine.
#
# Reproduces what the Sony bootloader leaves in RAM before entering the
# kernel (E0521; same contract as Oein/a5000-emulator docs/BOOT.md):
#   - kernel Image at PA 0x00108000, initrd at 0x00700000 (board code)
#   - "kemco"+cmdline block at 0x00103000 (board code, from -append);
#     the cmdline is the main-boot /setting/kemco.txt, with only
#     console=ttynull swapped for the ttyAM0 console
#   - /setting/Backup.bin copied to the PA in its header word 0
#     (backup.ko Backup_load=0 expects it already in RAM)
#   - /system/av-cam.bin copied to 0x035C6000 (liro.ko adrs=035c6000)
# The eMMC image is never written: every run uses a fresh qcow2 overlay.
#
# Env overrides: A6400_QEMU_DIR, A6400_QEMU_DATA_DIR (verified prepared inputs),
# RUN_DIR, AVCAM (legacy mode only: path to av-cam.bin),
# TIMEOUT (seconds, 0 = none), EXTRA (appended cmdline tokens),
# QEMU_EXTRA (extra QEMU options; default "-icount shift=0,sleep=on" ties
# virtual time to executed instructions, E0522; set QEMU_EXTRA= to disable).
# The machine models 4 Cortex-A5 cores by default (E0522); -smp 1 still works.
set -euo pipefail
E="$(cd "$(dirname "$0")" && pwd)"
D="$E/data"
PREPARED=0
if [ -n "${A6400_QEMU_DATA_DIR:-}" ]; then
    D="$(cd "$A6400_QEMU_DATA_DIR" && pwd)"
    PREPARED=1
    (cd "$E/.." && python3 -m tools.research.qemu_boot_inputs verify --report-dir "$D")
fi
if [ -n "${A6400_QEMU_DIR:-}" ]; then Q="$A6400_QEMU_DIR"
elif [[ "$E" == *" "* ]]; then Q="$HOME/sony-a6400-qemu"
else Q="$E/qemu"; fi
R="${RUN_DIR:-$HOME/a6400-runs}"
if [ "$PREPARED" = 1 ]; then
    if [ -n "${AVCAM:-}" ] && [ "$AVCAM" != "$D/av-cam.bin" ]; then
        echo "Prepared inputs do not permit an unverified AVCAM override." >&2
        exit 1
    fi
    AVCAM="$D/av-cam.bin"
    R="${RUN_DIR:-$HOME/a6400-runs/$(date -u +%Y%m%dT%H%M%SZ)-$$}"
    if [ -e "$R" ]; then
        echo "Refusing to overwrite an existing prepared-input run: $R" >&2
        exit 1
    fi
else
    AVCAM="${AVCAM:-$E/../firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin}"
fi
AVCAM_PA=0x035C6000
mkdir -p "$R"

# QEMU's option parser splits on ',' and the repo path may contain spaces,
# so stage the preload inputs under $R.
tar xOf "$D/setting.tar" ./Backup.bin > "$R/Backup.bin"
cp "$AVCAM" "$R/av-cam.bin"
BACKUP_PA=$(printf '0x%08X' "$(od -An -tu4 -N4 "$R/Backup.bin" | tr -d ' ')")

if [ "$PREPARED" = 1 ]; then
    CMD=$(cat "$D/serial-cmdline.txt")
else
    CMD=$(tar xOf "$D/setting.tar" ./kemco.txt | tr '\r\n' '  ' | tr -s ' ' \
          | sed 's/ \*[[:space:]]*$//; s/^kemco //; s/console=ttynull/console=ttyAM0,115200n8 amba2.console=1/')
fi

rm -f "$R/emmc-overlay.qcow2"
"$Q/build/qemu-img" create -q -f qcow2 -F raw -b "$D/emmc.img" "$R/emmc-overlay.qcow2"

echo "Backup.bin -> $BACKUP_PA, av-cam.bin -> $AVCAM_PA, overlay $R/emmc-overlay.qcow2" >&2
T=()
if [ "${TIMEOUT:-0}" != 0 ]; then T=(timeout "$TIMEOUT"); fi
exec "${T[@]}" "$Q/build/qemu-system-arm" -M sony-a6400 -m 1G -nographic -serial mon:stdio \
  -kernel "$D/nflasha3-Image" -initrd "$D/nflasha3-initrd" \
  -append "$CMD ${EXTRA:-}" \
  -drive if=sd,format=qcow2,file="$R/emmc-overlay.qcow2" \
  -device loader,file="$R/Backup.bin",addr="$BACKUP_PA",force-raw=on \
  -device loader,file="$R/av-cam.bin",addr="$AVCAM_PA",force-raw=on \
  ${QEMU_EXTRA--icount shift=0,sleep=on}
