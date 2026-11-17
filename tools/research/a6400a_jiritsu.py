"""ILCE-6400A Jiritsu helpers that do not open a camera by themselves.

The local nflasha5 warm-boot image proves the service names and temporary
paths. It does not prove the numeric adjust-command IDs used by another body.
Packet builders therefore refuse an unverified command map before any bytes
that would be sent to a camera are returned.
"""
from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from tools.research.wbi_memory import decode_section, parse_sections

WBI_NEEDLES = (
    b"ILCE-6400A",
    b"Linux version 3.0.27_nl-rt106+",
    b"libJiritsu.so",
    b"libsencore.so",
    b"libusbcmd.so",
    b"usb_extcmd",
    b"/tmp/jiritsu/libJit.so",
    b"/tmp/jiritsu/ver.txt",
    b"JIRITSU_GET_FILE",
    b"JIRITSU_SET_FILE",
    b"JIRITSU_DEBUG",
    b"JIRITSU_START",
)
RAM_PREFIXES = ("/tmp/", "/tmp_bt/", "/tmp_network/")
EXCLUDED_STORAGE = {"/dev/nflasha23"}
STORAGE_PATH = re.compile(r"/dev/nflasha[0-9]+")
CHUNK_BYTES = 512 * 1024


class JiritsuUnverified(RuntimeError):
    """Raised when a camera packet needs a command ID this body has not proven."""


@dataclass(frozen=True)
class JiritsuCommandMap:
    """Numeric adjust commands. ``verified`` is reserved for an A6400A proof."""

    status: str
    block: int
    get_file: int
    set_path: int
    set_bytes: int
    source: str

    def require_verified(self) -> None:
        if self.status != "verified":
            raise JiritsuUnverified(
                "This command map cannot be sent to a camera "
                f"(status {self.status}: {self.source})."
            )


# Public ZV-E10 layout. Kept for a future diff against A6400A libJiritsu.so.
# status is deliberately not "verified".
ZV_E10_PUBLIC_COMMANDS = JiritsuCommandMap(
    status="unverified",
    block=0x1001,
    get_file=0x4008,
    set_path=15,
    set_bytes=16,
    source="ZV-E10 public notes, not an ILCE-6400A disassembly",
)

# Halfwords selected by the ILCE-6400A 1.01 dispatcher. status stays short of
# "verified" because no Senser packet was built or sent from this map.
A6400A_V101_LIBRARY_COMMANDS = JiritsuCommandMap(
    status="library",
    block=0x1001,
    get_file=8,
    set_path=15,
    set_bytes=16,
    source=(
        "ILCE-6400A 1.01 nflasha15 libJiritsu.so SHA-256 c3621c7ec3077160"
        "4b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff, dispatcher 0x7015"
    ),
)


def jiritsu_key() -> bytes:
    """AES-128 key named by the public Jiritsu file reader."""
    material = b"jiritsu team"
    return hashlib.md5(material + b"\0" * (16 - len(material))).digest()


def jiritsu_crypt(data: bytes) -> bytes:
    """AES-128-CTR with the documented 00..0f counter. Encrypt and decrypt match."""
    from Cryptodome.Cipher import AES

    return AES.new(jiritsu_key(), AES.MODE_CTR, nonce=b"", initial_value=bytes(range(16))).decrypt(data)


def get_file_payload(path: str, commands: JiritsuCommandMap) -> bytes:
    """Build a GET_FILE body. Refuses an unverified command map first."""
    commands.require_verified()
    if not path.startswith("/") or path.startswith("/..") or "\0" in path:
        raise ValueError("Jiritsu read path must be absolute")
    transport = ("/.." + path).encode("ascii")
    return bytes((1, 0, 1)) + transport


def ephemeral_runner(daemon_path: str) -> bytes:
    """Shell text that starts the debugger from RAM and does not touch flash."""
    if not daemon_path.startswith(RAM_PREFIXES) or ".." in daemon_path or "\n" in daemon_path:
        raise ValueError("Daemon path must stay on a RAM prefix")
    script = (
        "#!/bin/sh\n"
        "export A6400_EPHEMERAL_RAM=1\n"
        f"exec {daemon_path}\n"
    )
    if b"/setting" in script.encode() or b"ld.so.preload" in script.encode():
        raise ValueError("Ephemeral runner must not name internal-flash paths")
    return script.encode()


def readable_storage(path: str) -> str:
    """Allow a raw nflasha partition read and reject the excluded region."""
    if path in EXCLUDED_STORAGE or STORAGE_PATH.fullmatch(path) is None:
        raise ValueError(f"Storage path is not allowed: {path}")
    return path


def chunk_read_command(path: str, ram_dir: str, offset: int, count: int) -> str:
    """One read-only dd plus sha256sum into a RAM file."""
    readable_storage(path)
    if not ram_dir.startswith(RAM_PREFIXES) or ram_dir.endswith("/") or ".." in ram_dir:
        raise ValueError("Chunk directory must be a RAM path without a trailing slash")
    if offset < 0 or count <= 0 or offset % 4096 or count % 4096 or count > CHUNK_BYTES:
        raise ValueError("Chunk range must be a positive 4096-byte multiple up to 512 KiB")
    destination = ram_dir + "/chunk"
    skip = offset // 4096
    blocks = count // 4096
    return (
        f"busybox dd if={path} of={destination} bs=4096 skip={skip} count={blocks} "
        f"&& busybox sha256sum {destination}"
    )


def verify_chunk(data: bytes, camera_line: str, expected_count: int) -> str:
    """Require the camera sha256sum line to match the host bytes."""
    if len(data) != expected_count:
        raise ValueError("Chunk length does not match the requested count")
    parts = camera_line.split()
    if len(parts) < 2 or len(parts[0]) != 64:
        raise ValueError("Camera checksum line is not a sha256sum result")
    digest = hashlib.sha256(data).hexdigest()
    if parts[0] != digest:
        raise ValueError("Camera and host chunk hashes differ")
    return digest


def audit_wbi(image: Path) -> dict:
    """Count required strings in a WBI1 image. Does not write section files."""
    raw = image.read_bytes()
    header, sections = parse_sections(raw)
    counts = {needle.decode("ascii"): 0 for needle in WBI_NEEDLES}
    for section in sections:
        data, _blocks, _used = decode_section(raw, section)
        for needle in WBI_NEEDLES:
            counts[needle.decode("ascii")] += data.count(needle)
    missing = [name for name, count in counts.items() if count <= 0]
    return {
        "path": str(image),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "magic": header["magic"],
        "sections": header["sections"],
        "expanded_size": header["expanded_size"],
        "string_counts": counts,
        "missing": missing,
        "command_ids": "not recovered from this warm-boot image",
        "filesystem": "WBI1 warm-boot memory, not a mountable root filesystem",
    }


def parse_adjust_status(response: bytes) -> tuple[int, bytes]:
    """Split a little-endian status word from an adjust response body."""
    if len(response) < 4:
        raise ValueError("Adjust response is shorter than its status word")
    status = struct.unpack_from("<I", response)[0]
    return status, response[4:]
