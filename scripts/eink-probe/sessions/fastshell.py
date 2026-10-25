#!/usr/bin/env python3
"""fastshell: one persistent `<adb> shell` behind a unix socket, plus a client that can stand in
for the `adb` command prefix of `eink-probe act --adb`.

Why (eink-gpu-perf F7): every `adb shell input ...` pays adb transport + a new shell per command
(110-776 ms measured). Here the shell is already running; the client sends the command line over
a local socket and returns when the shell printed a completion marker, so the host-measured
`t_ret` is "command finished inside the phone shell".

  fastshell.py serve  --sock S --real 'adb -s SERIAL'     # long-running (the session starts it)
  fastshell.py run    --sock S --real 'adb -s SERIAL' -- shell input keyevent KEYCODE_HOME
  fastshell.py stop   --sock S

`run` forwards anything that is not `shell ...` (for example `exec-out screencap -p`) to the real
adb unchanged (exec), so it is a drop-in prefix. If the daemon is not reachable it exits 255 and
says so on stderr (it does NOT silently fall back, so a dead injector is visible in the probe's
adb rc). The daemon only ever runs what the client sends; the session script decides what that is.
"""
import argparse
import json
import os
import queue
import shlex
import socket
import socketserver
import subprocess
import sys
import threading
import time


def serve(sock_path, real, ready_file=None):
    proc = subprocess.Popen(shlex.split(real) + ["shell"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    q = queue.Queue()

    def reader():
        for line in iter(proc.stdout.readline, b""):
            q.put(line.decode(errors="replace"))
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    lock = threading.Lock()
    counter = [0]
    done = threading.Event()

    def run_cmd(cmd, timeout):
        with lock:
            if proc.poll() is not None:
                return 255, "persistent shell is gone (rc %s)" % proc.returncode
            while True:                     # drop output of an earlier command that timed out
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            counter[0] += 1
            tok = "__FS%d_%d__" % (os.getpid(), counter[0])
            # subshell: an `exit` or a syntax error in cmd cannot kill the persistent shell
            script = "(\n%s\n) 2>&1\nprintf '\\n%s %%s\\n' $?\n" % (cmd, tok)
            try:
                proc.stdin.write(script.encode())
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                return 255, "persistent shell is gone (write failed)"
            buf = []
            end = time.monotonic() + timeout
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    return 124, "".join(buf) + "\n[fastshell: timeout]"
                try:
                    line = q.get(timeout=left)
                except queue.Empty:
                    continue
                if line is None:
                    return 255, "".join(buf) + "\n[fastshell: shell ended]"
                if line.startswith(tok + " "):
                    out = "".join(buf)
                    if out.endswith("\n"):
                        out = out[:-1]      # the newline our printf added
                    try:
                        return int(line.split()[1]), out
                    except ValueError:
                        return 255, out
                buf.append(line)

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                req = json.loads(self.rfile.readline().decode())
            except Exception:
                return
            if req.get("op") == "quit":
                self.wfile.write(b'{"rc":0,"out":"bye"}\n')
                done.set()
                return
            rc, out = run_cmd(req.get("cmd", ""), float(req.get("timeout", 60)))
            self.wfile.write((json.dumps({"rc": rc, "out": out}) + "\n").encode())

    if os.path.exists(sock_path):
        os.unlink(sock_path)
    srv = socketserver.ThreadingUnixStreamServer(sock_path, H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if ready_file:
        open(ready_file, "w").write("ready\n")
    while not done.is_set() and proc.poll() is None:
        time.sleep(0.1)
    srv.shutdown()
    try:
        proc.stdin.write(b"exit\n"); proc.stdin.flush()
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:
        proc.kill()
    try:
        os.unlink(sock_path)
    except OSError:
        pass
    return 0


def request(sock_path, req, timeout):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock_path)
    s.sendall((json.dumps(req) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    s.close()
    return json.loads(data.decode())


def run(sock_path, real, args, timeout=60.0):
    if not args or args[0] != "shell":
        os.execvp(shlex.split(real)[0], shlex.split(real) + args)   # pass-through (screencap etc.)
    cmd = " ".join(args[1:])
    try:
        r = request(sock_path, {"cmd": cmd, "timeout": timeout}, timeout + 5)
    except (OSError, ValueError) as e:
        sys.stderr.write("fastshell: daemon not reachable (%s)\n" % e)
        return 255
    sys.stdout.write(r["out"])
    sys.stdout.flush()
    return int(r["rc"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("serve", "run", "stop"):
        p = sub.add_parser(n)
        p.add_argument("--sock", required=True)
        p.add_argument("--real", default="adb")
        if n == "serve":
            p.add_argument("--ready-file")
        if n == "run":
            p.add_argument("--timeout", type=float, default=60.0)
            p.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    if a.cmd == "serve":
        return serve(a.sock, a.real, a.ready_file)
    if a.cmd == "stop":
        try:
            request(a.sock, {"op": "quit"}, 5)
        except (OSError, ValueError):
            pass
        return 0
    args = a.args[1:] if a.args and a.args[0] == "--" else a.args
    return run(a.sock, a.real, args, a.timeout)


if __name__ == "__main__":
    sys.exit(main())
