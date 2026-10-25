#!/usr/bin/env python3
"""Run bkerler edlclient in-process with QEMU-passthrough-safe USB reads.

Root-cause hypothesis: edlclient issues bulk-IN reads with 1..10 ms timeouts
(usblib.usbread, sahara.connect, sahara.cmd_exec, firehose configure). When such a
read times out the URB is cancelled. Under QEMU usb-host passthrough the host-side
libusb transfer may already have completed (data received from the device) when the
guest cancels -> QEMU drops the data. The Sahara HELLO (sent exactly once by PBL) is
lost, edl then sends garbage (<nop/>, 0x7E nandprg) and PBL goes to an error state.

Patches:
  * usb_class.usbread: any timeout <= 10 ms becomes ONE read of EDL_MIN_MS (default
    250) and returns b"" on timeout (same semantics as original "give up", no
    sub-millisecond cancellations). Longer timeouts unchanged.
  * sahara.connect: first read waits EDL_HELLO_MS (default 5000). Never sends the
    0x7E nandprg probe. The XML <nop/> fallback (needed when firehose is already
    loaded) only if EDL_ALLOW_NOP=1. EDL_FAKE_HELLO=1: if no hello arrives, pretend a
    v2 hello was received (diagnostic: PBL may still be waiting for HELLO_RESP).

Usage: edl_qemu.py <edl args...>
"""
import os, sys, time, runpy, struct, logging

EDL_BIN = os.environ.get("EDL_BIN") or os.path.realpath(
    "/run/current-system/sw/bin/edl")
WRAPPED = os.path.join(os.path.dirname(EDL_BIN), ".edl-wrapped")
src = open(WRAPPED).read()
for line in src.splitlines():
    if line.startswith("import sys;import site;"):
        exec(line)
        break

import usb.core, usb.util
from edlclient.Library.Connection import usblib
from edlclient.Library import sahara as sahara_mod
from edlclient.Library.sahara_defs import cmd_t

MIN_MS = int(os.environ.get("EDL_MIN_MS", "250"))
HELLO_MS = int(os.environ.get("EDL_HELLO_MS", "5000"))
ALLOW_NOP = os.environ.get("EDL_ALLOW_NOP") == "1"
FAKE_HELLO = os.environ.get("EDL_FAKE_HELLO") == "1"

def T():
    return time.strftime("%H:%M:%S") + ".%03d" % (int(time.time() * 1000) % 1000)

def say(*a):
    print(f"[qemu-patch {T()}]", *a, file=sys.stderr, flush=True)

def usbread(self, resplen=None, timeout=0):
    if timeout == 0:
        timeout = 1
    if resplen is None:
        resplen = self.maxsize
    short = timeout is not None and timeout <= 10
    if short:
        timeout = MIN_MS
    res = bytearray()
    buffer = self.buffer[:resplen]
    epr = self.EP_IN.read
    while len(res) < resplen:
        try:
            resplen = epr(buffer, timeout)
            res.extend(buffer[:resplen])
            if resplen == self.EP_IN.wMaxPacketSize:
                break
        except usb.core.USBError as e:
            error = str(e.strerror)
            if "timed out" in error:
                if timeout is None or short:
                    return b""
                self.debug("Timed out")
                timeout += 1
            elif "Overflow" in error:
                self.error("USB Overflow")
                return b""
            else:
                self.info(repr(e))
                return b""
    if self.loglevel == logging.DEBUG:
        self.verify_data(res[:resplen], "RX:")
    return res[:resplen]

usblib.usb_class.usbread = usbread

