"""Where is Picture Profile gated on an ILCE-6100 (a6100)?

Offline, read-only audit of a captured a6100 partition dump plus its extracted
root filesystem. The tool never writes to the dump and never "fixes" a hash: a
mismatch is reported as a mismatch.

It answers one question per layer, because a missing menu entry and a missing
pipeline need different fixes:

  strings   the localized PP text exists in share/app/string_english.uxc
  nodes     CautionConfig.so exports the PP setting-node objects
  registry  the model's root-node registry still lists the PP node
  menu      viewUnified2.so's menu trees reference the PP node
  settings  Backup.bin (BK4) carries the PP setting units
  pipeline  av-cam.bin carries the PP command and its gamma/colour code

If all six are present, the camera does not hide PP by removing data: the
remaining gate can only be a runtime decision. The one runtime decision with a
name is `CmnForUseMultipleViewStatus::setPictureProfileValidInfo(bool)`, a
single byte store exported by viewUnified2.so. Its producer and its object
address are not established here, so this tool reports the candidate and patches
nothing.

Usage:
    python3 -m tools.research.a6100_picture_profile report \
        --dump /path/to/dump --rootfs-image nflasha15.img \
        --defs-dir /path/to/extracted/rootfs \
        --a6400-backup /path/to/a6400/Backup.bin [--json out.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

# Files that decide PP availability one layer at a time.
APP_FILES = {
    'strings': 'share/app/string_english.uxc',
    'nodes': 'lib/CautionConfig.so',
    'menu': 'lib/viewUnified2.so',
    'app_binary': 'bin/im.elf',
}

# Stock a6100 root filesystem timestamps are 2021 (firmware build year). A file
# carrying a later timestamp was written after the model firmware shipped;
# report it instead of silently trusting it.
STOCK_ROOTFS_YEAR = '2021'

PP_STRINGS = ('Picture Profile', 'PP1', 'PP10', 'S-Log2', 'S-Log3',
              'S-Gamut3', 'HLG1', 'HLG3', 'Cine1', 'Cine4', 'ITU709')

PP_NODE = 'cmnViewSettingNodeRootPictureProfile'
SLOG_NODE = 'cmnViewSettingNodeRootSLogDispAssist'

# av-cam.bin markers: the LIRO command handler, its parameter validator and the
# gamma selection code that consumes the PP state.
AVCAM_MARKERS = (b'SET_PICTUREPROFILE[%d', b'CheckSetPictureProfile',
                 b'PP_GammaType', b'PP_ColorMode', b'S-LOG2', b'ITU709')

R_ARM_ABS32 = 2


# ---------------------------------------------------------------- dump hashes

def parse_checksums(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, name = line.split(None, 1)
        out[name.lstrip('*')] = sha.lower()
    return out


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(16 * 1024 ** 2):
            h.update(chunk)
    return h.hexdigest()


def verify_dump(dump: Path) -> dict:
    """Recompute every stored hash. A mismatch stays a mismatch."""
    sums = parse_checksums((dump / 'SHA256SUMS').read_text())
    rows = {}
    for name, expected in sorted(sums.items()):
        path = dump / name
        if not path.is_file():
            rows[name] = dict(expected=expected, actual=None, ok=False, note='missing')
            continue
        actual = digest(path)
        rows[name] = dict(expected=expected, actual=actual, ok=actual == expected)
    return dict(ok=all(r['ok'] for r in rows.values()), files=rows)


# ------------------------------------------------------------------ ELF32 bits

def elf_sections(data: bytes) -> list[dict]:
    if data[:4] != b'\x7fELF' or data[4] != 1:
        raise ValueError('expected a 32-bit ELF')
    shoff, = struct.unpack_from('<I', data, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', data, 0x2e)
    sections = []
    for i in range(shnum):
        f = struct.unpack_from('<IIIIIIIIII', data, shoff + i * shentsize)
        sections.append(dict(name_off=f[0], type=f[1], addr=f[3], offset=f[4],
                             size=f[5], link=f[6]))
    strtab = sections[shstrndx]
    for section in sections:
        end = data.index(b'\0', strtab['offset'] + section['name_off'])
        section['name'] = data[strtab['offset'] + section['name_off']:end].decode()
    return sections


def elf_symbols(data: bytes, sections: list[dict], index: int) -> dict[int, str]:
    section = sections[index]
    strtab = sections[section['link']]
    names = {}
    for i in range(section['size'] // 16):
        name_off = struct.unpack_from('<I', data, section['offset'] + i * 16)[0]
        end = data.index(b'\0', strtab['offset'] + name_off)
        names[i] = data[strtab['offset'] + name_off:end].decode()
    return names


def elf_relocations(data: bytes, sections: list[dict]) -> list[dict]:
    rows = []
    for section in sections:
        if section['type'] != 9:  # SHT_REL
            continue
        names = elf_symbols(data, sections, section['link'])
        for i in range(section['size'] // 8):
            r_offset, r_info = struct.unpack_from('<II', data, section['offset'] + i * 8)
            rows.append(dict(offset=r_offset, type=r_info & 0xff,
                             symbol=names.get(r_info >> 8, '')))
    return rows


def section_data(data: bytes, sections: list[dict], name: str) -> bytes:
    section = next(s for s in sections if s['name'] == name)
    return data[section['offset']:section['offset'] + section['size']]


# ------------------------------------------------------------------- BK4 units

def bk4_units(raw: bytes) -> dict[int, int]:
    """Property units present in a BK4 preset (id -> size). Structure per
    src/a6400/execution/backup_service.py, reimplemented here without importing
    the emulator package."""
    if len(raw) < 0x100 or raw[12:16] != b'BK4\0':
        raise ValueError('not a BK4 preset')
    subsystems, properties, data_offset, data_size, _ = struct.unpack_from('<5I', raw, 0x10)
    if 0x100 + 6 * subsystems + 6 * properties > data_offset:
        raise ValueError('BK4 tables overlap the payload')
    if data_offset + data_size > len(raw):
        raise ValueError('BK4 payload exceeds the file')
    subsys_off = 0x100
    props_off = 0x100 + subsystems * 6
    units = {}
    for sub in range(subsystems):
        count, base = struct.unpack_from('<HI', raw, subsys_off + sub * 6)
        for prop in range(count):
            _, value = struct.unpack_from('<HI', raw, props_off + (base + prop) * 6)
            if value == 0xffffffff:
                continue
            flag = value >> 24
            off = value & 0x00ffffff
            if flag == 0:
                size, = struct.unpack_from('<H', raw, off)
            elif flag == 0xff:
                size, = struct.unpack_from('<H', raw, off)
            else:
                size = flag
            units[(sub << 16) | prop] = size
    return units


# --------------------------------------------------------------------- layers

def string_layer(uxc: bytes) -> dict:
    counts = {token: uxc.count(token.encode()) for token in PP_STRINGS}
    return dict(ok=all(counts[t] for t in ('Picture Profile', 'PP1', 'PP10', 'S-Log2', 'S-Log3')),
                counts=counts)


def node_layer(caution: bytes) -> dict:
    sections = elf_sections(caution)
    names = set(elf_symbols(caution, sections, next(i for i, s in enumerate(sections)
                                                    if s['name'] == '.dynsym')).values())
    exported = sorted(n for n in names if n.startswith(PP_NODE))
    return dict(ok=bool(exported), exported=exported)


def registry_layer(caution: bytes) -> dict:
    """The root-node registry is the array of PP-setting nodes ending .data.
    Report the PP node's index and whether PP sub-items share the layout."""
    sections = elf_sections(caution)
    relocs = [r for r in elf_relocations(caution, sections) if r['type'] == R_ARM_ABS32]
    by_offset = {r['offset']: r['symbol'] for r in relocs}
    data_section = next(s for s in sections if s['name'] == '.data')
    end = data_section['addr'] + data_section['size']
    last = end - 4
    start = last
    while (start - 4) in by_offset:
        start -= 4
    entries = [by_offset.get(off, '') for off in range(start, last + 4, 4)]
    index = entries.index(PP_NODE) if PP_NODE in entries else None
    sub_items = [i for i, name in enumerate(entries) if name.startswith(PP_NODE)]
    return dict(ok=index is not None, base=hex(start), entries=len(entries),
                pp_index=index, pp_entries=len(sub_items), sub_item_indices=sub_items)


