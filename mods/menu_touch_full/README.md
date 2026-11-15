# Full-Menu Touch Mod (`MTF2`)

RAM-only dual-hook touch mod for Sony `ILCE-6400` (`im.elf`). Original standalone version: [E0520](../../docs/evidence/0520-mtf2-full-menu-touch-verified-on-a6400.md).
Loader installation and combined physical test: [E0522](../../docs/evidence/0522-mods-tab-icon-and-uxd-image-container.md#13-default-artwork-in-the-mod-loader-and-menutouchfull-coexistence).

## Verified Mod Loader build (v3, 2026-11-15)

| File | SHA-256 |
| --- | --- |
| `MenuTouchFull.mod` (SMOD, 2940 B, code 2160 B at `0x0700d708`, state 632 B at `0x07143920`) | `334a3dd491d04a87e5318694ac652fca65dfe7cd2575e7e6886620bc282b1b80` |

Copy [MenuTouchFull.mod](MenuTouchFull.mod) to the SD card's `mods/` directory, or stage it under
`/tmp_bt/mods/` for a single boot. Install the [Mod Loader](../../README.md#short-tutorial--load-a-mod-from-the-sd-card),
then open MENU → Mods → `Menu Touch Full` and press the center button. The `.mod` carries its MTF2
state header (`SMOD_FLAG_STATE_INIT`).

## Capabilities

- Tab taps in every tab, including with the cursor on the tab bar and from My Menu / Mods
- Page-dot taps and horizontal swipe paging (`|dx| >= 48`) in ordinary setting tabs
- 1st-tap row select, 2nd-tap popup open (`0x15010052u` `KEY_ENTER`)
- Popup option tap: live select + apply without closing (`menu + 0x1fc`, `0x157f8ca5u`)
- Popup vertical swipe scrolling (`|dy| >= 36`)
- Outside-popup tap to close only the popup (`0x15010102u` `KEY_MENU`)
- Rows/options inside My Menu and Mods are intentionally not touch-handled

## Rebuild

```bash
wsl -e bash mods/menu_touch_full/build_mtf2_wsl.sh artifacts/hardware/mtf2_v3/menu_touch_full.elf -DMT_MAX_TABS=7u
```

```bash
.\venv\Scripts\python.exe -m tools.research.pack_mod --elf artifacts/hardware/mtf2_v3/menu_touch_full.elf --no-tab-patch
```

`MT_MAX_TABS=7u` accounts for the Mod Loader's injected 7th tab. The standalone E0520 build (6 tabs) is preserved as
`artifacts/hardware/menu_touch_full.elf`.
