# Sony a6400 QEMU Machine (`sony-a6400`)

Out-of-tree QEMU v10.1.0 overlay and patch for the Sony ILCE-6400 (`CXD90045` / `ARM-CXD900X0`), modeled after the clean `qemu-overlay/` + `patches/` pattern used by `Oein/a5000-emulator`.

## Prerequisites (WSL2 Ubuntu)

Install the QEMU build dependencies in WSL2 Ubuntu:

```bash
sudo apt update
sudo apt install -y build-essential git meson ninja-build pkg-config \
  libglib2.0-dev libpixman-1-dev libfdt-dev zlib1g-dev python3-venv \
  python3-tomli libsdl2-dev
```

## Setup & Build

From the `qemu-a6400/` directory inside WSL2:

```bash
./setup-qemu.sh
cd qemu/build
../configure --target-list=arm-softmmu --enable-sdl --disable-werror
ninja qemu-system-arm
```

## Milestone 1: Initial Bring-Up

Verify the machine registers cleanly:

```bash
./qemu/build/qemu-system-arm -M help | grep sony-a6400
```

## Milestone 2: Main firmware boot (E0521)

```bash
./run.sh            # TIMEOUT=80 ./run.sh for a bounded run
```

`run.sh` reproduces the Sony bootloader's hand-off: kernel at `0x00108000`, initrd at `0x00700000`,
`"kemco"+cmdline` at `0x00103000` (machine code), the main-boot `/setting/kemco.txt` cmdline (console
swapped to `ttyAM0`), `Backup.bin` preloaded at its header word-0 address (`0x02E80000`) and
`av-cam.bin` at `0x035C6000`. The eMMC image is attached through a fresh qcow2 overlay.

State at E0521: kernel → init → all eMMC partitions mounted → 40 kernel modules → av-cam
`entry_main` → `stella`/`DMP_2D` → BusyBox shell on `ttyAM0`. See
[docs/evidence/0521-qemu-a6400-main-firmware-boots-to-avcam.md](../docs/evidence/0521-qemu-a6400-main-firmware-boots-to-avcam.md).

## Milestone 3: SMP (E0522)

The machine models 4 Cortex-A5 cores (`-smp 1` still works). A CPU0 stub presets GIC SPI targets
and enters the kernel. CPU1–3 wait on the kernel's SRAM mailbox (`0xFE005010`). Timers use the CXD4115
model (adapted from Oein/a5000-emulator), with per-CPU ticks on IRQ 153/155/156/157. `run.sh`
defaults to `-icount shift=0,sleep=on`. See
[docs/evidence/0522-qemu-a6400-smp-four-cores-and-cxd4115-timers.md](../docs/evidence/0522-qemu-a6400-smp-four-cores-and-cxd4115-timers.md).

Findings, known gaps and the ordered next steps: [STATUS.md](STATUS.md).

Next: av-cam's hardware dependencies (no `liro-*` IRQ fires yet): sub-micro link, ADC, front end;
then the Sugilite display pipeline.
Before each new blocker, check whether `Oein/a5000-emulator` already solved the CXD90014 equivalent.