def menu_layer(view_unified: bytes) -> dict:
    sections = elf_sections(view_unified)
    relocs = elf_relocations(view_unified, sections)
    pp = [r for r in relocs if r['symbol'] == PP_NODE]
    slog = [r for r in relocs if r['symbol'] == SLOG_NODE]
    return dict(ok=bool(pp), pp_references=len(pp), slog_references=len(slog),
                pp_reference_offsets=[hex(r['offset']) for r in pp])


def pipeline_layer(avcam: bytes) -> dict:
    found = {m.decode(): (m in avcam) for m in AVCAM_MARKERS}
    return dict(ok=all(found.values()), markers=found)


def settings_layer(backup: bytes, a6400_backup: Path | None) -> dict:
    units = bk4_units(backup)
    result = dict(ok=True, units=len(units))
    if a6400_backup is not None:
        reference = bk4_units(a6400_backup.read_bytes())
        missing = sorted(set(reference) - set(units))
        extra = sorted(set(units) - set(reference))
        result.update(reference_units=len(reference), missing_from_a6100=len(missing),
                      extra_in_a6100=len(extra),
                      superset=not missing)
        result['ok'] = not missing
    return result


# ----------------------------------------------------------------- provenance

def debugfs_stat(image: Path, path: str) -> dict:
    out = subprocess.run(['debugfs', '-R', f'stat {path}', str(image)],
                         capture_output=True, text=True, check=False)
    text = out.stdout + out.stderr
    mode = mtime = None
    for line in text.splitlines():
        stripped = line.strip()
        if 'Mode:' in stripped:
            mode = stripped.split('Mode:')[1].split()[0]
        if stripped.startswith('mtime:'):
            rest = stripped.split('mtime:')[1].strip()
            mtime = rest.split('--', 1)[1].strip() if '--' in rest else rest
    if mode is None:
        raise ValueError(f'debugfs could not stat {path} in {image}: {text.strip()[:200]}')
    return dict(mode=mode, mtime=mtime, stock=STOCK_ROOTFS_YEAR in (mtime or ''))


