#!/usr/bin/env python3
"""a9-capture-host.py -- host side of the A9 mainline log capture (2026-10-15).

Talks to /bin/a9-ctl in the mainline initramfs. Never talks to adb, fastboot
or EDL; it only reaches the phone while the phone runs the mainline image.

  net <ip> <port> <command...> [--out FILE] [--timeout S]
      one TCP connection to the a9-ctl service (tcpsvd on the NCM link):
      sends the command line, reads until EOF, writes the bytes to FILE or
      stdout. Exit 0 if anything came back.
  acm <tty> <command...> [--out FILE] [--timeout S]
      the same over the ACM serial shell (needs read/write access to the tty;
      run it with sudo when the user is not in the dialout group). The output
      is framed with markers and base64, so the shell's echo and CRLF cannot
      corrupt it.

Commands: ping, stay <s>, release, capture, capture-dbg, bootloader (see a9-ctl).
"""
import argparse
import base64
import os
import select
import socket
import sys
import termios
import time
import tty


def out_bytes(data, path):
    if path:
        with open(path, 'wb') as f:
            f.write(data)
    else:
        sys.stdout.buffer.write(data)


def do_follow(a):
    """--follow: append everything received to --out as it arrives, until the
    phone closes the connection or goes silent for --timeout seconds."""
    with socket.create_connection((a.ip, a.port), timeout=10) as s, open(a.out, 'ab') as f:
        s.settimeout(a.timeout)
        n = 0
        while True:
            try:
                b = s.recv(65536)
            except (socket.timeout, OSError):
                break
            if not b:
                break
            f.write(b)
            f.flush()
            n += len(b)
    return 0 if n else 1


def do_net(a):
    if a.follow:
        return do_follow(a)
    cmd = ' '.join(a.command) + '\n'
    with socket.create_connection((a.ip, a.port), timeout=a.timeout) as s:
        s.sendall(cmd.encode())
        s.shutdown(socket.SHUT_WR)
        buf = bytearray()
        end = time.time() + a.timeout
        while time.time() < end:
            try:
                b = s.recv(65536)
            except socket.timeout:
                break
            if not b:
                break
            buf += b
    out_bytes(bytes(buf), a.out)
    return 0 if buf else 1


def do_acm(a):
    fd = os.open(a.tty, os.O_RDWR | os.O_NOCTTY)
    try:
        tty.setraw(fd)
        termios.tcflush(fd, termios.TCIOFLUSH)
        beg, end_m = '__A9' + 'BEG__', '__A9' + 'END__'
        # markers are printed split so the shell's echo of this line never matches
        ctl = os.environ.get('A9_CTL', '/bin/a9-ctl')   # override for host-side tests only
        line = ("\nprintf '%%s\\n' __A9'BEG__'; %s %s | base64; printf '%%s\\n' __A9'END__'\n"
                % (ctl, ' '.join(a.command)))
        os.write(fd, line.encode())
        buf = bytearray()
        deadline = time.time() + a.timeout
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.5)
            if r:
                b = os.read(fd, 65536)
                if not b:
                    break
                buf += b
                if end_m.encode() + b'\n' in buf.replace(b'\r', b'') and beg.encode() in buf:
                    break
    finally:
        os.close(fd)
    text = buf.replace(b'\r', b'').decode('latin-1')
    lines = text.split('\n')
    try:
        i = next(k for k, l in enumerate(lines) if l.strip() == beg)
        j = next(k for k, l in enumerate(lines) if k > i and l.strip() == end_m)
    except StopIteration:
        sys.stderr.write('acm: markers not found (%d bytes read)\n' % len(buf))
        return 1
    payload = ''.join(l.strip() for l in lines[i + 1:j])
    try:
        data = base64.b64decode(payload, validate=False)
    except Exception as e:
        sys.stderr.write('acm: base64 decode failed: %s\n' % e)
        return 1
    out_bytes(data, a.out)
    return 0 if data else 1


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest='mode', required=True)
    n = sp.add_parser('net')
    n.add_argument('ip')
    n.add_argument('port', type=int)
    n.add_argument('command', nargs='*')
    n.add_argument('--follow', action='store_true',
                   help='stream into --out as data arrives (kmsg port 2324)')
    c = sp.add_parser('acm')
    c.add_argument('tty')
    c.add_argument('command', nargs='+')
    for x in (n, c):
        x.add_argument('--out')
        x.add_argument('--timeout', type=float, default=30)
    a = p.parse_args()
    sys.exit(do_net(a) if a.mode == 'net' else do_acm(a))


if __name__ == '__main__':
    main()
