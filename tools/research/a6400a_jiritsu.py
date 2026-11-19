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
STORAGE_PATH = re.compile(r"/dev/nflasha(?:B[01]|[0-9]+)")
CHUNK_BYTES = 512 * 1024
A6400A_DUMP_PARTITIONS: dict[str, int] = {
    "/dev/nflashaB0": 4_194_304,
    "/dev/nflashaB1": 4_194_304,
    "/dev/nflasha1": 8_323_072,
    "/dev/nflasha3": 50_331_648,
    "/dev/nflasha4": 12_582_912,
    "/dev/nflasha5": 62_914_560,
    "/dev/nflasha7": 8_388_608,
    "/dev/nflasha15": 314_572_800,
}


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


def _validated_ram_dir(ram_dir: str) -> str:
    """Validate a RAM directory path without trailing slash, traversal, or newlines."""
    if (
        not ram_dir.startswith(RAM_PREFIXES)
        or ram_dir.endswith("/")
        or ".." in ram_dir
        or "\n" in ram_dir
        or "\r" in ram_dir
        or "/setting" in ram_dir
    ):
        raise ValueError("Chunk directory must be a RAM path without a trailing slash")
    return ram_dir


def readable_storage(path: str) -> str:
    """Allow a raw nflasha partition read and reject the excluded region."""
    if path in EXCLUDED_STORAGE or STORAGE_PATH.fullmatch(path) is None:
        raise ValueError(f"Storage path is not allowed: {path}")
    return path


def chunk_cleanup_command(ram_dir: str) -> str:
    """Remove any staged chunk file in RAM so tmpfs holds at most one chunk."""
    _validated_ram_dir(ram_dir)
    return f"busybox rm -f {ram_dir}/chunk"


def chunk_read_command(path: str, ram_dir: str, offset: int, count: int) -> str:
    """One read-only dd plus sha256sum into a single reused RAM chunk file."""
    readable_storage(path)
    _validated_ram_dir(ram_dir)
    if offset < 0 or count <= 0 or offset % 4096 or count % 4096 or count > CHUNK_BYTES:
        raise ValueError("Chunk range must be a positive 4096-byte multiple up to 512 KiB")
    destination = ram_dir + "/chunk"
    skip = offset // 4096
    blocks = count // 4096
    return (
        f"busybox rm -f {destination} "
        f"&& busybox dd if={path} of={destination} bs=4096 skip={skip} count={blocks} "
        f"&& busybox sha256sum {destination}"
    )


def plan_partition_chunk_dump(
    path: str,
    total_bytes: int | None = None,
    ram_dir: str = "/tmp_network/a6400a-dump",
    chunk_bytes: int = CHUNK_BYTES,
) -> dict:
    """Plan a bounded single-slot RAM-chunk read sequence for an allowlisted partition."""
    readable_storage(path)
    cleanup_cmd = chunk_cleanup_command(ram_dir)
    if total_bytes is None:
        if path not in A6400A_DUMP_PARTITIONS:
            raise ValueError(f"Partition capacity is not configured for {path}")
        total_bytes = A6400A_DUMP_PARTITIONS[path]
    if total_bytes <= 0 or total_bytes % 4096:
        raise ValueError("Partition total_bytes must be a positive 4096-byte multiple")
    if chunk_bytes <= 0 or chunk_bytes > CHUNK_BYTES or chunk_bytes % 4096:
        raise ValueError("chunk_bytes must be a positive 4096-byte multiple up to 512 KiB")

    ram_chunk_path = ram_dir + "/chunk"
    chunks = []
    for index, offset in enumerate(range(0, total_bytes, chunk_bytes)):
        count = min(chunk_bytes, total_bytes - offset)
        chunks.append(
            {
                "index": index,
                "offset": offset,
                "count": count,
                "ram_chunk_path": ram_chunk_path,
                "camera_command": chunk_read_command(path, ram_dir, offset, count),
                "cleanup_command": cleanup_cmd,
            }
        )

    return {
        "partition": path,
        "total_bytes": total_bytes,
        "chunk_bytes": chunk_bytes,
        "chunk_count": len(chunks),
        "max_ram_bytes": min(total_bytes, chunk_bytes),
        "ram_dir": ram_dir,
        "ram_chunk_path": ram_chunk_path,
        "dlopen_constraint": (
            "adj_cmd_set_file2 at 0x5d08 dlopens /tmp/jiritsu/libJit.so synchronously "
            "on im.elf; never stage full partitions in tmpfs or re-upload libJit.so "
            "in the same power cycle"
        ),
        "chunks": chunks,
    }


def ephemeral_chunk_worker_script(ram_dir: str = "/tmp_network/a6400a-dump") -> bytes:
    """Return a RAM-only polling worker script for /tmp/jiritsu/run.sh."""
    _validated_ram_dir(ram_dir)
    script = (
        "#!/bin/sh\n"
        "busybox grep ' /tmp_network tmpfs ' /proc/mounts >/dev/null || exit 31\n"
        f"busybox mkdir -p '{ram_dir}' || exit 32\n"
        f"busybox cat /proc/$$/status >'{ram_dir}/ready'\n"
        "while :; do\n"
        f" if [ -f '{ram_dir}/input' ] && [ -f '{ram_dir}/go' ]; then\n"
        f"  busybox mv '{ram_dir}/input' '{ram_dir}/current' || exit 33\n"
        f"  . '{ram_dir}/current' >'{ram_dir}/output.tmp' 2>&1\n"
        "  rc=$?\n"
        f"  printf '\\n__A6400A_RC__:%s\\n' \"$rc\" >>'{ram_dir}/output.tmp'\n"
        f"  busybox mv '{ram_dir}/output.tmp' '{ram_dir}/output'\n"
        f"  busybox rm -f '{ram_dir}/current' '{ram_dir}/go'\n"
        " else\n"
        "  busybox sleep 1\n"
        " fi\n"
        "done\n"
    )
    encoded = script.encode("ascii")
    if b"/setting" in encoded or b"ld.so.preload" in encoded or b"/dev/nflasha23" in encoded:
        raise ValueError("Ephemeral worker must not reference flash or excluded storage")
    return encoded


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