def provenance(rootfs_image: Path) -> dict:
    rows = {}
    for name in ('lib/CautionConfig.so', 'lib/viewUnified2.so',
                 'share/app/string_english.uxc'):
        rows[name] = debugfs_stat(rootfs_image, '/' + name)
    suspect = sorted(n for n, row in rows.items() if not row['stock'])
    return dict(ok=not suspect, files=rows, suspect=suspect)


# ------------------------------------------------------------------ opt-in flag

VALIDITY_SYMBOL = '_ZN27CmnForUseMultipleViewStatus26setPictureProfileValidInfoEb'
# Thumb `strb r0, [r3, #0]`. The setter is:
#   ldr r3,[pc,#12]; ldr r2,[pc,#16]; add r3,pc; push {r7,lr}; add r7,sp,#0;
#   ldr r3,[r3,r2]; strb r0,[r3,#0]; pop {r7,pc}; .word a; .word b
VALIDITY_STORE = b'\x18\x70'


def va_to_offset(sections: list[dict], va: int) -> int | None:
    for section in sections:
        if section['type'] == 8:  # NOBITS
            continue
        if section['addr'] <= va < section['addr'] + section['size']:
            return section['offset'] + (va - section['addr'])
    return None


def validity_setter(view_unified: bytes) -> dict | None:
    """Locate `CmnForUseMultipleViewStatus::setPictureProfileValidInfo(bool)`.

    The body is one byte store through a GOT-resolved object, so its address
    cannot be derived from the file alone: the report says what is established
    and leaves the object address to a runtime trace.
    """
    sections = elf_sections(view_unified)
    dynsym = next(s for s in sections if s['name'] == '.dynsym')
    symbols = elf_symbols(view_unified, sections, sections.index(dynsym))
    text = next(s for s in sections if s['name'] == '.text')
    dynsym_index = sections.index(dynsym)
    for index, name in symbols.items():
        if name != VALIDITY_SYMBOL:
            continue
        _name, value, _size, _info, _other, _shndx = struct.unpack_from(
            '<IIIBBH', view_unified, dynsym['offset'] + index * 16)
        fn_va = value & ~1
        fn = va_to_offset(sections, fn_va)
        if fn is None:
            return None
        body = view_unified[fn:fn + 24]
        if body[12:14] != VALIDITY_STORE:
            raise ValueError('validity setter body does not match the analysed encoding')
        got_base = (fn_va + 4 + struct.unpack_from('<I', body, 16)[0]) & 0xffffffff
        slot_va = (got_base + struct.unpack_from('<I', body, 20)[0]) & 0xffffffff
        return dict(function_va=hex(fn_va), store_va=hex(fn_va + 12),
                    store_encoding=body[12:14].hex(), got_slot_va=hex(slot_va),
                    object_resolved=False,
                    note='object address is loaded through the GOT; resolving it '
                         'needs a runtime module base and was not attempted')
    return None


