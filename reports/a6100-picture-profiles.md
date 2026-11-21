# a6100 Picture Profiles — status

**Verdict: no data-layer gate found; no unlock applied. One provenance anomaly blocks a
stock-level answer.** Details: [E9101](../docs/evidence/9101-a6100-picture-profile-gate.md).

## What was asked

Make PP1–PP10 and their settings available on the ILCE-6100 by removing or satisfying the
firmware's own gate, in the emulator first, with evidence.

## What the dump shows

Every layer that could hide Picture Profile on a model carries it on this a6100 image:

| Layer | Result |
| --- | --- |
| Menu text (`string_english.uxc`) | byte-identical to the a6400 file that has PP; contains PP1–PP10, S-Log2/3, S-Gamut3, HLG1–3, Cine1–4 |
| Setting-node classes (`CautionConfig.so`) | 13 `cmnViewSettingNodeRootPictureProfile*` exports |
| Root registry | PP at index 251 of 416, same index as the a6400 |
| Menu trees (`viewUnified2.so`) | 21 references to the PP node, 15 to S-Log Display Assist — same counts as the a6400 |
| Settings (`Backup.bin`, BK4) | 30,762 units, a strict superset of the a6400 (0 missing) |
| ISP pipeline (`av-cam.bin`) | `SET_PICTUREPROFILE`, `CheckSetPictureProfile`, `PP_GammaType`/`PP_ColorMode`, S-LOG2, ITU709 |

So this is neither "menu entry hidden by a flag" nor "pipeline missing": the data is
complete, and the gate has to be a runtime availability decision. The named candidate is
`CmnForUseMultipleViewStatus::setPictureProfileValidInfo(bool)` (`viewUnified2.so`
`0x00413940`, one byte store at `0x0041394C`). Its producer and object address are not
established, so nothing was patched.

## Blocker

`/lib/CautionConfig.so` in the dumped root filesystem has `mtime 2026-11-19 19:55:03`,
mode `0644`, while every sibling application file is `2021-04-05`, mode `0555`. The dump
is from 2026-11-20. The file that decides per-item availability is therefore **not a
stock file**, and this dump cannot say what a stock a6100 hides. No hash was refreshed and
no file was rewritten.

## What is in the checkout

- `tools/research/a6100_picture_profile.py` — audit: verifies the dump's stored hashes
  (reports a mismatch, never fixes it), checks the six layers, reads the identity of the
  validity setter, and flags rootfs files written after the stock date.
- `tests/test_a6100_picture_profile.py` — 11 cases: hash mismatch stays a mismatch, all
  six layers present, PP at registry index 251, 21/15 menu references, settings superset,
  shared string resource, non-stock file flagged, setter body and its refusal path.
- Boot receipt: a6100 boots on QEMU to eMMC tuning and VPH scanout, killed by the 60 s
  timeout at guest time 3.78 s. Default boot path untouched.

## Next

1. A pre-2026-11-19 `CautionConfig.so` to separate the model from the modification.
2. A runtime trace for the validity byte once the guest passes the av-cam step-32 wait.
3. A one-byte opt-in validity patch only after 1–2, with the default path unchanged.

Nothing here needs a physical camera write.
