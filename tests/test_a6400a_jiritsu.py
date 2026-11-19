"""Offline ILCE-6400A Jiritsu guards. No camera and no USB."""
import json
from pathlib import Path
import shutil
import unittest

from src.a6400.targets.usb_debugger_compat import get_debugger_target
from tools.deploy_native_usb_debugger import deploy
from tools.research.a6400a_jiritsu import (
    A6400A_V101_LIBRARY_COMMANDS,
    CHUNK_BYTES,
    JiritsuUnverified,
    ZV_E10_PUBLIC_COMMANDS,
    chunk_read_command,
    ephemeral_chunk_worker_script,
    ephemeral_runner,
    get_file_payload,
    jiritsu_crypt,
    jiritsu_key,
    parse_adjust_status,
    plan_partition_chunk_dump,
    readable_storage,
    verify_chunk,
)
from tools.research.a6400a_jiritsu_bootstrap import camera_entry_closed, main as bootstrap_main

ROOT = Path(__file__).resolve().parents[1]
WBI = ROOT / "artifacts/dumps/a6400a/nflasha5.bin"


class JiritsuGuardTests(unittest.TestCase):
    def test_public_command_map_cannot_build_a_camera_packet(self):
        with self.assertRaises(JiritsuUnverified):
            get_file_payload("/version.txt", ZV_E10_PUBLIC_COMMANDS)

    def test_packaged_library_ids_still_cannot_build_a_camera_packet(self):
        self.assertEqual(A6400A_V101_LIBRARY_COMMANDS.get_file, 8)
        self.assertEqual(A6400A_V101_LIBRARY_COMMANDS.set_path, 15)
        self.assertEqual(A6400A_V101_LIBRARY_COMMANDS.set_bytes, 16)
        self.assertNotEqual(A6400A_V101_LIBRARY_COMMANDS.get_file, ZV_E10_PUBLIC_COMMANDS.get_file)
        with self.assertRaises(JiritsuUnverified):
            get_file_payload("/version.txt", A6400A_V101_LIBRARY_COMMANDS)

    def test_camera_modes_stop_before_usb(self):
        self.assertEqual(camera_entry_closed("check"), 2)
        self.assertEqual(camera_entry_closed("jiritsu-dump"), 2)

    def test_crypt_round_trip_uses_the_documented_key_length(self):
        self.assertEqual(len(jiritsu_key()), 16)
        try:
            import Cryptodome.Cipher.AES  # noqa: F401
        except ImportError:
            self.skipTest("Cryptodome is not installed")
        payload = b"ILCE-6400A jiritsu fixture"
        self.assertEqual(jiritsu_crypt(jiritsu_crypt(payload)), payload)

    def test_adjust_status_is_a_leading_word(self):
        status, body = parse_adjust_status(b"\x06\x00\x00\x10rest")
        self.assertEqual(status, 0x10000006)
        self.assertEqual(body, b"rest")

    def test_ephemeral_runner_stays_on_ram(self):
        script = ephemeral_runner("/tmp_bt/a6400_usb_debugd.elf")
        self.assertIn(b"A6400_EPHEMERAL_RAM=1", script)
        self.assertNotIn(b"/setting", script)
        self.assertNotIn(b"ld.so.preload", script)
        with self.assertRaises(ValueError):
            ephemeral_runner("/setting/a6400_usb_debugd.elf")

    def test_storage_allowlist_rejects_nflasha23_and_other_devices(self):
        self.assertEqual(readable_storage("/dev/nflasha5"), "/dev/nflasha5")
        with self.assertRaises(ValueError):
            readable_storage("/dev/nflasha23")
        with self.assertRaises(ValueError):
            readable_storage("/dev/mmca1")

    def test_chunk_command_is_read_only_and_bounded(self):
        command = chunk_read_command("/dev/nflasha5", "/tmp_bt/a6400a-chunk", 4096, 8192)
        self.assertIn("busybox dd if=/dev/nflasha5", command)
        self.assertIn("sha256sum", command)
        self.assertNotIn(" of=/dev/", command)
        with self.assertRaises(ValueError):
            chunk_read_command("/dev/nflasha23", "/tmp_bt/a6400a-chunk", 0, 4096)
        with self.assertRaises(ValueError):
            chunk_read_command("/dev/nflasha5", "/tmp_bt/a6400a-chunk", 1, 4096)

    def test_chunk_hash_must_match(self):
        data = b"a" * 4096
        digest = verify_chunk(data, __import__("hashlib").sha256(data).hexdigest() + "  chunk\n", 4096)
        self.assertEqual(len(digest), 64)
        with self.assertRaises(ValueError):
            verify_chunk(data, "0" * 64 + "  chunk\n", 4096)

    def test_a6400a_registry_is_not_a_senser_installer_target(self):
        spec = get_debugger_target("ilce_6400a")
        self.assertEqual(spec.bootstrap_mode, "jiritsu_ram_only")
        self.assertEqual(spec.qualification, "reference")
        with self.assertRaises(RuntimeError):
            deploy(object(), spec)

    def test_boot_area_storage_nodes_are_allowed(self):
        self.assertEqual(readable_storage("/dev/nflashaB0"), "/dev/nflashaB0")
        self.assertEqual(readable_storage("/dev/nflashaB1"), "/dev/nflashaB1")
        cmd = chunk_read_command("/dev/nflashaB0", "/tmp_network/a6400a-dump", 0, CHUNK_BYTES)
        self.assertIn("busybox rm -f /tmp_network/a6400a-dump/chunk", cmd)
        self.assertIn("busybox dd if=/dev/nflashaB0", cmd)

    def test_plan_partition_chunk_dump_reuses_single_ram_slot(self):
        plan = plan_partition_chunk_dump("/dev/nflashaB0")
        self.assertEqual(plan["partition"], "/dev/nflashaB0")
        self.assertEqual(plan["total_bytes"], 4_194_304)
        self.assertEqual(plan["chunk_bytes"], 524_288)
        self.assertEqual(plan["chunk_count"], 8)
        self.assertEqual(plan["max_ram_bytes"], 524_288)
        self.assertEqual(plan["ram_chunk_path"], "/tmp_network/a6400a-dump/chunk")
        self.assertIn("dlopen", plan["dlopen_constraint"])
        for idx, chunk in enumerate(plan["chunks"]):
            self.assertEqual(chunk["index"], idx)
            self.assertEqual(chunk["offset"], idx * 524_288)
            self.assertEqual(chunk["ram_chunk_path"], "/tmp_network/a6400a-dump/chunk")
            self.assertTrue(chunk["camera_command"].startswith("busybox rm -f /tmp_network/a6400a-dump/chunk && "))
            self.assertEqual(chunk["cleanup_command"], "busybox rm -f /tmp_network/a6400a-dump/chunk")
        with self.assertRaises(ValueError):
            plan_partition_chunk_dump("/dev/nflashaB0", chunk_bytes=CHUNK_BYTES + 4096)
        with self.assertRaises(ValueError):
            plan_partition_chunk_dump("/dev/nflashaB0", total_bytes=4097)
        with self.assertRaises(ValueError):
            plan_partition_chunk_dump("/dev/nflasha23")

    def test_ephemeral_chunk_worker_script_is_ram_only(self):
        script = ephemeral_chunk_worker_script("/tmp_network/a6400a-dump")
        self.assertIn(b"/tmp_network tmpfs", script)
        self.assertIn(b"/tmp_network/a6400a-dump/ready", script)
        self.assertNotIn(b"/setting", script)
        self.assertNotIn(b"ld.so.preload", script)
        with self.assertRaises(ValueError):
            ephemeral_chunk_worker_script("/setting/dump")

    def test_bootstrap_plan_chunk_dump_mode_writes_artifact(self):
        out_dir = ROOT / "artifacts/tests/test-a6400a-jiritsu-chunk-plan"
        report_path = out_dir / "chunk-plan.json"
        try:
            rc = bootstrap_main(
                [
                    "--mode",
                    "plan-chunk-dump",
                    "--partition",
                    "/dev/nflashaB0",
                    "--report",
                    str(report_path),
                ]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["chunk_count"], 8)
            self.assertEqual(payload["max_ram_bytes"], CHUNK_BYTES)
            self.assertIn("/tmp_network/a6400a-dump/ready", payload["worker_script"])
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    @unittest.skipUnless(WBI.is_file(), "local ILCE-6400A nflasha5 image is absent")
    def test_local_wbi_contains_the_jiritsu_service_strings(self):
        from tools.research.a6400a_jiritsu import audit_wbi

        report = audit_wbi(WBI)
        self.assertEqual(report["magic"], "WBI1")
        self.assertEqual(report["missing"], [])
        self.assertGreater(report["string_counts"]["JIRITSU_GET_FILE"], 0)
        self.assertGreater(report["string_counts"]["/tmp/jiritsu/libJit.so"], 0)


if __name__ == "__main__":
    unittest.main()