_ncall = [0]
def connect(self):
    _ncall[0] += 1
    first = _ncall[0] == 1
    try:
        say(f"sahara.connect #{_ncall[0]}: reading up to 0x30 bytes, {HELLO_MS} ms")
        v = b""
        if first and os.environ.get("EDL_PRERESET") == "1":
            say("EDL_PRERESET: USBDEVFS_RESET (passes through QEMU as a real port reset)")
            try:
                self.cdc.device.reset()
            except usb.core.USBError as e:
                say("reset:", e)
            time.sleep(0.2)
            try:
                usb.util.claim_interface(self.cdc.device, 0)
            except Exception as e:
                say("reclaim:", e)
        try:
            v = bytes(self.cdc.EP_IN.read(0x1000, HELLO_MS))
        except usb.core.USBError as e:
            say("first read:", e.strerror)
        if not v and first and os.environ.get("EDL_CLEARHALT") == "1":
            say("EDL_CLEARHALT: CLEAR_FEATURE(ENDPOINT_HALT) on 0x81/0x01 (resets data toggles)")
            for ep in (self.cdc.EP_IN, self.cdc.EP_OUT):
                try:
                    self.cdc.device.clear_halt(ep.bEndpointAddress)
                except usb.core.USBError as e:
                    say("clear_halt:", e)
            try:
                v = bytes(self.cdc.EP_IN.read(0x1000, HELLO_MS))
            except usb.core.USBError as e:
                say("read after clear_halt:", e.strerror)
        say(f"first read got {len(v)} bytes: {v[:48].hex()}")
        if len(v) > 1:
            if v[0] == 0x01:
                pkt = self.ch.pkt_cmd_hdr(v)
                if pkt.cmd == cmd_t.SAHARA_HELLO_REQ:
                    rsp = self.ch.pkt_hello_req(v)
                    self.pktsize = rsp.cmd_packet_length
                    self.version = rsp.version
                    self.info(f"Protocol version: {rsp.version}, Version supported: {rsp.version_supported}")
                    return {"mode": "sahara", "cmd": cmd_t.SAHARA_HELLO_REQ, "data": rsp}
            if b"<?xml" in v:
                # firehose log/response already pending; drain
                return {"mode": "firehose"}
            if v[:4] == struct.pack("<I", cmd_t.SAHARA_END_TRANSFER):
                rsp = self.ch.pkt_image_end(v)
                say("END_TRANSFER status", rsp.image_tx_status)
                return {"mode": "sahara", "cmd": cmd_t.SAHARA_END_TRANSFER, "data": rsp}
            say("unexpected data; giving up")
            return {"mode": "error"}
        if FAKE_HELLO and first:
            say("NO HELLO seen; EDL_FAKE_HELLO=1 -> synthesising v2 hello")
            fake = struct.pack("<12I", 1, 0x30, 2, 1, 0x400, 0, 0, 0, 0, 0, 0, 0)
            rsp = self.ch.pkt_hello_req(fake)
            self.pktsize = 0x400
            self.version = 2
            return {"mode": "sahara", "cmd": cmd_t.SAHARA_HELLO_REQ, "data": rsp}
        if ALLOW_NOP:
            say("no unsolicited data; sending firehose <nop/>")
            self.cdc.write(b"<?xml version=\"1.0\" ?><data><nop /></data>")
            res = b""
            for _ in range(5):
                try:
                    res += bytes(self.cdc.EP_IN.read(0x4000, 2000))
                except usb.core.USBError as e:
                    break
                if b"</data>" in res and b"response" in res:
                    break
            say(f"nop reply {len(res)} bytes: {res[:200]!r}")
            if b"<?xml" in res:
                return {"mode": "firehose"}
        say("no response; NOT sending 0x7E nandprg probe; mode=error")
    except Exception as e:
        say("connect exception:", repr(e))
    return {"mode": "error"}

sahara_mod.sahara.connect = connect

sys.argv = [EDL_BIN] + sys.argv[1:]
say("argv:", sys.argv[1:], f"MIN_MS={MIN_MS} HELLO_MS={HELLO_MS} NOP={ALLOW_NOP} FAKE={FAKE_HELLO}")
try:
    runpy.run_path(WRAPPED, run_name="__main__")
except SystemExit as e:
    say("exit", e.code)
    raise
