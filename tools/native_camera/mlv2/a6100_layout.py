"""Mod Loader v2 build layout for the ILCE-6100 (a6100) V1.00 `viewUnified2.so`.

The loader's three view hooks are byte-identical between the a6100 build and the
ILCE-6400 2.00 build the v2 sources were written for; only the offsets differ.
This module records the a6100 offsets, checks them against the shipped image,
and refuses loudly if the image is not the build it was derived from.

It is a layout table, not a loader: it lets the trampolines and the guard target
this build, and it is the place a future a6100 core/guard reads instead of the
a6400 constants.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# SHA-256 of the viewUnified2.so in the ILCE-6100 V1.00 root filesystem.
VIEWUNIFIED2_SHA256 = '7bb0f245c8ccdff8a7d4a6f473357b50cbdeed12d081bde805edffd339c28bce'

# site -> a6100/a6400 offsets, the bytes the trampoline replays (`original`,
# `replay` bytes) and a longer unique locator (`signal`). The replayed bytes are
# identical in both builds; the signal is what `verify()` searches for, because
# an 8-byte replay is ambiguous in a 11 MB image.
HOOKS = {
    'set_root': dict(a6100=0x5acc0c, a6400=0x5ab654, replay=16,
                     original='044600af51b100f5c23303f52b731960',
                     signal='044600af51b100f5c23303f52b731960'),
    'refresh': dict(a6100=0x5ad038, a6400=0x5aba80, replay=14,
                    original='2de9f04f9bb006af044607f12000',
                    signal='2de9f04f9bb006af044607f12000'),
    'open_item': dict(a6100=0x5ad5fc, a6400=0x5ac044, replay=8,
                      original='00f5c23303f52c73',
                      signal='00f5c23303f52c732de9'),
}


class LayoutMismatch(ValueError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify(image: bytes, *, expect_sha256: str | None = VIEWUNIFIED2_SHA256) -> dict:
    """Return the resolved hook table, or raise if the image is not this build.

    The offsets are checked two ways: the recorded offset must carry the
    recorded original bytes, and those bytes must occur exactly once in the
    image (a moved or duplicated site is a mismatch, not a guess).
    """
    if expect_sha256 is not None:
        actual = sha256(image)
        if actual != expect_sha256:
            raise LayoutMismatch(f'viewUnified2 SHA-256 {actual} is not the '
                                 f'a6100 build {expect_sha256}')
    out = {}
    for name, row in HOOKS.items():
        original = bytes.fromhex(row['original'])
        signal = bytes.fromhex(row['signal'])
        at = image[row['a6100']:row['a6100'] + row['replay']]
        if at != original:
            raise LayoutMismatch(f'{name}: bytes at +{row["a6100"]:#x} are '
                                 f'{at.hex()}, expected {original.hex()}')
        hits = []
        pos = 0
        while True:
            i = image.find(signal, pos)
            if i < 0:
                break
            hits.append(i)
            pos = i + 1
        if hits != [row['a6100']]:
            raise LayoutMismatch(f'{name}: site occurs at '
                                 f'{[hex(h) for h in hits]}, expected once at '
                                 f'+{row["a6100"]:#x}')
        out[name] = dict(offset=row['a6100'], original=original, replay=row['replay'],
                         signal=signal, trap_after=row['replay'])
    return out


def from_file(path: Path) -> dict:
    return verify(Path(path).read_bytes())
