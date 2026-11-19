"""Offline ILCE-6400A Jiritsu audit, chunk planner, and closed camera-entry points.

``audit-wbi`` reads a local WBI1 image and writes a string-count receipt.
``plan-chunk-dump`` builds a single-slot 512 KiB RAM-chunk plan offline.
``check``, ``start-ram-debugger`` and ``jiritsu-dump`` stop before USB I/O.
E0554 names the packaged library's command halfwords, and that map is still
not a verified Senser send.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research.a6400a_jiritsu import (  # noqa: E402
    CHUNK_BYTES,
    JiritsuUnverified,
    ZV_E10_PUBLIC_COMMANDS,
    audit_wbi,
    ephemeral_chunk_worker_script,
    plan_partition_chunk_dump,
)

DEFAULT_IMAGE = ROOT / "artifacts/dumps/a6400a/nflasha5.bin"


def camera_entry_closed(mode: str) -> int:
    """Refuse every mode that would talk to a camera."""
    try:
        ZV_E10_PUBLIC_COMMANDS.require_verified()
    except JiritsuUnverified as exc:
        print(f"{mode}: {exc}")
        print("No USB device was opened and no camera command was sent.")
        return 2
    return 0


def _write_artifacts_report(destination: Path, report: dict) -> None:
    if not destination.resolve().is_relative_to(ROOT / "artifacts"):
        raise ValueError("Report path must stay under artifacts")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        required=True,
        choices=(
            "audit-wbi",
            "plan-chunk-dump",
            "check",
            "start-ram-debugger",
            "jiritsu-dump",
        ),
    )
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--partition", default="/dev/nflashaB0")
    parser.add_argument("--ram-dir", default="/tmp_network/a6400a-dump")
    parser.add_argument("--chunk-bytes", type=int, default=CHUNK_BYTES)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "plan-chunk-dump":
        try:
            plan = plan_partition_chunk_dump(
                args.partition,
                ram_dir=args.ram_dir,
                chunk_bytes=args.chunk_bytes,
            )
            worker_script = ephemeral_chunk_worker_script(args.ram_dir).decode("ascii")
        except ValueError as exc:
            print(str(exc))
            return 2
        report = {**plan, "worker_script": worker_script}
        destination = (
            args.report
            if args.report is not None
            else ROOT / "artifacts/analysis/a6400a-wbi-jiritsu/chunk-plan.json"
        )
        try:
            _write_artifacts_report(destination, report)
        except ValueError as exc:
            print(str(exc))
            return 2
        print(destination)
        return 0
    if args.mode != "audit-wbi":
        return camera_entry_closed(args.mode)
    if not args.image.is_file():
        print(f"WBI image not found: {args.image}")
        return 2
    report = audit_wbi(args.image)
    destination = args.report
    if destination is None:
        destination = ROOT / "artifacts/analysis/a6400a-wbi-jiritsu/audit.json"
    try:
        _write_artifacts_report(destination, report)
    except ValueError as exc:
        print(str(exc))
        return 2
    print(destination)
    if report["missing"]:
        print("missing: " + ", ".join(report["missing"]))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
