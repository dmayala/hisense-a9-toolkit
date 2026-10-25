"""Deterministic matched-source fixtures and explicit viewer argv, offline only.

There is no default Android viewer. The caller must supply and later verify a
viewer command that displays the complete image without UI chrome or scaling.
Constructing an argv does not establish intent/file-URI access, SELinux access,
fullscreen presentation, activity lifetime or a usable viewer on either ROM.
This module never executes commands, changes settings or contacts a device.
"""
import hashlib
import json
import os
import re
import shlex
import struct
import zlib

SOURCE_IDS = ("white", "dark")
WIDTH, HEIGHT = 824, 1648
VERSION = 1
_PATH_RE = re.compile(r"/data/local/tmp/[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.png\Z")
_PRIVATE_PATH_RE = re.compile(r"/data/user/0/org\.lineageos\.glimpse/cache/ghost-matched-[0-9a-f]{32}-(?:dark|white)\.png\Z")
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")


def _source_id(source_id):
    if source_id not in SOURCE_IDS:
        raise ValueError("source must be 'white' or 'dark'")
    return source_id


def source_pixels(source_id):
    """Return portrait row-major 8-bit luma, using only exact 0/255 endpoints.

    Dark has a black background, a white border and large alternating white
    squares. Its asymmetric upper-left/upper-right bars make rotation/cropping
    apparent during the future source verification. No fonts or private data.
    """
    _source_id(source_id)
    if source_id == "white":
        return bytes([255]) * (WIDTH * HEIGHT)
    pixels = bytearray(WIDTH * HEIGHT)

    def white_box(x1, y1, x2, y2):
        row = bytes([255]) * (x2 - x1)
        for y in range(y1, y2):
            pixels[y * WIDTH + x1:y * WIDTH + x2] = row

    white_box(0, 0, WIDTH, 12)
    white_box(0, HEIGHT - 12, WIDTH, HEIGHT)
    white_box(0, 0, 12, HEIGHT)
    white_box(WIDTH - 12, 0, WIDTH, HEIGHT)
    white_box(32, 32, 256, 64)
    white_box(WIDTH - 64, 32, WIDTH - 32, 160)
    for row in range(11):
        for col in range(5):
            if (row + col) % 2 == 0:
                x, y = 64 + col * 152, 192 + row * 128
                white_box(x, y, x + 80, y + 80)
    return bytes(pixels)


def _chunk(kind, payload):
    crc = zlib.crc32(kind + payload) & 0xffffffff
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def source_png(source_id):
    """Return a grayscale PNG with no color profiles, time or variable metadata."""
    pixels = source_pixels(source_id)
    rows = b"".join(b"\0" + pixels[y * WIDTH:(y + 1) * WIDTH] for y in range(HEIGHT))
    ihdr = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) +
            _chunk(b"IDAT", zlib.compress(rows, 9)) + _chunk(b"IEND", b""))


def source_metadata(source_id):
    png = source_png(source_id)
    pixels = source_pixels(source_id)
    return {"source_id": source_id, "fixture_version": VERSION,
            "width": WIDTH, "height": HEIGHT, "mime": "image/png",
            "sha256": hashlib.sha256(png).hexdigest(),
            "pixels_sha256": hashlib.sha256(pixels).hexdigest(),
            "white_pixels": pixels.count(255), "black_pixels": pixels.count(0)}


def write_source(out_dir, source_id):
    """Write only a new/identical host fixture; refuse clobbering another file."""
    _source_id(source_id)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.abspath(os.path.join(out_dir, "ghost-source-v%d-%s.png" % (VERSION, source_id)))
    data = source_png(source_id)
    try:
        with open(path, "xb") as out:
            out.write(data)
    except FileExistsError:
        if os.path.islink(path):
            raise ValueError("source fixture path must not be a symlink")
        with open(path, "rb") as old:
            if old.read() != data:
                raise ValueError("source fixture path already contains different bytes")
    return dict(source_metadata(source_id), path=path)


def validate_device_path(device_path):
    """Accept one exact temporary PNG basename, never a directory/glob/shell."""
    if not isinstance(device_path, str) or not (_PATH_RE.fullmatch(device_path) or _PRIVATE_PATH_RE.fullmatch(device_path)):
        raise ValueError("source path must be an allowed temporary PNG")
    return device_path


def validate_viewer_template(template):
    """Parse explicit JSON argv and require {path} and/or {uri} placeholders.

    Tokens are argv values, never shell fragments. source_args shell-quotes
    each token for Android's shell transport. There is no fallback VIEW intent.
    Unknown placeholders/braces and control characters fail before any action.
    """
    if isinstance(template, str):
        try:
            template = json.loads(template)
        except (ValueError, TypeError) as exc:
            raise ValueError("viewer command must be a JSON array of argv strings") from exc
    if not isinstance(template, (list, tuple)) or not template:
        raise ValueError("an explicit viewer argv is required")
    if len(template) > 64:
        raise ValueError("viewer argv has too many tokens")
    found = False
    for token in template:
        if not isinstance(token, str) or not token or len(token) > 4096:
            raise ValueError("viewer argv tokens must be nonempty bounded strings")
        if any(ord(char) < 32 or ord(char) == 127 for char in token):
            raise ValueError("viewer argv must not contain control characters")
        placeholders = _PLACEHOLDER_RE.findall(token)
        if any(name not in ("path", "uri") for name in placeholders):
            raise ValueError("viewer argv has an unsupported placeholder")
        remaining = _PLACEHOLDER_RE.sub("", token)
        if "{" in remaining or "}" in remaining:
            raise ValueError("viewer argv has malformed placeholder braces")
        found |= bool(placeholders)
    if "{" in template[0] or "}" in template[0]:
        raise ValueError("viewer executable must be fixed")
    if not found:
        raise ValueError("viewer argv must contain {path} or {uri}")
    if os.path.basename(template[0]) in ("sh", "bash", "zsh", "dash", "mksh", "eval", "su"):
        raise ValueError("viewer argv must name the viewer command, not a shell")
    return list(template)


def viewer_argv(template, device_path):
    path = validate_device_path(device_path)
    return [token.replace("{path}", path).replace("{uri}", "file://" + path)
            for token in validate_viewer_template(template)]


def source_args(template, device_path):
    """Return phase-step adb argv: ['shell', ONE safely quoted remote command]."""
    return ["shell", shlex.join(viewer_argv(template, device_path))]
