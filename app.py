"""
RPS Robot — Pi Server
Streams a USB camera over WebRTC to a browser client that runs MediaPipe
gesture recognition. Browser does all the inference; Pi just streams.
"""
import asyncio
import logging
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rps")

# --- Config ---
ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
TEMPLATE_DIR = ROOT / "templates"

CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
CAMERA_WIDTH = int(os.getenv("CAMERA_WIDTH", "480"))
CAMERA_HEIGHT = int(os.getenv("CAMERA_HEIGHT", "360"))
CAMERA_FPS = int(os.getenv("CAMERA_FPS", "25"))
PORT = int(os.getenv("PORT", "5000"))
DEBUG = os.getenv("DEBUG", "0") == "1"

if DEBUG:
    log.setLevel(logging.DEBUG)

log.info(f"Config: cam={CAMERA_INDEX} {CAMERA_WIDTH}x{CAMERA_HEIGHT}@{CAMERA_FPS}fps port={PORT} debug={DEBUG}")

pcs = set()


class Camera:
    """Reads USB camera in a thread; exposes the latest frame."""

    def __init__(self):
        self.cap = None
        self.running = True
        self.lock = threading.Lock()
        self.latest = self._placeholder("Starting camera...")
        self.frame_count = 0
        self.last_log = time.time()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="camera")
        self.thread.start()
        log.info("Camera thread started")

    def _placeholder(self, text: str) -> np.ndarray:
        f = np.zeros((CAMERA_HEIGHT, CAMERA_WIDTH, 3), dtype=np.uint8)
        cv2.putText(f, text, (15, CAMERA_HEIGHT // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        return f

    def _open(self) -> bool:
        if self.cap and self.cap.isOpened():
            return True
        for backend in (cv2.CAP_V4L2, cv2.CAP_ANY):
            try:
                cap = cv2.VideoCapture(CAMERA_INDEX, backend)
                if not cap.isOpened():
                    cap.release()
                    continue
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
                cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
                ok, _ = cap.read()
                if not ok:
                    cap.release()
                    continue
                self.cap = cap
                log.info(f"Camera opened (backend={backend})")
                return True
            except Exception as e:
                log.warning(f"Backend {backend} failed: {e}")
        return False

    def _loop(self):
        while self.running:
            if not self._open():
                with self.lock:
                    self.latest = self._placeholder(f"Waiting for /dev/video{CAMERA_INDEX}...")
                time.sleep(0.5)
                continue
            ok, frame = self.cap.read()
            if not ok or frame is None:
                log.warning("Camera read failed; reconnecting")
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
                with self.lock:
                    self.latest = self._placeholder("Reconnecting...")
                time.sleep(0.1)
                continue
            with self.lock:
                self.latest = frame
            self.frame_count += 1
            now = time.time()
            if now - self.last_log >= 5.0:
                fps = self.frame_count / (now - self.last_log)
                log.info(f"Camera FPS: {fps:.1f}")
                self.frame_count = 0
                self.last_log = now

    def get_frame(self) -> np.ndarray:
        with self.lock:
            return self.latest.copy()

    def stop(self):
        log.info("Stopping camera")
        self.running = False
        try:
            self.thread.join(timeout=1.0)
        except Exception:
            pass
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass


class CameraTrack(VideoStreamTrack):
    def __init__(self, camera: Camera):
        super().__init__()
        self.camera = camera

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        frame = self.camera.get_frame()
        vf = VideoFrame.from_ndarray(frame, format="bgr24")
        vf.pts = pts
        vf.time_base = time_base
        return vf


# --- Routes ---

async def index(request):
    return web.FileResponse(TEMPLATE_DIR / "index.html")


async def health(request):
    return web.json_response({
        "status": "ok",
        "camera": {"index": CAMERA_INDEX, "width": CAMERA_WIDTH, "height": CAMERA_HEIGHT, "fps": CAMERA_FPS},
        "active_pcs": len(pcs),
        "debug": DEBUG,
    })


async def offer(request):
    try:
        params = await request.json()
        log.info(f"Offer received from {request.remote}")
        offer_sdp = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
        pc = RTCPeerConnection()
        pcs.add(pc)

        @pc.on("connectionstatechange")
        async def _():
            log.info(f"PC state: {pc.connectionState}")
            if pc.connectionState in ("failed", "closed", "disconnected"):
                pcs.discard(pc)
                try:
                    await pc.close()
                except Exception:
                    pass

        pc.addTrack(CameraTrack(request.app["camera"]))
        await pc.setRemoteDescription(offer_sdp)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        if pc.iceGatheringState != "complete":
            done = asyncio.Event()

            @pc.on("icegatheringstatechange")
            def _():
                if pc.iceGatheringState == "complete":
                    done.set()
            try:
                await asyncio.wait_for(done.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                log.warning("ICE timeout, proceeding")

        return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
    except Exception as e:
        log.exception(f"Offer failed: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def on_shutdown(app):
    log.info("Shutting down")
    coros = [pc.close() for pc in pcs]
    if coros:
        await asyncio.gather(*coros, return_exceptions=True)
    pcs.clear()
    app["camera"].stop()


def build_app():
    app = web.Application()
    app["camera"] = Camera()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_post("/offer", offer)
    app.router.add_static("/static/", path=str(STATIC_DIR), name="static")
    app.on_shutdown.append(on_shutdown)
    return app


if __name__ == "__main__":
    log.info(f"Starting RPS Robot on port {PORT}")
    web.run_app(build_app(), host="0.0.0.0", port=PORT, access_log=None)
