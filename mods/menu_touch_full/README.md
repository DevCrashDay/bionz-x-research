# Full-Menu Touch Mod (`MTF2`)

Verified RAM-only dual-hook touch mod for Sony `ILCE-6400` (`im.elf`), documented in [E0520](../../docs/evidence/0520-mtf2-full-menu-touch-verified-on-a6400.md).

## Capabilities

- Main menu tab and page-dot touch switching
- Horizontal swipe paging (`|dx| >= 48`)
- 1st-tap row select, 2nd-tap popup open (`0x15010052u` `KEY_ENTER`)
- Popup option tap: live select + apply without closing (`menu + 0x1fc`, `0x157f8ca5u`)
- Popup vertical swipe scrolling (`|dy| >= 36`)
- Outside-popup tap to close only the popup (`0x15010102u` `KEY_MENU`)
