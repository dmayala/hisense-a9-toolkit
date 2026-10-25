#!/usr/bin/env python3
"""Host test: a9-eink-ctl against a tiny abstract-socket server. usage: test_ctl.py <ctl>"""
import socket, subprocess, sys, threading
ctl = sys.argv[1]
name = "a9_eink_test_%d" % __import__("os").getpid()
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind("\0" + name); srv.listen(4)
got = []
def serve():
    while True:
        try: c, _ = srv.accept()
        except OSError: return
        data = b""
        while not data.endswith(b"\n"):
            d = c.recv(64)
            if not d: break
            data += d
        got.append(data)
        if data.startswith(b"sct"):
            if data.strip() == b"sct4000": c.sendall(b"OK\n")
            elif data.strip() == b"sct9": c.sendall(b"NO\n")
            # sct7: no reply -> timeout
        c.close()
threading.Thread(target=serve, daemon=True).start()
def run(*a): return subprocess.run([ctl, "--socket", name, *a], capture_output=True, text=True)
for cmd in "cbspr": 
    r = run(cmd); assert r.returncode == 0 and r.stdout == "", (cmd, r)
    assert got[-1] == cmd.encode() + b"\n", got
r = run("sct4000"); assert r.returncode == 0 and r.stdout == "OK\n", r
r = run("sct9"); assert r.returncode == 1 and r.stdout == "NO\n", r
r = run("sct7"); assert r.returncode == 1 and r.stdout == "", r  # server closes without reply
assert run().returncode == 1 and "usage" in run().stderr
assert run("a\nb").returncode == 1
r = subprocess.run([ctl, "--socket", "nonexistent_zz", "c"], capture_output=True, text=True); assert r.returncode == 1
print("ctl tests passed")
