# Sony BIONZ X Universal Emulator, Modding & Custom OS Platform

An open, evidence-backed reverse-engineering, emulation (**QEMU** + **Unicorn**), and runtime modding platform for Sony **BIONZ X (`CXD90045`)** cameras—starting with **`ILCE-6400`** and scaling across 13 mapped camera bodies.

> **Canonical upstream:** [ivica00/bionz-x-research](https://github.com/ivica00/bionz-x-research). This research branch contributes native Unicorn continuation, bounded USB/GPU and A6400A packet experiments, and toolchain/QEMU updates. Original upstream research, code and contributor attribution are retained.

**You don't need a camera to contribute.** Roughly 90% of the work is offline Python & C. Every claim below is strictly labelled by what was actually demonstrated (offline emulation, read-only observation, or physical RAM-only run), and nothing is presented as a percentage of "full firmware compatibility."

> **[📦 Install & Use Camera Mods (Mod Loader v2, Touch Menu, ARRI Look)](mods/README.md)** · **[🖥️ Run QEMU Emulator (`sony-a6400`)](qemu-a6400/README.md)** · **[🔬 Develop & Hot-Reload Mods in QEMU](qemu-a6400/MOD_DEVELOPMENT.md)** · **[📷 Add a BIONZ X Camera](docs/ADDING_A_BIONZX_CAMERA.md)** · **[🎥 UVC / PTP Liveview](docs/CONTRIBUTING_UVC_PTP.md)**

---

## Project Goals

Stock USB attach boundary: original startup code inhibits the physical USB
key; messages reach Linux input processing but its live subscription is
suppressed. Paired captured-code traces identify the native owner/store and
the initialization release path. Its original readiness check is blocked by
`ModelCamera` (`0x0B`); native stores change readiness `1,0,1,0`, while
`ModelStillRec` is ready. The exact state dependency remains open. No ready
flag, application event or new hardware response was supplied
([E0619](docs/evidence/0619-native-usb-release-model-camera-readiness.md)).

Contributor boot scores for the merged read-back windows and the v11 speed
patches are filed with the pull-request comments:
[E0614](docs/evidence/0614-f280-clock-readback-contributor-boot-score.md),
[E0615](docs/evidence/0615-f350-readback-contributor-boot-score.md),
[E0616](docs/evidence/0616-tb-jmp-cache-bits-14-contributor-boot-score.md),
[E0617](docs/evidence/0617-v11-sd-insert-check-contributor-boot-score.md),
and [E0618](docs/evidence/0618-f388-readback-contributor-boot-score.md).
They keep captured step 26 (`0x0323`/`0x0320`). This workspace did not re-run
that scorer, and no physical camera result is claimed.
[E0637](docs/evidence/0637-camcore-f301-contributor-boot-score.md) records the
same kind of score for the merged CamCore window at `0xF3010000`: unmodelled
addresses 2,322 to 1,310, still step 26.

QEMU EP0 milestone: original firmware now completes six host-driven standard
control transfers to address 1, configuration 1 and UDC `configured` in two
fresh runs. The optional mode preserves the older descriptor replay and
rejects unsupported ownership/DMA cases. The enumerated original function
is bus-power/HID; UVC remains unverified
([E0598](docs/evidence/0598-qemu-token-driven-ep0-configuration.md)).

| Goal | What it means | Current milestone |
| :--- | :--- | :--- |
| **1 · Emulate Every BIONZ X Model** | Run original bootloaders, Linux kernels (`ARM-CXD900X0`), and `im.elf` UI/ISP stacks offline in **QEMU** and **Unicorn** across the `CXD90045` family | **13 bodies / 9 kernels mapped** ([`PLATFORM_MODEL.md`](docs/PLATFORM_MODEL.md)); **8 registered profiles** (`ILCE-6400` verified execution target + 7 sourced reference profiles, [E0570](docs/evidence/0570-ilce-7rm4a-offline-sourced-profile.md)) |
| **2 · Universal Modding Across Models** | Position-independent (`SMOD v2`) runtime mods with per-library relocation (`--reloc-layout`), hot-reloadable in QEMU and RAM-only on physical cameras | **Mod Loader v2 (`MLCR`)** runs in QEMU ([E0577](docs/evidence/0577-qemu-real-mods-relocation-and-hot-reload.md)) and `ILCE-6400` RAM ([E0555](docs/evidence/0555-arrivederci-menu-exit-recompute-and-cc8-refill.md)); ships **MTF2 Touch** ([E0520](docs/evidence/0520-mtf2-full-menu-touch-verified-on-a6400.md)), **Arrivederci ARRI Look** ([E0556](docs/evidence/0556-arrivederci-classic-fitted-matrix.md)), and **Open Gate 3:2** feasibility ([E0578](docs/evidence/0578-a6400-open-gate-3-2-recording-feasibility.md)) |
| **3 · Custom OS & Open Camera Stack** | Replace closed camera bottlenecks with open, verified kernel/userspace modules—native composite USB webcam (UVC+PTP), persistent WinUSB debugging, custom OSD/ISP pipelines, and an open replacement OS layer | **USB Debugger v2** ([E0401](docs/evidence/0401-usb-debugger-v2-ring-recover-and-mode-boundary.md)), **29.8 FPS PTP Liveview** ([E0349](docs/evidence/0349-p1-foreign-get-jpeg-succeeds-in-pc-remote.md)), **Composite UVC+PTP enumeration** & EP0 pre-arm bridge ([E0476](docs/evidence/0476-status-prearm-fix-build-audit-unicorn.md)) |

---

## Emulation Architecture & Progress: QEMU vs. Unicorn

This project uses two complementary emulators:

- **QEMU ([`qemu-a6400/`](qemu-a6400/README.md)) — Full-System Board (`sony-a6400`):** Out-of-tree QEMU v11.1.3 machine emulating 4× Cortex-A5 SMP cores, native eMMC partitions, 40 kernel modules, `av-cam.bin` (LIRO RTOS), `im.elf`, Sugilite P3D/PPF OSD, and an interactive browser rear panel (`rear_panel.py`) with live mod hot-reload. The earlier v10.1.0 build and evidence remain preserved.
- **Unicorn (`src/a6400/`) — Deterministic Contract & Instruction Engine:** Fast Python + Unicorn harness split into **L2** (`UnicornMachine` kernel boot & MMU/TLB), **L3** (`InterpreterProbe` user-space syscall/IPC/thread engine), and **isolated ARM/Thumb unit harnesses** for verifying mods and kernel patches in CI without booting a full VM.

### Compact Stage Matrix

Legend: `🟢 Verified` · `🟡 Partial / Opt-in` · `⚪ Open Boundary` · `⛔ Not Supported`

| Stage / Subsystem | QEMU (`sony-a6400` Full Board) | Unicorn (`L2` Kernel / `L3` Userspace) | Key Evidence & Docs |
| :--- | :--- | :--- | :--- |
| **CPU, SMP & Boot** | 🟢 4× Cortex-A5 SMP, `kemco` handoff, CXD4115 timers | 🟢 L2 boots `vmlinux.bin` (`0xDA9`), native CP15/TLB maintenance, original linker and BusyBox/ash builtin roundtrips | [E0522](docs/evidence/0522-qemu-a6400-smp-four-cores-and-cxd4115-timers.md), [E0533](docs/evidence/0533-unicorn-native-thumb-userspace-continuation.md), [E0560](docs/evidence/0560-unicorn-nonsecure-af-exception-return.md) |
| **Storage & Mounts** | 🟢 18 captured partitions, CMD1 sector mode, qcow2 overlay | 🟢 Read-only ext2 (`nflasha7`/`15`), FAT12 `/setting` (`nflasha2`), 88 devnodes | [E0524](docs/evidence/0524-reproducible-captured-qemu-boot-and-liro-waits.md), [E0307](docs/evidence/0307-nflasha2-setting-namespace-uipc-unregister-and-idle-state.md) |
| **RTOS (`av-cam` / LIRO)** | 🟡 40 `.ko` loaded, `entry_main`, `VD_Sequence` reaches step 32 (`EVSEL_LATE_US=500`) | 🟢 Static & slice execution of CC 3D-LUT, EC Gamma, DMM & Open Gate 3:2 tables | [E0557](docs/evidence/0557-qemu-a6400-hs-root-order-passes-step-12.md), [E0505](docs/evidence/0505-av-cam-cc-3dlut-and-ec-gamma-pipeline.md), [E0578](docs/evidence/0578-a6400-open-gate-3-2-recording-feasibility.md) |
| **UI & Display (`im.elf`)** | 🟢 Sugilite P3D/PPF renders EVF OSD & Rear-LCD Menu (`640×480`) | 🟡 L3 reaches 88+ threads; allocator ownership remains unresolved and strict GLES replay stops on unknown shared contents | [E0572](docs/evidence/0572-qemu-a6400-rear-lcd-layout-on-panel.md), [E0526](docs/evidence/0526-stella-allocator-size-isolation-and-padding-trial.md), [E0530](docs/evidence/0530-gles-tgid-completion-and-shared-memory-boundary.md) |
| **Inputs & Touch** | 🟢 Web Rear Panel (`ADC`, `GPIO`, sub-micro, wheel, `MENU`); ⛔ **No touch panel** | 🟢 Isolated harnesses for touch press/release (`MTF2`) & menu vtables | [E0573](docs/evidence/0573-qemu-a6400-rear-panel-controls.md), [E0520](docs/evidence/0520-mtf2-full-menu-touch-verified-on-a6400.md) |
| **Mod Loader & Mods** | 🟢 `Mod Loader v2` installs in guest `im.elf`, relocates & hot-reloads `.mod` files | 🟢 10-scenario `MLCR` core + `SMOD v2` relocation & trampoline checks | [E0577](docs/evidence/0577-qemu-real-mods-relocation-and-hot-reload.md), [E0576](docs/evidence/0576-mod-loader-v2-reload-request.md) |
| **Live Sensor / YUV** | 🟡 Opt-in deterministic luma reaches the stock guest's CamPig/Ydet, RC0 DMA and rear LCD via VPH ch51 | ⚪ Sensor feeder unwired; DMM `0x0167046a` JPEG queue contract decoded | [E0591](docs/evidence/0591-qemu-a6400-first-live-view-frame.md), [E0349](docs/evidence/0349-p1-foreign-get-jpeg-succeeds-in-pc-remote.md) |
| **USB, Wi-Fi/BT, HDMI, Lens** | 🟡 Opt-in DWC3: original bus-power/HID reaches configured through six EP0 controls; no UVC/data endpoints, radio, HDMI, lens or audio qualification | 🟡 Original-process USB startup reaches FSM state 7 and gadget registration, then stops at guarded OTG `SET_MODE`; ⛔ no radio/HDMI/lens | [E0598](docs/evidence/0598-qemu-token-driven-ep0-configuration.md), [E0534](docs/evidence/0534-original-process-usb-gadget-registration-and-otg-boundary.md), [E0476](docs/evidence/0476-status-prearm-fix-build-audit-unicorn.md) |

### Parallel Offline Contract Follow-Ups

- **Native Unicorn continuation:** [E0533](docs/evidence/0533-unicorn-native-thumb-userspace-continuation.md) removes re-entrant CPU helpers and manual Thumb recovery. Two updater replays execute ash `echo`/`pwd`; the separate post-`uname` child/COW exception remains open. This is independent of the QEMU display result.
- **USB delivery versus startup:** [E0524](docs/evidence/0524-original-local-osal-setconfig-route.md) executes isolated synchronous-to-asynchronous SETCONFIG delivery with balanced fixture ownership; startup state 0 does not handle that event. [E0534](docs/evidence/0534-original-process-usb-gadget-registration-and-otg-boundary.md) uses the process's own heap and reaches states `1 -> 5 -> 6 -> 7`, but `created=0` at OTG `SET_MODE`. No event or hardware acknowledgement is fabricated.
- **Allocator and GPU corrections:** [E0526](docs/evidence/0526-stella-allocator-size-isolation-and-padding-trial.md) shows that the isolated log constructor fits its `0x4C` request; padding is not a verified fix. [E0530](docs/evidence/0530-gles-tgid-completion-and-shared-memory-boundary.md) verifies TGID completion/reset and distinguishes 128 KiB software-shared memory from the GPU BAR. Strict L3 replay produces no frame.
- **A6400A service packets:** [E0525](docs/evidence/0525-a6400a-jiritsu-senser-offline-packets.md) maps all 18 Jiritsu commands and seven repeatable packet fixtures. Opt-in [E0532](docs/evidence/0532-a6400a-dmm-metadata-bounds-and-lifetime.md) adds bounded single-extent DMM metadata/lifetime and an eighth synthetic reply. USB, authentication, filesystem operations and authentic allocation remain unsupported; the profile stays `reference`.
- **Earlier captured-profile waits:** [E0527](docs/evidence/0527-qemu-task43-initialization-flag-wait.md) identifies Task 43's first display wait as mask 3, not its later `0x3373` loop. That no-peripheral observation is retained separately from the later QEMU device models and rear-panel progress.

### What Is Not Supported Yet

- **Live camera sensor, shutter & recording (QEMU & Unicorn):** The QEMU frame uses synthetic luma, not a real sensor stream or validated RAW/color ISP. AF/AE, still capture and movie recording to SD are unverified (see [QEMU checkpoint](qemu-a6400/STATUS.md)).
- **Touch-panel hardware in QEMU:** The `sony-a6400` board has no emulated touch controller, so touch mods (`MTF2`) cannot be exercised via clicks in QEMU ([`MOD_DEVELOPMENT.md`](qemu-a6400/MOD_DEVELOPMENT.md)).
- **USB Gadget (UVC / PTP / MSC) in QEMU:** The stock view-driven server-start gate remains unqualified ([E0589](docs/evidence/0589-qemu-usb-mode-gate-and-defrootview.md)). Explicit diagnostic commands and optional captured-register/host-token policies now enumerate the original bus-power/HID function to UDC configured ([E0598](docs/evidence/0598-qemu-token-driven-ep0-configuration.md)). A genuine UVC function, non-EP0 data endpoints, transfer chains, packetization and video remain unqualified; the USB Streaming Test item still only counts loader lifecycle calls.
- **Peripherals & Radios (QEMU & Unicorn):** Wi-Fi / Bluetooth (`BCM4339`), HDMI output, E-mount lens communication, audio I/O, are unmodelled; the hardware watchdog (`0xF2402000`) is modelled only as opt-in `watchdog=on` ([E0636](docs/evidence/0636-qemu-cxd4115-hardware-watchdog-opt-in.md)).
- **Full Unicorn L3 GUI & Non-a6400 Boot:** `InterpreterProbe` (Unicorn L3) does not yet produce a GUI frame (`stella` heap & GPES barriers), and only `ILCE-6400` is a verified execution target (other `CXD90045` bodies are reference profiles).

---

## Universal Modding & Available Mods

**[Mod Loader v2 (`MLCR`)](docs/MOD_LOADER_V2_DESIGN.md)** installs a tiny permanent RAM shim in `im.elf` (`viewUnified2.so` hooks) and loads a position-independent core (`core.bin`) plus relocatable **`SMOD v2`** `.mod` packages—requiring **zero flash writes** and restoring stock behavior on reboot. Using `--reloc-layout`, a single `.mod` binary shifts its library addresses at load time to run across both QEMU and physical camera memory layouts ([E0577](docs/evidence/0577-qemu-real-mods-relocation-and-hot-reload.md)).

| Mod | What it does | Status |
| :--- | :--- | :--- |
| **[MenuTouchFull (`MTF2`)](mods/menu_touch_full/README.md)** | Full-menu touch navigation: tabs, page dots, swipe paging, row select/open, popup scroll & apply | Verified on `ILCE-6400` ([E0520](docs/evidence/0520-mtf2-full-menu-touch-verified-on-a6400.md)); `SMOD v2` usable & hot-reloads in QEMU |
| **[Arrivederci](mods/arri_isp/arrivederci/README.md)** | ARRI LogC3 Classic tone curve + fitted Q10 color matrix for S-Log3 / S-Gamut3.Cine | Usable in Modset v2 ([E0555](docs/evidence/0555-arrivederci-menu-exit-recompute-and-cc8-refill.md), [E0556](docs/evidence/0556-arrivederci-classic-fitted-matrix.md)); turn OFF before power-off |
| **ModsTabLook** | Renders the custom `MODS` tab icon artwork (`E0522`) | Auto-enabled in QEMU mod-dev mode ([E0522](docs/evidence/0522-mods-tab-icon-and-uxd-image-container.md)) |
| **Command items** (`--action`) | A Mods row without ON/OFF: pressing it starts the mod and closes the menu to the shooting screen, like the ZV-E10 "USB Streaming" entry | Loader feature verified in Unicorn and QEMU ([E0592](docs/evidence/0592-mod-loader-command-items-usb-streaming-entry.md)); no UVC streaming mod uses it yet. The QEMU virtual USB host reaches the DWC3 model, but the guest never starts a gadget ([E0593](docs/evidence/0593-qemu-dwc3-virtual-host-and-gadget-start-gap.md)) |
| **Open Gate (3:2)** | Staged `IMX284` 3:2 (`1620×1080` / `3240×2160`) recording path via `DMM` & `ZIMA DVENC` | Offline feasibility mapped ([E0578](docs/evidence/0578-a6400-open-gate-3-2-recording-feasibility.md)); untested on hardware |

👉 **[Read `mods/README.md` for step-by-step installation (QEMU & physical `ILCE-6400`)](mods/README.md)**

---

## Multi-Camera Support (`CXD90045` Platform)

Registered profiles live in [`src/a6400/targets/`](src/a6400/targets/catalog.py) and share the cross-body `CXD90045` model ([`PLATFORM_MODEL.md`](docs/PLATFORM_MODEL.md)). Guide: **[Adding a BIONZ X camera](docs/ADDING_A_BIONZX_CAMERA.md)**.

| Body | Kernel Group | Status | Notes / Evidence |
| :--- | :--- | :--- | :--- |
| **`ILCE-6400`** | `a6400` / `a6600` source | **Verified Execution Target** | Full QEMU & Unicorn L2/L3 execution, physical RAM-only mods |
| **`ILCE-6400A`** | `1.01` packaged | **Sourced Reference** | Native `GetModelInfo` (`0032858730`), packaged Jiritsu command map and bounded offline DMM replies ([E0554](docs/evidence/0554-a6400a-v101-jiritsu-command-dispatch.md), [E0525](docs/evidence/0525-a6400a-jiritsu-senser-offline-packets.md), [E0532](docs/evidence/0532-a6400a-dmm-metadata-bounds-and-lifetime.md)) |
| **`ILCE-6600`** | `a6400` / `a6600` source | **Sourced Reference** | Identical 14,401 kernel function symbols; `0x413b` command delta |
| **`ZV-E10`**, **`ILCE-7C`** | Shared build | **Sourced Reference** | Identical 14,460 kernel function symbols; `ZV-E10` UVC stack cataloged ([E0321](docs/evidence/0321-zve10-uvc-enablement-manifest.md)) |
| **`ILCE-6100`**, **`ILCE-7M3`**, **`ILCE-7RM4A`** | Shared symbol list | **Sourced Reference** | `ILCE-7RM4A` 1.13 gadget-core symbols decoded ([E0570](docs/evidence/0570-ilce-7rm4a-offline-sourced-profile.md)) |

---

## Live Status Telemetry

Recorded offline suite: **2091 tests**, with 88 skip records and no failures/errors in the integrated checkout ([rebase validation and limits](docs/TOOLCHAIN.md#upstream-rebase-integration-2026-11-18)). Skips are not passes. Separate QEMU milestones are the [USB mode gate](docs/evidence/0589-qemu-usb-mode-gate-and-defrootview.md) and [first firmware-driven live-view frame](docs/evidence/0591-qemu-a6400-first-live-view-frame.md). Earlier narrative highlights are preserved in [docs/archives/readme-latest-progress-2026-11-18.md](docs/archives/readme-latest-progress-2026-11-18.md) and [docs/archives/readme-latest-progress-2026-11-15.md](docs/archives/readme-latest-progress-2026-11-15.md).

![Research status: demonstrated results and remaining boundaries](docs/images/research-status.svg?v=db1b8b37e317)

![Daily automated test suite growth: 2091 tests across 32 days](docs/images/test-growth.svg?v=7c3d215972bb)

---

## How to Help (No Camera Needed)

Please bring BIONZ X research, fixes, and camera support to this repository early. Reuse the tracking issue for your camera model and open a draft pull request with your first verified result. Shared evidence and fixes can then help every compatible body. See the [contribution and attribution policy](CONTRIBUTING.MD#work-in-the-main-repository).

**Top 3 open tasks right now:**

1. **Resolve remaining live-view sequencer gates (QEMU):** Stock firmware displays the deterministic test image and consumes fresh Ydet records. Trace the captured boot's separate step-26 gate and verify the older packaged step-32 case without bypassing either ([E0591](docs/evidence/0591-qemu-a6400-first-live-view-frame.md)).
2. **Complete native USB gadget startup (Unicorn / UVC):** Qualify OTG/class/gadgetcore completion after original registration, then prove process-owned SETCONFIG production, delivery and teardown ([E0534](docs/evidence/0534-original-process-usb-gadget-registration-and-otg-boundary.md)). The isolated local delivery route is already executable.
3. **Establish GPU shared backing and rendering (Unicorn L3):** TGID completion/reset is verified, but strict replay stops on unsupplied shared bytes. Recover initialization and functional GPU work without assumed success ([E0530](docs/evidence/0530-gles-tgid-completion-and-shared-memory-boundary.md)).

More guides: [Contributing to UVC / PTP](docs/CONTRIBUTING_UVC_PTP.md) · [UVC/PTP progress log](docs/UVC_PTP_STATUS.md) · [USB Debugger v2](docs/NATIVE_USB_DEBUGGER.md).

---

## Quick Start

Requires Python 3.11+ and dependencies from [`requirements.txt`](requirements.txt) (no camera needed).
The current research environment uses Python 3.15.2 in `.venv`; the previous
`venv/` remains available for rollback. See [verified toolchain setup and upgrade
results](docs/TOOLCHAIN.md) for REA 6.9.0, Ghidra 12.2 and patched QEMU 11.1.3.

```powershell
# Windows (PowerShell)
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

```bash
# Linux / macOS
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -q
```

| What to check | Command |
| :--- | :--- |
| Full offline suite (2091 tests) | `python -m unittest discover -s tests -q` |
| Structure, profiles & doc sync | `python -m unittest tests.test_repository_layout tests.test_target_profiles tests.test_research_status_sync -v` |
| Status card matches data | `python -m tools.research.render_research_status --check` |
| Windows launchers | [`scripts/`](scripts/) (`launch_cinemahud_ptp.bat`, `launch_cinemahud_android.ps1`, `launch_cinema_mpv.bat`, `start_camera_shell.bat`) |

Run scripts as Python modules from the repo root (`python -m tools.research.<tool>`). Firmware images, backups, and captured artifacts stay local and are excluded from Git.

---

## Repository Layout & Safety Rules

| Folder | What's inside |
| :--- | :--- |
| [`qemu-a6400/`](qemu-a6400/README.md) | Out-of-tree QEMU v11.1.3 `sony-a6400` machine, browser rear panel & mod-dev hot-reload |
| [`mods/`](mods/README.md) | RAM-only camera mods (`MenuTouchFull`, `Arrivederci`, `ModsTabLook`) & install guide |
| `src/a6400/execution/` | Unicorn L2/L3 ARM emulator and verified OS, USB, and GPU contracts |
| `src/a6400/targets/` | Device profiles and validation for supported Sony `CXD90045` cameras |
| `src/a6400/debug/` | USB debugger protocol, log ring buffer, and WinUSB transport |
| `tools/research/` & `tools/native_camera/` | Firmware analysis scripts, Mod Loader v2 (`mlv2/`), C helpers, and status-card generator |
| `probes/` | Safe, read-only ARM probe for other BIONZ X cameras |
| [`a6400-lut-converter/`](a6400-lut-converter/README.md) | Offline ISP table fitting, ARRI presets, and 8-bit 4:2:0 preview |
| `tests/`, `docs/`, `docs/evidence/` | Automated contract checks, architecture notes, and dated evidence logs |
| `android/`, `camera-daemon/` | Android liveview client (`CinemaHUD`) and camera-side composite USB webcam daemon |
| `firmware/`, `backups/`, `artifacts/` | Local firmware dumps and generated outputs (never committed to Git) |

**Safety & Epistemic Rules:**
- **Honest claims only:** Host-written UI or camera logic is **not** firmware compatibility. Every claim links to a dated evidence record.
- **No flash writes:** Never modify internal camera flash, install custom firmware, or alter boot partitions.
- **Clean-room emulation:** Original firmware owns application logic; unknown OS services stop and report where they stopped instead of faking success.
- **Safe hardware checks:** Read-only camera inspection is allowed; writes, mode switches, and module loads require explicit scope. Bulk `/dev/mem` memory sweeps are banned ([`AGENTS.MD`](AGENTS.MD)).

---

## Links & Attribution

[Documentation index](docs/INDEX.md) · [Current state](docs/CURRENT_STATE.md) · [Evidence index](docs/evidence/README.md) · [Related research repositories](docs/RELATED_REPOSITORIES.md) · [Architecture](docs/ARCHITECTURE.md) · [Roadmap](docs/ROADMAP.md) · [Contributing](CONTRIBUTING.MD) · [Repository layout](docs/REPOSITORY.md) · [Agent rules](AGENTS.MD) · [Archived README](docs/archives/README-2026-11-09.md)

- **Cross-generation BIONZ X research:** [Oein/a5000-emulator](https://github.com/Oein/a5000-emulator) by Park SungHyun (Oein) — clean-room reference for DMP SMAPH/P3D command-list format and 8×8 Morton tiling (`docs/RE_GPU_P3D_CMD.md`, CC BY 4.0), `GICD_ITARGETSR` bootloader pre-routing (`docs/HARDWARE.md`), and `im.elf` UI bringup gates (`docs/RE_UI_BRINGUP.md`).

Independent research; not affiliated with or endorsed by Sony. Sony, a6400, ILCE-6400, and BIONZ X are trademarks of their respective owners.
