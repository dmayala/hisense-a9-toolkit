"""Pure offline fixture/argv tests; no subprocess, phone or viewer required."""
import hashlib
import json
import os
import pathlib
import shlex
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "eink-probe"))
import matched_source as M  # noqa: E402


def decode_fixture(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, []
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, payload = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        crc = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0]
        assert crc == zlib.crc32(kind + payload) & 0xffffffff
        chunks.append((kind, payload))
        pos += n + 12
    assert [k for k, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    assert struct.unpack(">IIBBBBB", chunks[0][1]) == (824, 1648, 8, 0, 0, 0, 0)
    rows = zlib.decompress(chunks[1][1])
    assert len(rows) == 825 * 1648
    assert all(rows[y * 825] == 0 for y in range(1648))
    return b"".join(rows[y * 825 + 1:(y + 1) * 825] for y in range(1648))


class Fixtures(unittest.TestCase):
    def test_white_full_canvas_and_no_metadata(self):
        data = M.source_png("white")
        pixels = decode_fixture(data)
        self.assertEqual(pixels, b"\xff" * (824 * 1648))
        self.assertEqual(M.source_png("white"), data)

    def test_dark_pattern_is_identical_and_has_orientation_landmarks(self):
        pixels = decode_fixture(M.source_png("dark"))
        at = lambda x, y: pixels[y * 824 + x]
        self.assertEqual(set(pixels), {0, 255})
        self.assertGreater(pixels.count(0), 0.8 * len(pixels))
        self.assertEqual([at(400, 0), at(400, 1647), at(0, 800)], [255] * 3)
        self.assertEqual([at(50, 50), at(700, 50), at(775, 120)], [255, 0, 255])
        self.assertEqual([at(80, 220), at(232, 220), at(232, 348)], [255, 0, 255])
        self.assertEqual(M.source_png("dark"), M.source_png("dark"))

    def test_write_metadata_identity_and_repeat(self):
        with tempfile.TemporaryDirectory() as d:
            for kind in M.SOURCE_IDS:
                meta = M.write_source(d, kind)
                data = pathlib.Path(meta["path"]).read_bytes()
                self.assertEqual(meta["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(meta["pixels_sha256"], hashlib.sha256(decode_fixture(data)).hexdigest())
                self.assertEqual(meta["black_pixels"] + meta["white_pixels"], 824 * 1648)
                self.assertEqual(M.write_source(d, kind), meta)
            self.assertNotEqual(M.source_metadata("dark")["sha256"], M.source_metadata("white")["sha256"])

    def test_invalid_id_and_existing_path_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            for invalid in ("", "../dark", "black", None):
                with self.assertRaises(ValueError):
                    M.write_source(d, invalid)
            p = pathlib.Path(d) / "ghost-source-v1-dark.png"
            p.write_text("keep this")
            with self.assertRaises(ValueError):
                M.write_source(d, "dark")
            self.assertEqual(p.read_text(), "keep this")
            p.unlink()
            target = pathlib.Path(d) / "keep.png"
            target.write_bytes(M.source_png("dark"))
            p.symlink_to(target)
            with self.assertRaises(ValueError):
                M.write_source(d, "dark")


class Viewer(unittest.TestCase):
    path = "/data/local/tmp/ghost-source-8d2a-dark.png"
    template = ["am", "start", "-W", "-n", "verified.viewer/.ImageActivity", "-d", "{uri}", "-t", "image/png"]

    def test_explicit_viewer_and_shell_transport(self):
        expected = self.template[:]
        expected[6] = "file://" + self.path
        self.assertEqual(M.validate_viewer_template(json.dumps(self.template)), self.template)
        self.assertEqual(M.viewer_argv(self.template, self.path), expected)
        args = M.source_args(self.template, self.path)
        self.assertEqual(args[0], "shell")
        self.assertEqual(len(args), 2)
        self.assertEqual(shlex.split(args[1]), expected)

    def test_path_and_uri_and_literal_tokens_stay_separate(self):
        template = ["verified-viewer", "--path={path}", "--uri={uri}", "spaces and 'quotes'", "$(do-not-execute)"]
        expanded = M.viewer_argv(template, self.path)
        self.assertEqual(expanded[1:3], ["--path=" + self.path, "--uri=file://" + self.path])
        self.assertEqual(shlex.split(M.source_args(template, self.path)[1]), expanded)

    def test_no_implicit_viewer_or_shell_template(self):
        invalid = [None, [], "am start -d {uri}", ["am", "start"], ["am", "{other}"],
                   ["am", "{{path}}"], ["am", "{path"], ["am", "{path}", "\n"],
                   ["am", "{path}", 3], ["{path}", "{path}"], ["sh", "-c", "viewer {path}"],
                   ["/system/bin/sh", "-c", "viewer {path}"], ["su", "-c", "viewer {path}"]]
        for template in invalid:
            with self.subTest(template=template), self.assertRaises(ValueError):
                M.validate_viewer_template(template)

    def test_device_path_is_exact_temporary_file(self):
        self.assertEqual(M.validate_device_path(self.path), self.path)
        for path in ("/data/local/tmp/../file.png", "/sdcard/file.png", "/data/local/tmp/file;id.png",
                     "/data/local/tmp/*.png", "/data/local/tmp/dir/file.png", "/data/local/tmp/file.jpg",
                     "/data/local/tmp/.file.png", "/data/local/tmp/xx.png\n", None):
            with self.subTest(path=path), self.assertRaises(ValueError):
                M.source_args(self.template, path)


if __name__ == "__main__":
    unittest.main()
