# E0643 - CXD90045 MISCREG/eFuse decode, physical MSC read, and ILCE-6400A Jiritsu RAM-chunk planner

Date: 2026-11-19. Combines offline disassembly of preserved `CXD90045` boot/kernel/service images, one authorized read-only single-page `/dev/mem` observation on the connected `ILCE-6400` in Mass Storage, and an offline `plan-chunk-dump` extension to `tools/research/a6400a_jiritsu_bootstrap.py` (no `ILCE-6400A` USB session, no firmware flash write, and no persistent camera change).

## Result

1. **No kernel or second-stage signature check in the analyzed `ILCE-6400` / `ILCE-6400A` binaries.**
   Neither `ILCE-6400` `DGU5` (`nflashaB0`), `ILCE-6400` main kernel (`8.2.6.0`, 24,600 symbols), nor `ILCE-6400A` 1.01 main kernel (`8.2.7.6`, 24,514 symbols) contains an `efuse`, `otp`, `rsa`, `trustzone`, `tz_`, or `smc` driver or cryptographic kernel signature verification. Any hardware OTP/eFuse root-of-trust verification on `CXD90045` is bounded to the masked SoC BootROM before handoff to `nflashaB0` (`0xFE047000`).
2. **`CXD90045` `MISCREG`/eFuse and `ASV` MMIO addresses decoded from `DGU5` and physically verified on `ILCE-6400`.**
   Thumb-2 disassembly of `nflashaB0` (`0xFE04D59A..0xFE04D720`) resolves `0xF2915000` (`EFUSE_CHIPID` / `EFUSE_TYPEID`), `0xF2915010` (`MODEREAD`), SRAM `0xFE040600` (`MIS` parameter block), and `0xF2927000` (`ASV` voltage-binning register). A bounded single-page (`4 KiB`, `O_RDONLY | PROT_READ`) `/dev/mem` read on the live `ILCE-6400` returns `0xF2915000 = 0x00000500` and `0xF2915010 = 0x0c01002b` (chip/type/mode IDs only, readable from Linux, with `0xF2915004..0xF291500C` and `0xF2915014..0xF291503C` zero).
3. **`ILCE-6400A` 1.01 `libJiritsu.so` synchronous `dlopen` and `tmpfs` OOM root cause + offline chunk planner.**
   Disassembly of `adj_cmd_set_file2` (`0x5A68..0x5D26`) in `ILCE-6400A` 1.01 `libJiritsu.so` confirms `dlopen("/tmp/jiritsu/libJit.so", RTLD_LAZY)` at `0x5D08` and `dlclose()` at `0x5D20` execute synchronously on the `im.elf` thread. Staging full partitions or large buffers in RAM `tmpfs` exhausts memory and triggers `im.elf` OOM / hardware watchdog (`wdt.timeout=500`) resets; re-uploading `libJit.so` in the same power cycle spawns competing background loops. `tools/research/a6400a_jiritsu.py` and `tools/research/a6400a_jiritsu_bootstrap.py --mode plan-chunk-dump` now generate bounded single-slot `<= 512 KiB` RAM-chunk plans (including `/dev/nflashaB0` and `/dev/nflashaB1`) and an asynchronous polling worker script strictly offline.

## 1. Inputs and identities

| Input | Bytes | SHA-256 | Role |
| --- | ---: | --- | --- |
| `ILCE-6400` `nflashaB0-4194304.bin` (`E0414`) | 4,194,304 | `3db81064bbb62f2af3937bc68adac7b37482a8f3a582734cb2036fd5a3f4d260` | `EXBL` + `DGU5` `V85.00.03` bootloader |
| `ILCE-6400` `live-kallsyms.json` (`E0415`) | 2,547,031 | `edfab49ef7c933f1ea45d62c6f61c2dfafa93f14b05d7c6660d8899e000cbc02` | Decoded symbols for live `vmlinux.bin` (`d7a9ae09…`) |
| `ILCE-6400A` 1.01 `nflasha3_unpacked/vmlinux.bin` | 4,117,760 | `9d51ab60df8246f10cb2afb12fb504d7f148fae437ee404f5b9837cc3b8f85ca` | Sony CE Linux `8.2.7.6` main kernel (`E0506`) |
| `ILCE-6400A` 1.01 `nflasha15_unpacked/lib/libJiritsu.so` | 162,568 | `c3621c7ec30771604b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff` | `Jiritsu` service handler (`E0554`) |
| Freestanding ARM helper `ro_devmem_tiny` | 1,244 | `6750a05b8e85ccf4fb0243f85bcae465f3d63e38bf1900df5085ec52d9425242` | Temporary `/tmp_bt/_ro_devmem`, `O_RDONLY` + `PROT_READ` `mmap2`, removed after single read |

