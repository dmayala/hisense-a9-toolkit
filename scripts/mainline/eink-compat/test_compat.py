#!/usr/bin/env python3
"""Host-only socket integration tests. All writes stay in TemporaryDirectory."""
import json
import os
from pathlib import Path
import socket
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

BINARY = Path(sys.argv.pop(1)).resolve()
LEDS = ["a9:warm:center", "a9:white:center", "a9:warm:edge", "a9:white:edge"]
ATTRIBUTES = ("mode", "black_threshold", "white_threshold", "contrast", "force_clear", "manual_clear", "clear_shadow")


class CompatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sol-einkcompat-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.props = self.base / "properties"
        self.props.mkdir()
        self.tools = self.base / "tools"
        self.tools.mkdir()
        self.epd = self.make_group("eink-display", 2, "DSI-1", "a9-eink")
        # A fully populated decoy group on card0 must never receive writes.
        self.vkms = self.make_group("vkms", 0, "Virtual-1", "vkms")
        for led in LEDS:
            self.led(led).parent.mkdir(parents=True, exist_ok=True)
            self.led(led).write_text("initial\n")
        self.prop("ro.build.version.sdk", "36")
        # Fixed host fixtures only. Shell builtins avoid Python-import startup
        # consuming the daemon's real 550 ms tool timeout on a busy build host.
        helper = f"""#!/bin/sh
base={shlex.quote(str(self.base))}
printf '%s\\000' "${{0##*/}}" "$@" >> "$base/calls"
printf '\\n' >> "$base/calls"
mode=ok
[ ! -f "$base/reply" ] || IFS= read -r mode < "$base/reply"
case "$mode" in
  slow) sleep 4 ;;
  exit) exit 1 ;;
esac
case "$mode" in
  exception) printf 'Result: Parcel( ffffffff 00000001 )\\n' ;;
  false) printf 'Result: Parcel( 00000000 00000000 )\\n' ;;
  *) printf 'Result: Parcel( 00000000 00000001 )\\n' ;;
esac
"""
        for name in ("service", "settings"):
            path = self.tools / name
            path.write_text(helper)
            path.chmod(0o700)
        self.address = "\0sol_einkcompat_" + self.base.name
        self.log = (self.base / "stderr").open("w+")
        self.addCleanup(self.log.close)
        self.launch()
        self.addCleanup(self.stop)

    def make_group(self, device, number, connector, driver=None, aliases=True):
        platform = self.base / "sys/devices/platform" / device
        card = platform / "drm" / f"card{number}"
        connected = card / f"card{number}-{connector}"
        group = connected / "eink"
        group.mkdir(parents=True)
        for name in ATTRIBUTES:
            (group / name).write_text("initial\n")
        if driver and not (platform / "driver").exists():
            owner = self.base / "sys/bus/platform/drivers" / driver
            owner.mkdir(parents=True, exist_ok=True)
            (platform / "driver").symlink_to(owner, target_is_directory=True)
        if aliases:
            directory = self.base / "sys/class/drm"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / card.name).symlink_to(card, target_is_directory=True)
            (directory / connected.name).symlink_to(connected, target_is_directory=True)
        return group

    def launch(self, extra=()):
        self.process = subprocess.Popen(
            [str(BINARY), "--root", str(self.base), "--props", str(self.props),
             "--tools", str(self.tools), "--socket", self.address[1:], *extra],
            stdout=subprocess.DEVNULL, stderr=self.log,
        )
        self.client = self.connect()
        self.addCleanup(self.client.close)

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=3)

    def connect(self):
        for _ in range(100):
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(2)
            try:
                connection.connect(self.address)
                return connection
            except (ConnectionRefusedError, FileNotFoundError):
                connection.close()
                if self.process.poll() is not None:
                    self.fail("daemon exited at startup")
                time.sleep(0.01)
        self.fail("daemon did not start")

    def node(self, name):
        return self.epd / name

    def led(self, name):
        return self.base / "sys/class/leds" / name / "brightness"

    def prop(self, name, value):
        # Replace atomically so the fixture does not expose partially written props.
        staging = self.props / "new"
        staging.write_text(value)
        staging.replace(self.props / name)

    # Deferred writes retry once a second. Leave room for a retry racing late
    # fixture creation and host scheduling while QEMU/kernel builds are active.
    def wait_text(self, path, expected, attempts=600):
        for _ in range(attempts):
            if path.exists() and path.read_text() == expected:
                return
            time.sleep(0.01)
        self.assertEqual(path.read_text(), expected)

    def send(self, value):
        self.client.sendall(value.encode())

    def barrier(self):
        # Invalid sct replies synchronously without invoking a child tool.
        self.send("sct0\n")
        self.assertEqual(self.client.recv(3), b"NO\n")

    def calls(self):
        path = self.base / "calls"
        return [[arg.decode() for arg in line.split(b"\0")[:-1]]
                for line in path.read_bytes().splitlines()] if path.exists() else []

    def test_four_modes_and_clear(self):
        for command, value in [("b", 513), ("c", 515), ("s", 518), ("p", 521)]:
            self.send(command + "\n")
            self.wait_text(self.node("mode"), f"{value}\n")
        self.send("r\n")
        self.wait_text(self.node("manual_clear"), "1\n")
        self.assertEqual(self.node("mode").read_text(), "521\n", "clear must keep selected steady mode")
        self.assertEqual(self.node("force_clear").read_text(), "initial\n", "r must never write the recovery attribute")

    def test_diagnostics_default_off(self):
        self.send("c\n")
        self.barrier()
        self.assertNotIn("a9-eink-diag", (self.base / "stderr").read_text())

    def test_diagnostics_budget_and_numeric_records(self):
        self.stop()
        self.launch(("--diagnostics", "8"))
        self.send("c\ns\nb\np\nc\ns\nb\np\nc\ns\n")
        self.barrier()
        records = [s for s in (self.base / "stderr").read_text().splitlines()
                   if s.startswith("a9-eink-diag ")]
        self.assertEqual(len(records), 8)
        for record in records:
            self.assertRegex(record, r"^a9-eink-diag ms=\d+ event=(mode|lamp) seq=\d+ value=\d+ accepted=1$")

    def test_diagnostics_deferred_then_accepted(self):
        self.stop()
        self.launch(("--diagnostics", "32"))
        saved = self.epd.parent / "not-eink"
        self.epd.rename(saved)
        self.send("c\n")
        self.barrier()
        text = (self.base / "stderr").read_text()
        self.assertIn("event=mode seq=1 value=515 accepted=0", text)
        self.assertNotIn("event=mode seq=1 value=515 accepted=1", text)
        saved.rename(self.epd)
        self.wait_text(self.node("mode"), "515\n", attempts=600)
        # write() completion precedes diagnostic output by a few instructions.
        time.sleep(0.02)
        text = (self.base / "stderr").read_text()
        self.assertEqual(text.count("event=mode seq=1 value=515 accepted=1"), 1)

    def test_diagnostics_lamps_only_on_successful_changes(self):
        self.stop()
        self.launch(("--diagnostics", "32"))
        self.prop("sys.linevibrator_open", "100")
        self.wait_text(self.led(LEDS[0]), "81\n")
        self.wait_text(self.led(LEDS[2]), "78\n")
        self.prop("unrelated.fixture", "ignored")
        time.sleep(0.12)
        records = [s for s in (self.base / "stderr").read_text().splitlines()
                   if s.startswith("a9-eink-diag ")]
        self.assertEqual(len(records), 2)
        self.assertTrue(any("event=lamp seq=0 value=81 accepted=1" in s for s in records))
        self.assertTrue(any("event=lamp seq=2 value=78 accepted=1" in s for s in records))
        # A second identical clear must issue a second store.
        self.node("manual_clear").write_text("between\n")
        self.send("r\n")
        self.wait_text(self.node("manual_clear"), "1\n")

    def test_manual_clear_missing_attribute_has_no_fallback_or_replay(self):
        self.node("manual_clear").unlink()
        self.send("r\n")
        self.barrier()  # Failed write-only r must not pollute the reply stream.
        self.assertFalse(self.node("manual_clear").exists(), "never create sysfs nodes")
        self.assertEqual(self.node("force_clear").read_text(), "initial\n")
        self.node("manual_clear").write_text("late\n")
        time.sleep(1.15)
        self.assertEqual(self.node("manual_clear").read_text(), "late\n", "failed events must not replay")
        self.assertEqual(self.node("force_clear").read_text(), "initial\n")
        self.send("r\n")
        self.wait_text(self.node("manual_clear"), "1\n")

    def test_discovery_requires_manual_clear_before_applying_controls(self):
        self.node("manual_clear").unlink()
        self.send("s\n")
        self.barrier()
        self.assertEqual(self.node("mode").read_text(), "initial\n")
        self.node("manual_clear").write_text("late\n")
        self.wait_text(self.node("mode"), "518\n")
        self.assertEqual(self.node("manual_clear").read_text(), "late\n")
        self.assertEqual(self.node("force_clear").read_text(), "initial\n")

    def test_failed_manual_clear_store_is_not_retried(self):
        if os.geteuid() == 0:
            self.skipTest("root bypasses ordinary file write permissions")
        self.node("manual_clear").chmod(0o400)
        self.send("r\n")
        self.barrier()
        self.assertEqual(self.node("manual_clear").read_text(), "initial\n")
        self.assertEqual(self.node("force_clear").read_text(), "initial\n")
        self.node("manual_clear").chmod(0o600)
        time.sleep(1.15)
        self.assertEqual(self.node("manual_clear").read_text(), "initial\n")
        self.send("r\n")
        self.wait_text(self.node("manual_clear"), "1\n")
        self.assertEqual(self.node("force_clear").read_text(), "initial\n")

    def test_successful_manual_clear_is_not_periodically_reasserted(self):
        self.send("r\n")
        self.wait_text(self.node("manual_clear"), "1\n")
        self.node("manual_clear").write_text("completed\n")
        time.sleep(1.15)
        self.assertEqual(self.node("manual_clear").read_text(), "completed\n")
        self.assertEqual(self.node("force_clear").read_text(), "initial\n")

    def test_startup_preserves_kernel_defaults_without_control_requests(self):
        time.sleep(0.12)
        for name in ATTRIBUTES:
            self.assertEqual(self.node(name).read_text(), "initial\n")

    def test_default_mode_property(self):
        for value, expected in [("c", "515\n"), ("513", "513\n"), ("p", "521\n")]:
            self.stop(); self.client.close()
            self.node("mode").write_text("initial\n")
            self.prop("ro.vendor.a9ml.eink.default_mode", value)
            self.launch()
            self.wait_text(self.node("mode"), expected)
            self.assertEqual(self.node("contrast").read_text(), "initial\n")
            self.assertEqual(self.node("manual_clear").read_text(), "initial\n")
        # a later app request wins; a bad value writes nothing
        self.send("s\n"); self.wait_text(self.node("mode"), "518\n")
        self.stop(); self.client.close()
        self.node("mode").write_text("initial\n")
        self.prop("ro.vendor.a9ml.eink.default_mode", "999")
        self.launch(); time.sleep(0.12)
        self.assertEqual(self.node("mode").read_text(), "initial\n")

    def test_lock_mode_ignores_app_mode_requests(self):
        self.stop(); self.client.close()
        self.node("mode").write_text("initial\n")
        self.prop("ro.vendor.a9ml.eink.default_mode", "c")
        self.prop("ro.vendor.a9ml.eink.lock_mode", "1")
        self.launch()
        self.wait_text(self.node("mode"), "515\n")
        self.send("s\n"); self.send("b\n"); self.barrier()
        time.sleep(0.1)
        self.assertEqual(self.node("mode").read_text(), "515\n")
        self.send("r\n")
        self.wait_text(self.node("manual_clear"), "1\n")

    def test_pending_mode_survives_requesting_client_disconnect(self):
        shutil.rmtree(self.base / "sys/devices/platform/eink-display/drm")
        with self.connect() as other:
            other.sendall(b"s\nsct0\n")
            self.assertEqual(other.recv(3), b"NO\n")
        self.epd = self.make_group("eink-display", 18, "DSI-1")
        self.wait_text(self.node("mode"), "518\n")

    def test_mode_and_controls_retry_latest_request_after_late_probe(self):
        shutil.rmtree(self.base / "sys/devices/platform/eink-display/drm")
        self.send("c\nstb30\nstw30\nsco0\np\nstb0\nstw100\nsco42\n")
        self.send("stb101\nstw-1\nsco9999\nr\n")
        self.barrier()
        self.epd = self.make_group("eink-display", 17, "DSI-4")
        for name, value in [("mode", 521), ("black_threshold", 0),
                            ("white_threshold", 100), ("contrast", 42)]:
            self.wait_text(self.node(name), f"{value}\n")
        self.assertEqual(self.node("manual_clear").read_text(), "initial\n",
                         "failed clear events must not replay on later images")
        self.assertEqual((self.vkms / "mode").read_text(), "initial\n")

    def test_controls_retry_failed_attribute_write(self):
        if os.geteuid() == 0:
            self.skipTest("root bypasses ordinary file write permissions")
        self.node("contrast").chmod(0o400)
        self.send("sco37\n")
        self.barrier()
        self.assertEqual(self.node("contrast").read_text(), "initial\n")
        self.node("contrast").chmod(0o600)
        self.wait_text(self.node("contrast"), "37\n")

    def test_refresh_controls_are_independent_of_linevibrator_properties(self):
        self.send("p\nstb19\nstw81\nsco42\n")
        self.barrier()
        self.prop("sys.linevibrator_type", "96")
        self.prop("sys.linevibrator_on", "2")
        self.prop("sys.linevibrator_touch", "2000")
        self.prop("sys.linevibrator_short", "1000")
        self.wait_text(self.led("a9:white:center"), "750\n")
        for name, value in [("mode", 521), ("black_threshold", 19),
                            ("white_threshold", 81), ("contrast", 42)]:
            self.assertEqual(self.node(name).read_text(), f"{value}\n")
        self.assertEqual(self.node("manual_clear").read_text(), "initial\n")

    def test_successful_controls_are_not_periodically_reasserted(self):
        self.send("s\n")
        self.wait_text(self.node("mode"), "518\n")
        stamp = self.node("mode").stat().st_mtime_ns
        time.sleep(1.15)
        self.assertEqual(self.node("mode").stat().st_mtime_ns, stamp)
        self.node("mode").write_text("515\n")  # An explicit coordinator override.
        self.prop("sys.linevibrator_type", "97")
        time.sleep(0.12)
        self.assertEqual(self.node("mode").read_text(), "515\n")
        self.send("b\n")
        self.wait_text(self.node("mode"), "513\n")

    def test_platform_path_without_class_aliases(self):
        shutil.rmtree(self.base / "sys/devices/platform/eink-display/drm")
        self.epd = self.make_group("eink-display", 12, "DSI-3", aliases=False)
        self.send("c\n")
        self.wait_text(self.node("mode"), "515\n")
        self.assertEqual((self.vkms / "mode").read_text(), "initial\n")

    def test_class_fallback_accepts_a9_underscore_driver(self):
        shutil.rmtree(self.base / "sys/devices/platform/eink-display/drm")
        self.epd = self.make_group("renamed-device", 7, "DSI-2", "a9_eink")
        self.send("p\nstb42\nr\n")
        self.barrier()
        self.assertEqual(self.node("mode").read_text(), "521\n")
        self.assertEqual(self.node("black_threshold").read_text(), "42\n")
        self.assertEqual(self.node("manual_clear").read_text(), "1\n")
        self.assertEqual((self.vkms / "mode").read_text(), "initial\n")

    def test_class_fallback_accepts_card_device_driver_symlink(self):
        shutil.rmtree(self.base / "sys/devices/platform/eink-display/drm")
        self.epd = self.make_group("renamed-device", 8, "DSI-1")
        owner = self.base / "sys/bus/platform/drivers/a9-eink"
        device = self.base / "sys/devices/platform/card-owner"
        device.mkdir()
        (device / "driver").symlink_to(owner, target_is_directory=True)
        (self.epd.parent.parent / "device").symlink_to(device, target_is_directory=True)
        self.send("s\n")
        self.wait_text(self.node("mode"), "518\n")

    def test_missing_or_foreign_class_owner_is_rejected(self):
        shutil.rmtree(self.base / "sys/devices/platform/eink-display/drm")
        for number, driver in [(5, None), (6, "not_a9_eink")]:
            self.epd = self.make_group(f"unknown{number}", number, "DSI-1", driver)
        self.send("p\nr\n")
        self.barrier()
        self.assertEqual(self.node("mode").read_text(), "initial\n")
        self.assertEqual((self.vkms / "mode").read_text(), "initial\n")

    def test_multiple_matching_groups_are_rejected(self):
        second = self.make_group("another-a9", 9, "DSI-1", "a9_eink")
        self.send("p\n")
        self.barrier()
        self.assertEqual(self.node("mode").read_text(), "initial\n")
        self.assertEqual((second / "mode").read_text(), "initial\n")

    def test_reprobe_renumbering_and_late_discovery(self):
        self.send("c\n")
        self.wait_text(self.node("mode"), "515\n")
        shutil.rmtree(self.base / "sys/devices/platform/eink-display/drm")
        self.send("p\n")
        self.barrier()  # Missing group does not block the daemon or create files.
        self.epd = self.make_group("eink-display", 17, "DSI-4")
        self.send("b\n")
        self.wait_text(self.node("mode"), "513\n")
        self.assertEqual((self.vkms / "mode").read_text(), "initial\n")
    def test_split_and_coalesced_lines(self):
        self.send("st")
        time.sleep(0.02)
        self.send("b42\nstw17\nsco69\n")
        self.barrier()
        for name, value in [("black_threshold", 42), ("white_threshold", 17), ("contrast", 69)]:
            self.assertEqual(self.node(name).read_text(), f"{value}\n")

    def test_idle_client_does_not_block_second(self):
        with self.connect() as other:
            other.sendall(b"p\n")
            self.wait_text(self.node("mode"), "521\n")
        self.send("b\n")
        self.wait_text(self.node("mode"), "513\n")

    def test_malformed_commands_are_contained(self):
        self.send("stb-1\nstw101\nsco9999\nsmb2201\nwov2\nstl98\nb;bad\n")
        self.send("theme1 a\ntheme1 aaaaaa extra\ntheme99999999999999999999999999 aaaaaa\n")
        self.send("x" * 1024 + "\nb\n")
        self.client.sendall(b"stb4\x005\n")
        self.barrier()
        self.assertEqual(self.node("black_threshold").read_text(), "initial\n")
        self.assertEqual(self.node("white_threshold").read_text(), "initial\n")
        self.assertEqual(self.node("contrast").read_text(), "initial\n")
        self.assertEqual(self.node("mode").read_text(), "513\n")
        self.assertFalse((self.props / "sys.linevibrator_touch").exists())

    def test_framework_properties_and_sentinels(self):
        for value in [0, 47, 95, 96, 97]:
            self.send(f"stl{value}\n")
            self.wait_text(self.props / "sys.linevibrator_type", str(value))
        self.send("smb2000\nwov1\n")
        self.barrier()
        self.assertEqual((self.props / "sys.linevibrator_touch").read_text(), "2000")
        self.assertEqual((self.props / "sys.wakeup_on_volume").read_text(), "1")

    def test_frontlight_gain_zero_and_maximum(self):
        for logical, expected in [(1000, [810, 750, 780, 720]),
                                  (2200, [1782, 1650, 1596, 1473]), (0, [0, 0, 0, 0])]:
            self.prop("sys.linevibrator_open", str(logical))
            self.prop("sys.linevibrator_short", str(logical))
            for name, value in zip(LEDS, expected):
                self.wait_text(self.led(name), f"{value}\n")

    def test_no_repeated_frontlight_writes_and_invalid_values(self):
        self.prop("sys.linevibrator_short", "1000")
        path = self.led("a9:white:center")
        self.wait_text(path, "750\n")
        stamp = path.stat().st_mtime_ns
        time.sleep(0.16)
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        self.prop("sys.linevibrator_short", "-1")
        time.sleep(0.10)
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        self.prop("sys.linevibrator_short", "2201")
        time.sleep(0.10)
        self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_late_led_probe_retries_failed_write(self):
        path = self.led("a9:warm:edge")
        path.unlink()
        self.prop("sys.linevibrator_open", "1234")
        self.wait_text(self.led("a9:warm:center"), "999\n")
        time.sleep(0.08)
        self.assertFalse(path.exists(), "sysfs files must never be created")
        path.write_text("late\n")
        self.wait_text(path, "962\n")

    def test_aw_only_board_without_optional_ktd(self):
        edges = [self.led(name) for name in LEDS[2:]]
        for path in edges:
            path.unlink()
        for logical, warm, white in [(1000, 810, 750), (2200, 1782, 1650), (0, 0, 0)]:
            self.prop("sys.linevibrator_open", str(logical))
            self.prop("sys.linevibrator_short", str(logical))
            self.wait_text(self.led("a9:warm:center"), f"{warm}\n")
            self.wait_text(self.led("a9:white:center"), f"{white}\n")
            self.assertIsNone(self.process.poll())
            for path in edges:
                self.assertFalse(path.exists(), "absent KTD targets must not be created")

    def test_configurable_frontlight_paths_preserve_chip_gains(self):
        self.stop()
        self.client.close()
        extra, outputs = [], []
        for option, name in [("warm-center", "future-aw1"), ("white-center", "future-aw2"),
                             ("warm-edge", "future-ktd3"), ("white-edge", "future-ktd4")]:
            path = self.led(name)
            path.parent.mkdir(parents=True)
            path.write_text("initial\n")
            outputs.append(path)
            extra += ["--led-" + option, "/" + str(path.relative_to(self.base))]
        self.launch(extra)
        self.prop("sys.linevibrator_open", "1000")
        self.prop("sys.linevibrator_short", "1000")
        for path, value in zip(outputs, [810, 750, 780, 720]):
            self.wait_text(path, f"{value}\n")
        for name in LEDS:
            self.assertEqual(self.led(name).read_text(), "initial\n")

    def test_non_sysfs_frontlight_path_is_rejected(self):
        result = subprocess.run([str(BINARY), "--led-white-edge", "/tmp/brightness"], capture_output=True)
        self.assertEqual(result.returncode, 2)

    def test_socket_handoff_requires_previous_owner_to_exit(self):
        self.stop()
        self.client.close()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as owner:
            owner.bind(self.address)
            owner.listen()
            result = subprocess.run(
                [str(BINARY), "--root", str(self.base), "--props", str(self.props),
                 "--tools", str(self.tools), "--socket", self.address[1:]], capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn(b"finish stopping", result.stderr)
        self.launch()
        self.send("b\n")
        self.wait_text(self.node("mode"), "513\n")

    def test_night_light_preserves_native_transaction_sequence(self):
        self.send("sct3300\n")
        self.assertEqual(self.client.recv(3), b"OK\n")
        self.assertEqual(self.calls(), [["service", "call", "color_display", "7", "i32", "1"],
                                       ["service", "call", "color_display", "9", "i32", "3300"]])

    def test_night_light_rejects_other_sdks_and_out_of_range(self):
        for value in (2595, 4083):
            self.send(f"sct{value}\n")
            self.assertEqual(self.client.recv(3), b"NO\n")
        self.prop("ro.build.version.sdk", "35")
        self.send("sct3300\n")
        self.assertEqual(self.client.recv(3), b"NO\n")
        self.assertEqual(self.calls(), [])

    def test_binder_exception_false_and_process_failure(self):
        for mode in ("exception", "false", "exit"):
            (self.base / "reply").write_text(mode)
            self.send("sct3300\n")
            self.assertEqual(self.client.recv(3), b"NO\n")
        self.assertEqual(len(self.calls()), 3, "failed activation must skip temperature call")

    def test_binder_timeout_is_bounded(self):
        (self.base / "reply").write_text("slow")
        start = time.monotonic()
        self.send("sct3300\n")
        self.assertEqual(self.client.recv(3), b"NO\n")
        self.assertLess(time.monotonic() - start, 1.4)
        self.send("r\n")
        self.wait_text(self.node("manual_clear"), "1\n")

    def test_theme_and_unsupported_bitmap_do_not_pollute_reply_stream(self):
        self.send("theme1 abcdef\ncm\nsct3300\n")
        self.assertEqual(self.client.recv(3), b"OK\n")
        call = self.calls()[0]
        self.assertEqual(call[:4], ["settings", "put", "secure", "theme_customization_overlay_packages"])
        self.assertEqual(json.loads(call[4])["android.theme.customization.theme_style"], "VIBRANT")
        self.assertFalse(self.node("commit_bitmap").exists())

    def relaunch(self, **props):
        self.stop(); self.client.close()
        self.node("mode").write_text("initial\n")
        for key, value in props.items():
            self.prop("ro.vendor.a9ml.eink." + key, value)
        self.launch()

    def test_clear_shadow_property_and_command(self):
        self.send("shadow1\n"); self.barrier(); time.sleep(0.05)
        self.wait_text(self.node("clear_shadow"), "1\n")
        self.send("shadow0\n"); self.wait_text(self.node("clear_shadow"), "0\n")
        self.send("shadow2\nshadow\n"); self.barrier(); time.sleep(0.05)
        self.assertEqual(self.node("clear_shadow").read_text(), "0\n")
        self.relaunch(clear_shadow="1")
        self.wait_text(self.node("clear_shadow"), "1\n")

    def test_clear_shadow_untouched_by_default_and_old_kernel_gives_up(self):
        self.send("c\n"); self.wait_text(self.node("mode"), "515\n")
        self.assertEqual(self.node("clear_shadow").read_text(), "initial\n")
        self.node("clear_shadow").unlink()
        self.send("shadow1\n"); self.barrier()
        time.sleep(0.2)
        self.assertTrue(self.process.poll() is None)

    def scroll(self, n, gap=0.02):
        for _ in range(n):
            self.send("sv\n"); time.sleep(gap)

    def test_dynamic_refresh_off_by_default(self):
        self.relaunch(default_mode="c")
        self.wait_text(self.node("mode"), "515\n")
        self.scroll(10); self.send("ac\n"); self.barrier(); time.sleep(0.4)
        self.assertEqual(self.node("mode").read_text(), "515\n")
        self.assertEqual(self.node("manual_clear").read_text(), "initial\n")

    def test_dynamic_refresh_switches_after_events_and_restores(self):
        self.relaunch(default_mode="c", lock_mode="1", dyn_refresh="1", dyn_events="3", dyn_restore_ms="300")
        self.wait_text(self.node("mode"), "515\n")
        self.scroll(2); time.sleep(0.05)
        self.assertEqual(self.node("mode").read_text(), "515\n")
        self.scroll(1); self.wait_text(self.node("mode"), "513\n")
        self.scroll(12, 0.05)	# 600 ms of scrolling > restore delay: stays fast
        self.assertEqual(self.node("mode").read_text(), "513\n")
        last = time.monotonic()
        self.wait_text(self.node("mode"), "515\n")
        self.assertGreaterEqual(time.monotonic() - last, 0.25)
        self.send("s\n"); self.barrier(); time.sleep(0.05)	# lock_mode still blocks app requests
        self.assertEqual(self.node("mode").read_text(), "515\n")

    def test_dynamic_refresh_smooth_and_user_change_wins(self):
        self.relaunch(default_mode="c", dyn_refresh="1", dyn_fast="s", dyn_events="2", dyn_restore_ms="300")
        self.wait_text(self.node("mode"), "515\n")
        self.scroll(2); self.wait_text(self.node("mode"), "518\n")
        self.send("p\n"); self.wait_text(self.node("mode"), "521\n")
        time.sleep(0.6)	# the pending restore must not overwrite the user's mode
        self.assertEqual(self.node("mode").read_text(), "521\n")
        self.scroll(5); time.sleep(0.6)	# rest is Speed, stock only leaves Clear
        self.assertEqual(self.node("mode").read_text(), "521\n")
        self.send("c\n"); self.wait_text(self.node("mode"), "515\n")
        self.scroll(2); self.wait_text(self.node("mode"), "518\n")
        self.wait_text(self.node("mode"), "515\n")

    def storm(self, seconds, gap=0.02):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.send("ac\n"); time.sleep(gap)

    def test_activity_clear_hard_minimums_with_zero_config(self):
        # review 68: zero delay/re-arm used to allow 20 clears in 0.23 s. Hard floors
        # (6 s re-arm, 100 ms delay) plus a global limiter that starts at daemon start.
        self.relaunch(activity_clear="1", activity_delay_ms="0", activity_rearm_ms="0")
        self.storm(3)
        self.assertEqual(self.node("manual_clear").read_text(), "initial\n")	# not before 6 s from start
        self.storm(4.0)
        self.wait_text(self.node("manual_clear"), "1\n")
        self.node("manual_clear").write_text("x\n")
        self.storm(4.0)	# ~4 s of continuous events inside the 6 s window: no second clear
        self.assertEqual(self.node("manual_clear").read_text(), "x\n")
        self.send("r\n"); self.wait_text(self.node("manual_clear"), "1\n")	# explicit stays immediate
        self.node("manual_clear").write_text("x\n")
        self.storm(2.0)	# the explicit clear restarted the global limiter
        self.assertEqual(self.node("manual_clear").read_text(), "x\n")

    def test_activity_clear_coalesces_after_startup_gate(self):
        self.relaunch(activity_clear="1", activity_delay_ms="150")
        time.sleep(6.1)
        for _ in range(4):
            self.send("ac\n"); time.sleep(0.03)
        self.wait_text(self.node("manual_clear"), "1\n")
        self.node("manual_clear").write_text("x\n")
        self.send("ac\n"); self.barrier(); time.sleep(0.5)	# inside the re-arm window
        self.assertEqual(self.node("manual_clear").read_text(), "x\n")

    def test_dynamic_refresh_slow_stream_does_not_qualify(self):
        self.relaunch(default_mode="c", dyn_refresh="1", dyn_events="4", dyn_window_ms="200", dyn_restore_ms="0")
        self.wait_text(self.node("mode"), "515\n")
        self.scroll(8, 0.12)	# gaps below the window, but 4 events span > 200 ms
        self.assertEqual(self.node("mode").read_text(), "515\n")
        self.scroll(4, 0.01); self.wait_text(self.node("mode"), "513\n")
        self.wait_text(self.node("mode"), "515\n")	# zero restore is floored to 100 ms, not a flip-flop

    def test_no_hardware_access_without_fake_arguments_on_host(self):
        completed = subprocess.run([str(BINARY)], capture_output=True)
        self.assertEqual(completed.returncode, 2)


if __name__ == "__main__":
    unittest.main()
