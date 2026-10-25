"""Synthetic camera + screens for eink-probe tests (no phone, no webcam)."""
import os
import sys
import threading
import time
import http.server

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts", "eink-probe"))
import probe_lib as L  # noqa: E402

CAM_W, CAM_H = 1920, 1080
QUAD = np.array([[704, 80], [1112, 72], [1120, 900], [696, 912]], np.float32)  # TL TR BR BL (camera px)


def screen(name):
    """824x1648 BGR synthetic screen: text-like rows on white, or a flat colour."""
    if name == "white":
        return np.full((L.PANEL_H, L.PANEL_W, 3), 255, np.uint8)
    if name == "black":
        return np.zeros((L.PANEL_H, L.PANEL_W, 3), np.uint8)
    im = np.full((L.PANEL_H, L.PANEL_W, 3), 255, np.uint8)
    rng = np.random.RandomState(abs(hash(name)) % 1000 if False else sum(map(ord, name)))
    for k in range(14):
        y = 120 + k * 105
        cv2.putText(im, "%s line %d" % (name, k), (60 + rng.randint(0, 80), y), cv2.FONT_HERSHEY_SIMPLEX,
                    1.7, (0, 0, 0), 4)
        cv2.rectangle(im, (60, y + 25), (60 + rng.randint(200, 700), y + 40), (0, 0, 0), -1)
    cv2.rectangle(im, (0, 0), (L.PANEL_W - 1, 90), (0, 0, 0), -1)
    return im


def camera(scr, quad=QUAD, gain=1.0, noise=1.5, seed=0, blend=None):
    """Render what the webcam sees: dim glass in a dark scene, flat-field
    illumination gradient, blur, noise. `blend` = (other_screen, w) mixes in a ghost."""
    if blend is not None:
        scr = (scr.astype(np.float32) * (1 - blend[1]) + blend[0].astype(np.float32) * blend[1]).astype(np.uint8)
    g = cv2.cvtColor(scr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    yy, xx = np.mgrid[0:L.PANEL_H, 0:L.PANEL_W].astype(np.float32)
    illum = 0.75 + 0.25 * (xx / L.PANEL_W)
    glass = (0.12 + 0.55 * g) * illum * gain
    H = L.homography_from_quad(quad)
    Hinv = np.linalg.inv(H)
    cam = np.full((CAM_H, CAM_W), 0.10 * gain, np.float32)
    wg = cv2.warpPerspective(glass, Hinv, (CAM_W, CAM_H), flags=cv2.INTER_AREA)
    m = cv2.warpPerspective(np.ones_like(glass), Hinv, (CAM_W, CAM_H), flags=cv2.INTER_NEAREST) > 0.5
    cam[m] = wg[m]
    cam = cv2.GaussianBlur(cam, (0, 0), 1.0)
    rng = np.random.RandomState(seed)
    cam = cam + rng.normal(0, noise / 255, cam.shape).astype(np.float32)
    return cv2.cvtColor(np.clip(cam * 255, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)


def jpeg(img, q=85):
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes()


class CamServer:
    """MJPEG server (30 fps, X-Timestamp) whose picture is chosen by the first
    word of FILE each frame; maps name -> list of jpeg bytes (cycled for noise)."""

    def __init__(self, statefile, frames, fps=30.0, stall=None):
        """stall = (after_s, for_s): once, `after_s` after the server's first connection the stream goes silent for `for_s`
        (socket open, no bytes), like an ustreamer/USB hiccup. Later connections are served normally."""
        self.statefile, self.frames, self.fps = statefile, frames, fps
        self.stall, self._stall_t0, self._stalled = stall, None, False
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=boundarydonotcross")
                self.end_headers()
                k = 0
                nxt = time.time()
                try:
                    while not outer._stop:
                        if outer.stall and not outer._stalled:
                            if outer._stall_t0 is None:
                                outer._stall_t0 = time.time()
                            if time.time() - outer._stall_t0 >= outer.stall[0]:
                                outer._stalled = True
                                time.sleep(outer.stall[1])
                                nxt = time.time()
                        try:
                            st = open(outer.statefile).read().split()[0]
                        except Exception:
                            st = "A"
                        fl = outer.frames[st]
                        jp = fl[k % len(fl)]
                        k += 1
                        self.wfile.write(b"--boundarydonotcross\r\nContent-Type: image/jpeg\r\n"
                                         b"Content-Length: %d\r\nX-Timestamp: %.6f\r\n\r\n" % (len(jp), time.time()))
                        self.wfile.write(jp)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                        nxt += 1.0 / outer.fps
                        time.sleep(max(0, nxt - time.time()))
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._stop = False
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = "http://127.0.0.1:%d/stream" % self.port

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self._stop = True
        self.httpd.shutdown()
        self.httpd.server_close()