# ---------------------------------------------------------------------- report

def report(dump: Path | None, rootfs_image: Path, defs_dir: Path, *,
           a6400_backup: Path | None = None) -> dict:
    files = {key: (defs_dir / rel) for key, rel in APP_FILES.items()}
    view_unified = files['menu'].read_bytes()
    caution = files['nodes'].read_bytes()
    if dump is not None and (dump / 'SHA256SUMS').is_file():
        hash_check = verify_dump(dump)
    else:
        hash_check = dict(ok=False, available=False,
                          note='no dump with SHA256SUMS supplied; hashes not checked')
    result = dict(
        schema_version=1,
        dump=dict(path=None if dump is None else str(dump), hash_check=hash_check),
        inputs={key: dict(path=str(path), bytes=path.stat().st_size,
                          sha256=digest(path)) for key, path in files.items()},
        layers={},
        warnings=[],
    )
    available = {key: path for key, path in
                 (('av-cam', defs_dir / 'av-cam.bin'),
                  ('Backup', defs_dir / 'Backup.bin')) if path.is_file()}
    for key, path in available.items():
        result['inputs'][key] = dict(path=str(path), bytes=path.stat().st_size,
                                     sha256=digest(path))
    result['layers']['strings'] = string_layer(files['strings'].read_bytes())
    result['layers']['nodes'] = node_layer(caution)
    result['layers']['registry'] = registry_layer(caution)
    result['layers']['menu'] = menu_layer(view_unified)
    if 'av-cam' in available:
        result['layers']['pipeline'] = pipeline_layer(available['av-cam'].read_bytes())
    else:
        result['layers']['pipeline'] = dict(ok=False, note='av-cam.bin not supplied')
    if 'Backup' in available:
        result['layers']['settings'] = settings_layer(available['Backup'].read_bytes(),
                                                      a6400_backup)
    else:
        result['layers']['settings'] = dict(ok=False, note='Backup.bin not supplied')

    result['provenance'] = provenance(rootfs_image)
    if result['provenance']['suspect']:
        result['warnings'].append(
            'rootfs file(s) written after the stock image date: '
            + ', '.join(result['provenance']['suspect'])
            + ' - conclusions about the stock model build are limited')

    try:
        setter = validity_setter(view_unified)
        error = None
    except ValueError as exc:
        setter, error = None, str(exc)
    if error:
        result['warnings'].append(f'validity setter not readable: {error}')
    result['runtime_candidate'] = dict(
        symbol=VALIDITY_SYMBOL,
        found=setter is not None,
        detail=setter,
        note=('single-byte validity flag; its producer was not located, so no '
              'camera-visible unlock is claimed and nothing is patched'),
    )

    data_layers = ('strings', 'nodes', 'registry', 'menu', 'settings', 'pipeline')
    present = [k for k in data_layers if result['layers'].get(k, {}).get('ok')]
    result['verdict'] = dict(
        data_layers_present=present,
        data_layers_absent=[k for k in data_layers if k not in present],
        gate=('runtime-validity-flag' if len(present) == len(data_layers)
              else 'data-layer-missing'),
        confidence=('medium' if result['provenance']['suspect'] else 'high'),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    rep = sub.add_parser('report', help='audit the dump and print the gate report')
    rep.add_argument('--dump', type=Path,
                     help='read-only dump directory with SHA256SUMS (optional)')
    rep.add_argument('--rootfs-image', type=Path, required=True,
                     help='nflasha15 partition image (ext4)')
    rep.add_argument('--defs-dir', type=Path, required=True,
                     help='directory with the extracted rootfs files')
    rep.add_argument('--a6400-backup', type=Path)
    rep.add_argument('--json', type=Path)

    args = parser.parse_args(argv)
    result = report(args.dump, args.rootfs_image, args.defs_dir,
                    a6400_backup=args.a6400_backup)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(text + '\n')
    print(text)
    return 0 if result['dump']['hash_check']['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
