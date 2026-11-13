"""Contract tests for the file-transfer client.

Offline: the camera's answers are replayed as fixtures taken from the shapes Sony's own client
defines (E0660). What these protect:

* the nesting. A listing answers ``result[0].content[]`` and each entry carries
  ``original[].url``; a client that read ``result[0]`` as the list would download nothing and
  report an empty card.
* the refusal. With the body in a shooting mode the service answers error 12 and the tool must
  say "switch the camera to send mode", not surface a bare code — measured in E0662.
* the path. A file name arrives from a FAT filesystem and may contain ``..``; a download that
  trusted it would write outside the chosen directory.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "research"))

from camera_transfer import (  # noqa: E402
    MAX_PAGE,
    SEND_MODE,
    content_items,
    is_send_mode,
    safe_join,
    total_count,
)

# The measured shape: a page object holding a content array, each entry with originals.
PAGE = [{
    "type": "contentList",
    "cnt": 3,
    "stIdx": 0,
    "uri": "storage:memoryCard1",
    "content": [
        {
            "content": {"kind": "still", "original": [{"fileName": "DSC00001.JPG", "url": "http://cam/a"}]},
            "original": [
                {"fileName": "DSC00001.JPG", "url": "http://cam/orig/DSC00001.JPG", "stillObject": "still"},
            ],
            "largeUrl": "http://cam/large/DSC00001.JPG",
            "smallUrl": "http://cam/small/DSC00001.JPG",
            "thumbnailUrl": "http://cam/thumb/DSC00001.JPG",
        },
        {
            "original": [{"fileName": "DSC00002.ARW", "url": "http://cam/orig/DSC00002.ARW"}],
            "largeUrl": "http://cam/large/DSC00002.JPG",
        },
    ],
}]


class SendModeTests(unittest.TestCase):
    def test_the_send_mode_string_is_sonys_own(self) -> None:
        self.assertEqual(SEND_MODE, "Send to Smartphone")

    def test_the_send_mode_is_recognised(self) -> None:
        self.assertTrue(is_send_mode("Send to Smartphone"))

    def test_a_shooting_mode_is_not_send_mode(self) -> None:
        # Measured on the body while it was serving remote control.
        self.assertFalse(is_send_mode("Control with Smartphone"))

    def test_an_absent_mode_is_not_send_mode(self) -> None:
        self.assertFalse(is_send_mode(None))
        self.assertFalse(is_send_mode(""))


class ContentItemTests(unittest.TestCase):
    def test_the_original_url_is_preferred_over_the_large_preview(self) -> None:
        items = content_items(PAGE)
        self.assertEqual(items[0]["url"], "http://cam/orig/DSC00001.JPG")

    def test_the_thumbnail_is_kept_for_a_grid_view(self) -> None:
        self.assertEqual(content_items(PAGE)[0]["thumbnail"], "http://cam/thumb/DSC00001.JPG")

    def test_each_entry_yields_one_file(self) -> None:
        self.assertEqual(len(content_items(PAGE)), 2)

    def test_an_entry_without_an_original_falls_back_to_the_large_url(self) -> None:
        page = [{"content": [{"largeUrl": "http://cam/large/x.JPG"}]}]
        items = content_items(page)
        self.assertEqual(items[0]["url"], "http://cam/large/x.JPG")
        self.assertEqual(items[0]["file_name"], "x.JPG")

    def test_an_entry_with_no_url_at_all_is_skipped(self) -> None:
        page = [{"content": [{"thumbnailUrl": "http://cam/t.jpg"}, {}]}]
        self.assertEqual(content_items(page), [])

    def test_an_unexpected_shape_yields_nothing_instead_of_raising(self) -> None:
        for odd in (None, [], [{}], ["not a dict"], [{"content": "not a list"}]):
            with self.subTest(shape=odd):
                self.assertEqual(content_items(odd), [])

    def test_the_page_count_is_read(self) -> None:
        self.assertEqual(total_count(PAGE), 3)

    def test_a_page_without_a_count_answers_none(self) -> None:
        self.assertIsNone(total_count([{}]))
        self.assertIsNone(total_count([]))


class PathTests(unittest.TestCase):
    def test_a_plain_name_lands_in_the_directory(self) -> None:
        self.assertEqual(safe_join(Path("/tmp/out"), "DSC00001.JPG"),
                         Path("/tmp/out/DSC00001.JPG"))

    def test_a_traversal_name_cannot_escape(self) -> None:
        # A camera name is untrusted input; only the last component is kept.
        self.assertEqual(safe_join(Path("/tmp/out"), "../../etc/passwd"),
                         Path("/tmp/out/passwd"))

    def test_a_windows_separator_is_treated_as_a_separator(self) -> None:
        self.assertEqual(safe_join(Path("/tmp/out"), r"..\..\evil.JPG"),
                         Path("/tmp/out/evil.JPG"))

    def test_a_subdirectory_in_the_name_is_flattened(self) -> None:
        self.assertEqual(safe_join(Path("/tmp/out"), "100MSDCF/DSC00001.JPG"),
                         Path("/tmp/out/DSC00001.JPG"))


class ServerTypeTests(unittest.TestCase):
    """The mode is read from the camera, so the refusal can name it instead of guessing."""

    def test_an_unreachable_camera_answers_none_rather_than_a_guess(self) -> None:
        from camera_transfer import server_type
        # Port 9 (discard) is closed; nothing listens, so this must be None, not "send mode".
        self.assertIsNone(server_type("127.0.0.1", port=9, timeout=1.0))


class PageLimitTests(unittest.TestCase):
    def test_the_page_limit_is_sonys_own_ceiling(self) -> None:
        # The shipped client's code refuses cnt > 24.
        self.assertEqual(MAX_PAGE, 24)


if __name__ == "__main__":
    unittest.main()
