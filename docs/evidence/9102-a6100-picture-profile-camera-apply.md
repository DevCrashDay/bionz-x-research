# E9102 — Applying the Picture Profile redirect to a physical ILCE-6100

- **Date:** 2026-11-21
- **Status:** procedure only. **Not executed.** No camera was touched; the offline work is
  [E9101](9101-a6100-picture-profile-gate.md).
- **Prerequisite:** the camera runs the build whose `/usr/lib/CautionConfig.so` is
  md5 `a19110bd3d3dc7c45b4437a0cbba4839` (ILCE-6100 Ver.1.00, the JP build in the dump).
  Any other md5 means a different build: stop, the offsets below do not apply.

## What the change is

Two bytes in the dynamic symbol table of `/usr/lib/CautionConfig.so`:

| Item | Value |
| --- | --- |
| file offset | `0x138b4c` |
| before | `c8 b5 c5 00` (`0xc5b5c8`, `cmnViewSettingNodeRootSoftSkin`) |
| after | `30 d5 c5 00` (`0xc5d530`, `cmnViewSettingNodeRootPictureProfile`) |
| identical to | `CautionConfig.so.patched`, md5 `a91f2f2871b5abf6d468cfa9661fc0f0` |
| effect | the menu row that reads `Soft Skin Effect` becomes `Picture Profile` |
| side effect | Soft Skin Effect is no longer reachable while the change is in place |

## What blocks it today

Recorded from the earlier live work on this camera (`a6100-fw-recovery/docs/PP-findings.md`,
2026-11-10) and re-checked against the dump:

- `/usr` is mounted read-only on the running camera, and the `pmca-console` senser
  protocol has no `mount -o remount,rw`. A push to `/usr/...` returns error 130. `/tmp` is
  a small RAM filesystem and cannot stage the 12 MB file (same error 130).
- `term on` exposes a debug console over USB, but in the app build used then the typed
  input never reached the shell, so no remount was possible. The remaining route is the
  hardware UART test points.
- **Sony published no firmware updater for the original ILCE-6100** (any region): it ships
  on Ver.1.00 and no reflash package exists. The only recovery net is the stock
  `CautionConfig.so.bak` plus a writable `/usr` — the same capability that is missing. The
  ILCE-6100A Ver.1.01 updater is a different model and must not be applied.

So the honest answer to "how do I try it on the camera" is: **you need a root shell with
`/usr` writable, and this camera does not currently offer one.** Until that changes, the
mod lives in the emulator only, and the camera stays untouched.

## If a root shell becomes available (for example a fixed PMCA-RE console or UART)

Do this with the camera on a charged battery and a card inserted, not over a flaky link.

```sh
# 1. prove which build is installed before touching anything
md5sum /usr/lib/CautionConfig.so        # must print a19110bd3d3dc7c45b4437a0cbba4839

# 2. keep a copy reachable from outside the camera (already held on the PC:
#    CautionConfig.so.bak and .bak2, both md5 a19110bd...)
cp /usr/lib/CautionConfig.so /mnt/sd/CautionConfig.so.bak && sync

# 3. write the two bytes in place - no 12 MB transfer
mount -o remount,rw /usr || exit 1
printf '\x30\xd5' | dd of=/usr/lib/CautionConfig.so bs=1 seek=$((0x138b4c)) conv=notrunc
sync
md5sum /usr/lib/CautionConfig.so        # must print a91f2f2871b5abf6d468cfa9661fc0f0
mount -o remount,ro /usr

# 4. reboot, then open MENU -> camera tab 1 -> page 9/11:
#    the last row must read "Picture Profile" instead of "Soft Skin Effect".
```

Rollback is the same command with the original bytes:

```sh
mount -o remount,rw /usr
printf '\xc8\xb5' | dd of=/usr/lib/CautionConfig.so bs=1 seek=$((0x138b4c)) conv=notrunc
sync && mount -o remount,ro /usr
```

If step 4 shows the row but the PP screen misbehaves, roll back and report it; the row
being replaced is the only UI change this patch makes.

## What the emulator already proved

- stock boot, page 9/11: `Soft Skin Effect  Off`
- `PP_UNLOCK=1`, same page and row: `Picture Profile  PP7`
- generated patch byte-identical to the validated reference; boot log unchanged

## Why this record exists

So the procedure, the exact bytes, the verification md5 and the blockers are written down
once, and nobody has to rediscover them (or guess an offset) while holding a camera.
