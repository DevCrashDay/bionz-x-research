# Current State

For the full documentation index see [docs/INDEX.md](INDEX.md). For the verified test count and latest evidence snapshot, see [README.md](../README.md) and [UVC/PTP Status](UVC_PTP_STATUS.md).

## Active Track Summary (as of 2026-11-18 / E0591)

| Track | Current Frontier | Latest Evidence | Next Step |
| --- | --- | --- | --- |
| **L2 Full-System Kernel Boot** (`UnicornMachine`) | Native CP15/TLB maintenance preserves banked ARM/Thumb state. Two original updater replays reach BusyBox and ash echo/pwd roundtrips without manual Thumb execution. Separate opt-in CPSR.A and SCR.AW/FW controls match QEMU's entry and 16 comparable return rows; the initial stack still advertises VFPD32 versus QEMU VFPv3D16 | [E0533](evidence/0533-unicorn-native-thumb-userspace-continuation.md), [E0558](evidence/0558-unicorn-qemu-kernel-entry-async-abort-mask.md), [E0559](evidence/0559-unicorn-qemu-irq-async-abort-mask.md), [E0560](evidence/0560-unicorn-nonsecure-af-exception-return.md), [E0562](evidence/0562-thumb-loader-stack-vfp-capability.md) | Qualify the separate post-`uname` child/COW exception at `0x60116E9C` and continue the independent AT_HWCAP comparison. Main-firmware application/GUI boot is not established by Unicorn |
| **L3 User-Space (`im.elf`)** (`InterpreterProbe`) | `--thread-timeslice` reaches 88+ threads and a historical allocator abort; isolated original code shows request/chunk/usable `0x4C/0x50/0x4C` and constructor writes only through `+0x48`, qualifying the earlier confirmed-overrun interpretation | [E0526](evidence/0526-stella-allocator-size-isolation-and-padding-trial.md), [l3-userspace-and-ipc.md](architecture/l3-userspace-and-ipc.md) | Recover identity-matched allocation/free ownership traces; keep padding opt-in and do not subtract four from returned pointers. No full Appframework replay or GUI correction verified |
| **Track B Physical UVC/PTP** (`054C:0322`) | Run B25 (`E0468`) proved `S1` (camera arms EP0 OUT status in ≤16.4 ms; host's single Status OUT was NAK'd in `NRDY→arm` window). `E0476` status pre-arm fix built (`dcc2f64f…`), audited (`117/8/0`), Unicorn-verified (`8/8 PASS`) | [E0476](evidence/0476-status-prearm-fix-build-audit-unicorn.md), [UVC_PTP_STATUS.md](UVC_PTP_STATUS.md) | Awaiting explicit user GO for single supervised Run B26 |
| **QEMU `sony-a6400` machine** (`qemu-a6400/`) | Captured four-core boot. Opt-in raster/CamPig/RC0 models produce exact deterministic video on ch51 and the stock rear LCD, with fresh Ydet records consumed by unmodified firmware. The separate USB path still stops short of opening the server on virtual cable attach | [E0524](evidence/0524-reproducible-captured-qemu-boot-and-liro-waits.md), [E0589](evidence/0589-qemu-usb-mode-gate-and-defrootview.md), [E0591](evidence/0591-qemu-a6400-first-live-view-frame.md), [qemu-a6400/STATUS.md](../qemu-a6400/STATUS.md) | Resolve captured step 26 (`0x0323`/`0x0320`) separately from the earlier packaged step-32 wait ([E0561](evidence/0561-qemu-a6400-module3-waits-on-ydet-statistics.md)). A synthetic luma chart does not qualify the sensor or recording |
| **Multi-Device Platform** (`CXD90045`) | 13 bodies / 9 kernels mapped (`E0477–E0489`); `TargetProfile` registry has `ilce_6400`, `ilce_6400a`, `zv_e10`, `ilce_6100`, `ilce_6600`, `ilce_7c`, `ilce_7m3`, `ilce_7rm4a` | [PLATFORM_MODEL.md](PLATFORM_MODEL.md), [ADDING_A_BIONZX_CAMERA.md](ADDING_A_BIONZX_CAMERA.md) | Wire `TargetProfile` directly into `UnicornMachine` and `InterpreterProbe` for universal target selection |

Historical 2026-10-18 and 2026-11-06 checkpoints are preserved in [docs/archives/](archives/).

The earlier no-peripheral captured-profile [E0527](evidence/0527-qemu-task43-initialization-flag-wait.md)
decodes Task 43's first display wait as flag 10, mask 3, pattern zero, not the
later `0x3373` loop. Its unasserted IRQs and dormant Task 65 remain bounded
observations; they do not replace the later QEMU device-model and rear-panel
results above.

## USB delivery update (2026-11-18)

[E0534](evidence/0534-original-process-usb-gadget-registration-and-otg-boundary.md)
regenerates original `im.elf` state and uses its libc heap, pthread state and
queues for configure/start commands. Original actions reach FSM
`1 -> 5 -> 6 -> 7` and create/link a bus-power gadget registration record.
`created=0`: original `createGadget` stops at unqualified OTG `SET_MODE`
`0x4001E30C`, mode 2. No hardware reply or SETCONFIG event is supplied.
Configure-stage heap ownership balances; the pending start retains its owners.
The boundary checkpoint retries the unanswered ioctl rather than skipping it.
Next: native OTG/class/gadgetcore startup contracts, then original event
production, delivery and full teardown. End-to-end configuration remains open;
[E0524](evidence/0524-original-local-osal-setconfig-route.md) is still the separate
isolated SETCONFIG delivery proof.

## A6400A service-packet update (2026-11-17)

[E0532](evidence/0532-a6400a-dmm-metadata-bounds-and-lifetime.md) extends
[E0525](evidence/0525-a6400a-jiritsu-senser-offline-packets.md) with an explicit
`--dmm-fixtures` policy: original 24-byte metadata exchanges, separate share/open
counter controls, bounded borrowed-buffer lifetime and eight repeatable packets
to a memory-only sink. Raw lengths 0, 1 and 4096 serialize exactly; failed
metadata/release completions cannot authorize sending. The physical fixture
address stays unmapped. Default replay retains all seven earlier packets and
stops before DMM metadata. Authentic allocation, multi-extent replies and full
owner teardown remain unsupported. No authentication, filesystem service, USB
command or physical compatibility is claimed; the profile remains `reference`.

## GPU completion update (2026-11-17)

[E0530](evidence/0530-gles-tgid-completion-and-shared-memory-boundary.md) verifies
the original same-TGID completion/reset and fast-flag rules, with selective
modeled cross-FD wake and no user-stack context guessing. The 128 KiB
`dmpgles2` mapping is software-shared memory, not the 16 KiB GPU BAR; bit 24
selects caller recovery, not event presence. Historical assumed engine
completions are not qualified GPU work.

Opt-in `--gles-device kernel-bound-tgid` preserves unknown shared bytes and
unsupported MMIO as explicit stops. Two original-process replays stop at
146,135,735 instructions, `libObj+0x72BF96`, on the first shared-memory read.
OSD A/B remain unmapped; no firmware-produced display frame is claimed.
Next: establish real shared backing/initialization, then functional GPU
operations, IRQ and guest-driven presentation without assumed success.
