#!/usr/bin/env python3
"""Add one file to the root directory of a FAT12/FAT16 partition image.

Used by run.sh to place inputs on the guest /setting partition (nflasha2)
inside a per-run copy, so the original eMMC image is never modified.

Refuses to overwrite: exits non-zero if the 8.3 name already exists, the
root directory is full, or there are not enough free clusters.

usage: fat_add_file.py PARTITION_IMAGE SOURCE_FILE NAME.EXT
"""

import struct
import sys


def fat_entry(fat: bytearray, fat12: bool, n: int) -> int:
    if fat12:
        off = n + n // 2
        v = fat[off] | (fat[off + 1] << 8)
        return (v >> 4) if n & 1 else (v & 0xFFF)
    return struct.unpack_from('<H', fat, 2 * n)[0]


def set_fat_entry(fat: bytearray, fat12: bool, n: int, value: int) -> None:
    if fat12:
        off = n + n // 2
        v = fat[off] | (fat[off + 1] << 8)
        if n & 1:
            v = (v & 0x000F) | (value << 4)
        else:
            v = (v & 0xF000) | value
        fat[off] = v & 0xFF
        fat[off + 1] = (v >> 8) & 0xFF
    else:
        struct.pack_into('<H', fat, 2 * n, value)


def short_name(name: str) -> tuple[bytes, int]:
    base, _, ext = name.partition('.')
    if not (1 <= len(base) <= 8 and len(ext) <= 3):
        raise SystemExit(f'not an 8.3 name: {name}')
    raw = (base.upper().ljust(8) + ext.upper().ljust(3)).encode('ascii')
    # NT case flags: 0x08 lowercase base, 0x10 lowercase extension
    flags = (0x08 if base.islower() else 0) | (0x10 if ext.islower() else 0)
    return raw, flags


def main() -> int:
    image_path, source_path, name = sys.argv[1:4]
    img = bytearray(open(image_path, 'rb').read())
    data = open(source_path, 'rb').read()
    bps, spc, reserved, nfats, root_entries, total16, _media, fat_sectors = \
        struct.unpack_from('<HBHBHHBH', img, 11)
    total = total16 or struct.unpack_from('<I', img, 32)[0]
    root_sectors = (root_entries * 32 + bps - 1) // bps
    data_start = (reserved + nfats * fat_sectors + root_sectors) * bps
    clusters = (total * bps - data_start) // (spc * bps)
    fat12 = clusters < 4085
    cluster_bytes = spc * bps
    fat_off = reserved * bps
    fat = bytearray(img[fat_off:fat_off + fat_sectors * bps])

    raw_name, case_flags = short_name(name)
    root_off = fat_off + nfats * fat_sectors * bps
    slot = None
    for i in range(root_entries):
        entry = img[root_off + 32 * i:root_off + 32 * i + 32]
        if entry[0] == 0 or entry[0] == 0xE5:
            if slot is None:
                slot = i
            if entry[0] == 0:
                break
            continue
        if entry[11] != 0x0F and entry[:11] == raw_name:
            raise SystemExit(f'{name} already exists; refusing to overwrite')
    if slot is None:
        raise SystemExit('root directory full')

    need = max(1, (len(data) + cluster_bytes - 1) // cluster_bytes)
    free = [n for n in range(2, clusters + 2) if fat_entry(fat, fat12, n) == 0][:need]
    if len(free) < need:
        raise SystemExit('not enough free clusters')
    eoc = 0xFFF if fat12 else 0xFFFF
    for a, b in zip(free, free[1:] + [eoc]):
        set_fat_entry(fat, fat12, a, b)
    for k, c in enumerate(free):
        chunk = data[k * cluster_bytes:(k + 1) * cluster_bytes]
        off = data_start + (c - 2) * cluster_bytes
        img[off:off + cluster_bytes] = chunk.ljust(cluster_bytes, b'\0')
    for f in range(nfats):
        img[fat_off + f * fat_sectors * bps:fat_off + (f + 1) * fat_sectors * bps] = fat

    entry = bytearray(32)
    entry[0:11] = raw_name
    entry[11] = 0x20                      # archive
    entry[12] = case_flags
    struct.pack_into('<H', entry, 26, free[0])
    struct.pack_into('<I', entry, 28, len(data))
    img[root_off + 32 * slot:root_off + 32 * slot + 32] = entry
    open(image_path, 'wb').write(img)
    print(f'added {name} ({len(data)} B) at cluster {free[0]} '
          f'({"FAT12" if fat12 else "FAT16"}, root slot {slot})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
