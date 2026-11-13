"""Contract tests for the command-line remote's policy.

Offline: no camera is contacted. What these protect is the refusal rule, which is the whole
point of the tool on this body. The owner's ILCE-6400 advertises 47 methods and half-press but
not the shutter (E0659). A remote that sent ``actTakePicture`` regardless would return error 1
and a user would read that as a broken tool, so the remote must refuse *before* sending and
say why. If that check regressed, the tool would look broken in exactly the case it exists for.

Also protected: the MJPEG framer, because the stream has no length prefix and a framer that
returned a partial frame would write a truncated JPEG that looks like a corrupt photograph.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "research"))

from camera_remote import (  # noqa: E402
    COMMANDS,
    describe_capability,
    jpeg_frames,
    supported_commands,
    unsupported_commands,
)

# The 47 methods the owner's camera actually advertised, recorded in E0659.
LIVE_47 = [
    "getAvailableApiList", "getShootMode", "getSupportedShootMode", "getAvailableShootMode",
    "getSupportedFlashMode", "getSupportedSelfTimer", "getSupportedMovieQuality",
    "startLiveview", "stopLiveview", "getExposureMode", "getSupportedExposureMode",
    "getAvailableExposureMode", "setExposureCompensation", "getExposureCompensation",
    "getSupportedExposureCompensation", "getAvailableExposureCompensation", "setFNumber",
    "getFNumber", "getSupportedFNumber", "getAvailableFNumber", "setWhiteBalance",
    "getWhiteBalance", "getSupportedWhiteBalance", "getAvailableWhiteBalance",
    "getShutterSpeed", "getSupportedShutterSpeed", "getAvailableShutterSpeed",
    "setIsoSpeedRate", "getIsoSpeedRate", "getSupportedIsoSpeedRate",
    "getAvailableIsoSpeedRate", "actHalfPressShutter", "cancelHalfPressShutter",
    "getSupportedProgramShift", "startContShooting", "stopContShooting",
    "getSupportedMovieFileFormat", "setContShootingMode", "getContShootingMode",
    "getSupportedContShootingMode", "getAvailableContShootingMode",
    "getSupportedViewAngleMode", "getSupportedNearModeInPF",
    "getSupportedWirelessFlashSetting", "getApplicationInfo", "getEvent",
    "getTemporarilyUnavailableApiList",
]


class CapabilityTests(unittest.TestCase):
    def test_the_shutter_is_reported_as_unsupported_on_this_body(self) -> None:
        # This is the measured fact: the shutter is absent from the live list.
        self.assertIn("shoot", unsupported_commands(LIVE_47))

    def test_half_press_is_reported_as_supported(self) -> None:
        self.assertIn("half-press", supported_commands(LIVE_47))

    def test_the_two_lists_partition_the_commands(self) -> None:
        have = set(supported_commands(LIVE_47))
        lack = set(unsupported_commands(LIVE_47))
        self.assertEqual(have | lack, set(COMMANDS))
        self.assertEqual(have & lack, set())

    def test_an_empty_api_list_supports_nothing(self) -> None:
        self.assertEqual(supported_commands([]), [])

    def test_capability_text_states_the_count_and_the_refusal(self) -> None:
        text = describe_capability(LIVE_47)
        self.assertIn("47 methods", text)
        self.assertIn("shoot", text)
        self.assertIn("error 1", text)

    def test_capability_text_does_not_claim_a_shutter_when_one_is_absent(self) -> None:
        text = describe_capability(LIVE_47)
        available_line = next(line for line in text.splitlines() if line.startswith("available:"))
        self.assertNotIn("shoot,", available_line)
        self.assertFalse(available_line.rstrip().endswith("shoot"))


class JpegFramerTests(unittest.TestCase):
    def test_one_complete_frame_is_returned_whole(self) -> None:
        frame = b"\xff\xd8\xff\xe0payload\xff\xd9"
        self.assertEqual(jpeg_frames(frame), [frame])

    def test_two_frames_are_separated(self) -> None:
        a = b"\xff\xd8\xff\xe0one\xff\xd9"
        b = b"\xff\xd8\xff\xe0two\xff\xd9"
        self.assertEqual(jpeg_frames(a + b), [a, b])

    def test_leading_stream_headers_are_skipped(self) -> None:
        # The real stream begins with an HTTP-ish preamble before the first SOI.
        self.assertEqual(len(jpeg_frames(b"preamble\r\n" + b"\xff\xd8\xff\xe0x\xff\xd9")), 1)

    def test_an_unterminated_tail_is_dropped(self) -> None:
        # A partial frame must never be returned: a truncated JPEG reads as a corrupt photo.
        complete = b"\xff\xd8\xff\xe0ok\xff\xd9"
        self.assertEqual(jpeg_frames(complete + b"\xff\xd8\xff\xe0trunc"), [complete])

    def test_no_frames_in_a_buffer_without_markers(self) -> None:
        self.assertEqual(jpeg_frames(b"nothing here"), [])


if __name__ == "__main__":
    unittest.main()
