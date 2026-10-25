#!/usr/bin/env python3
"""Host-only persistent /data POC; builds regular files, never opens a device.

Without --block-device, deliver an image and a deliberately incomplete fstab
template. Only the coordinator can verify the candidate's current use and
select its concrete Linux device node. No phone write is performed here.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
SIZE = 2816 * 1024**2
MARKER = b"A9 mainline persistent data POC, round 7, 2026-10-20\n"
PLACEHOLDER = "VERIFIED_LUN4_PARTITION_59"


def fstab(device):
    # LUN4's candidate is GPT entry59, NOT entry58 inferred by counting the
    # abbreviated partition-map.txt. Repeated by-name/last_parti is ambiguous.
    if device != PLACEHOLDER and not re.fullmatch(r"/dev/block/sd[a-z]59", device):
        raise ValueError("use the verified LUN4 partition59 /dev/block/sdX59 node")
    original = (HERE.parent / "android/vendor/etc/fstab.hlte556n").read_text()
    rows = [line for line in original.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]
    data = [i for i, row in enumerate(rows) if row.split()[1] == "/data"]
    if len(data) != 1 or rows[data[0]].split()[0] != "/dev/block/ram0":
        raise ValueError("expected the existing single RAM /data fstab entry")
    rows[data[0]] = (f"{device} /data ext4 "
                     "noatime,nosuid,nodev,errors=remount-ro wait,latemount")
    return ("# OPT-IN POC: verified LUN4 last_parti, GPT partition59 only.\n"
            "# Preformatted ext4; never format/check/discard a target at boot.\n"
            "# No userdata, metadata, misc, encryption or voldmanaged entries.\n"
            "# Revert this vendor change to recover the default RAM /data.\n"
            + "\n".join(rows) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--block-device", help="coordinator-verified LUN4 partition59 node; never inferred")
    ap.add_argument("--overlay-only", action="store_true",
                    help="render fstab only after target verification; do not rebuild data.img")
    args = ap.parse_args()
    if args.overlay_only and not args.block_device:
        ap.error("--overlay-only requires --block-device")
    contents = fstab(args.block_device or PLACEHOLDER)
    os.umask(0o077)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # Keep debugfs command-file paths simple (no quoting or metacharacters).
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(out)):
        raise ValueError("output path must not contain whitespace or shell metacharacters")
    destination = out / ("vendor-overlay/etc/fstab.hlte556n" if args.block_device
                         else "fstab.hlte556n.in")
    if destination.exists() or (not args.overlay_only and (out / "data.img").exists()):
        raise FileExistsError("use a fresh output directory; existing artifacts are never overwritten")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(contents)
    if args.overlay_only:
        print(destination)
        return
    if shutil.disk_usage(out).free < 15 * 1024**3 + SIZE:
        raise RuntimeError("need 15 GiB free plus the 2816 MiB image budget")

    def run(argv, log):
        with (out / log).open("w") as stream:
            subprocess.run([str(x) for x in argv], check=True,
                           stdout=stream, stderr=subprocess.STDOUT)

    # Exclusive regular-file creation: mkfs is never given a /dev path.
    image = out / "data.img"
    with image.open("xb") as stream:
        stream.truncate(SIZE)
    root = out / "root"
    root.mkdir()
    (root / "a9-persistent-data-poc.txt").write_bytes(MARKER)
    run(["mke2fs", "-t", "ext4", "-b", "4096", "-I", "256", "-i", "16384",
         "-m", "0", "-L", "a9ml-data-poc", "-J", "size=64",
         "-O", "none,has_journal,ext_attr,dir_index,filetype,extent,64bit,flex_bg,"
               "sparse_super,large_file,huge_file,dir_nlink,extra_isize,metadata_csum",
         "-E", "lazy_itable_init=0,lazy_journal_init=0", "-d", root, image], "mkfs.log")
    label = out / "root-label"
    label.write_bytes(b"u:object_r:system_data_root_file:s0\0")
    commands = out / "debugfs.txt"
    commands.write_text("sif / mode 040771\nsif / uid 1000\nsif / gid 1000\n"
                        f"ea_set -f {label} / security.selinux\n"
                        "sif /a9-persistent-data-poc.txt mode 0100644\n"
                        "sif /a9-persistent-data-poc.txt uid 1000\n"
                        "sif /a9-persistent-data-poc.txt gid 1000\n")
    run(["debugfs", "-w", "-f", commands, image], "debugfs.log")
    run(["e2fsck", "-fn", image], "fsck.log")
    readback = out / "marker-readback.txt"
    run(["debugfs", "-R", f"dump /a9-persistent-data-poc.txt {readback}", image], "readback.log")
    if readback.read_bytes() != MARKER:
        raise RuntimeError("filesystem marker readback differs")
    run(["debugfs", "-R", "stat /", image], "root-stat.txt")
    stat = (out / "root-stat.txt").read_text()
    if not (re.search(r"Mode:\s+0771\b", stat)
            and re.search(r"User:\s+1000\s+Group:\s+1000\b", stat)
            and 'u:object_r:system_data_root_file:s0' in stat):
        raise RuntimeError("root metadata readback differs")
    with image.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (out / "data.img.sha256").write_text(digest + "  data.img\n")
    (out / "build.json").write_text(json.dumps({
        "source_commit": subprocess.check_output(
            ["git", "-C", str(HERE), "rev-parse", "HEAD"], text=True).strip(),
        "image_bytes": SIZE, "image_sha256": digest,
        "candidate": {"lun": 4, "gpt_partition": 59,
                      "map_start_4096": 274658, "map_length_4096": 773913},
        "target_current_use_verified": False,
        "device_node": args.block_device,
        "phone_writes_performed": False,
    }, indent=2) + "\n")
    print(f"{image}\nsha256 {digest}\nNo phone writes performed; see README.md before selecting a target.")


if __name__ == "__main__":
    main()