Cross-check of `ILCE-6400` (2.00 package) vs. `ILCE-6400A` (1.01 package) `nflasha3` boot payloads also shows byte-identical `ldr_drv.bin` (`d5cec3780a459f49…`), `dfe_app.bin` (`8f9a8d3f8e270d95…`), `dfe_dat.bin` (`703f457a480eee74…`), `lif_app.bin` (`5e1ebaa18e498e32…`), `bonobo.bin` (`a9e739bc28aa8431…`), and `dat.crypterName: CXD90045`.

## 2. `DGU5` `MISCREG`/eFuse decode and live `ILCE-6400` read

- **Static decode (`arm-linux-gnueabi-objdump -M force-thumb` on `nflashaB0`):**
  - Non-zero data in `nflashaB0` ends at `0x272EB`; no trailing cryptographic signature block follows `EXBL`.
  - Function `0xFE04D59A` reads `0xF2927000 & 0x1F` (`LDR:ASV val:0x%08X(@0x%08X)`).
  - Function `0xFE04D5E0` (`LDR:read MISCREG`) copies `0x200` bytes from SRAM `0xFE040600` (`0xFE04D604`). If byte `+0` is `!= 0xFF`, it takes `EFUSE_TYPEID = byte[+1] << 8`, `EFUSE_CHIPID = byte[+0]`, and reads `MODEREAD` from MMIO `0xF2915010` (`0xFE04D63E`); otherwise (`0xFE04D702`), it sets `EFUSE_TYPEID = 0x700` and reads `EFUSE_CHIPID` from `0xF2915000` (`uxtb`, `0xFE04D712`).
- **Live `ILCE-6400` read-only observation (MSC, `tools.camera_debugger`):**
  - Initial `dd if=/dev/mem bs=4096 count=1 skip=993557` failed with `dd: /dev/mem: Bad address` (`EFAULT`) because ARM Linux `read_mem()` only translates `System RAM` in `/proc/iomem`.
  - Single-page `mmap2` (`O_RDONLY | O_SYNC`, `PROT_READ`, `4096` bytes at `0xF2915000`) via `/tmp_bt/_ro_devmem` succeeded and was immediately removed:
    - `0xF2915000`: `0x00000500`
    - `0xF2915010`: `0x0c01002b`
    - `0xF2915004..0xF291500C` and `0xF2915014..0xF291503C`: `0x00000000`
  - Post-check camera receipts: `/proc/uptime` monotonic (`4897.90` → `4942.00` → `5009.50` → `5175.60`), debugger `ping` responsive (`PONG protocol=2 nonce=0xa6400001 pid=700 transport=usb_extcmd`), `/tmp_bt` clean.

## 3. `ILCE-6400A` `libJiritsu.so` execution constraint and RAM-chunk planner

- Disassembly of `adj_cmd_set_file2` (`0x5A68..0x5D26`) in `ILCE-6400A` 1.01 `libJiritsu.so` shows that after writing `/tmp/jiritsu/libJit.so`, the handler calls `dlopen("/tmp/jiritsu/libJit.so", RTLD_LAZY)` at `0x5D08` and `dlclose()` at `0x5D20` synchronously before returning.
- Updated `tools/research/a6400a_jiritsu.py` and `tools/research/a6400a_jiritsu_bootstrap.py`:
  - `STORAGE_PATH` now allows `/dev/nflashaB0` and `/dev/nflashaB1` while retaining `/dev/nflasha23` rejection (`EXCLUDED_STORAGE`).
  - `chunk_read_command` prepends `busybox rm -f {destination}` before `dd` + `sha256sum` into a single reused `{ram_dir}/chunk` path (`<= 512 KiB`).
  - `plan_partition_chunk_dump` and `--mode plan-chunk-dump` generate bounded chunk plans (`A6400A_DUMP_PARTITIONS`) and `ephemeral_chunk_worker_script` (asynchronous polling worker for `/tmp/jiritsu/run.sh`, `/tmp_network` `tmpfs` verified).
  - Live camera modes (`check`, `start-ram-debugger`, `jiritsu-dump`) remain closed offline (`exit 2`, `JiritsuUnverified`) until live `ILCE-6400A` `GET_FILE` framing is verified.

## 4. Verification

```powershell
python -m tools.research.a6400a_jiritsu_bootstrap --mode plan-chunk-dump --partition /dev/nflashaB0 --report artifacts\tests\e0643-check\chunk-plan.json
python -m unittest tests.test_a6400a_jiritsu -v
python -m tools.research.ci_check --summary
```

- `tests.test_a6400a_jiritsu` and CI guardrails pass; `plan-chunk-dump` stays strictly offline and writes no camera or flash state.
