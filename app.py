"""
RPS-Robot — Raspberry Pi streaming server.

Streams a USB camera over WebRTC to a browser client. The browser runs the
MediaPipe gesture recognizer and all of the game logic; the Pi only captures
frames and pushes them down the wire.

The server also supports a hardware-free **demo mode** (``RPS_DEMO_MODE=true``)
that streams a synthetic animated feed instead of a real camera, so the project
can be run and demonstrated on any laptop without Raspberry Pi hardware.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
TEMPLATE_DIR = ROOT / "templates"

# --------------------------------------------------------------------------- #
# Tunable constants (intentionally not exposed as env vars to keep config small)
# --------------------------------------------------------------------------- #
FPS_LOG_INTERVAL_S = 5.0          # how often to log measured capture FPS
CAMERA_RETRY_DELAY_S = 0.5        # wait between attempts to (re)open the camera
CAMERA_RECONNECT_DELAY_S = 0.1    # short pause after a failed frame read
ICE_GATHERING_TIMEOUT_S = 5.0     # max wait for ICE candidate gathering

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rps")


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def _env_bool(name: str, default: bool = False) -> bool:
    """Read a truthy/falsy environment variable (1/true/yes/on)."""
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """Runtime configuration, loaded from environment variables."""

    camera_index: int = 0
    camera_width: int = 480
    camera_height: int = 360
    camera_fps: int = 25
    host: str = "0.0.0.0"
    port: int = 5000
    debug: bool = False
    demo_mode: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            camera_index=int(os.getenv("CAMERA_INDEX", "0")),
            camera_width=int(os.getenv("CAMERA_WIDTH", "480")),
            camera_height=int(os.getenv("CAMERA_HEIGHT", "360")),
            camera_fps=int(os.getenv("CAMERA_FPS", "25")),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "5000")),
            debug=_env_bool("DEBUG", False),
            demo_mode=_env_bool("RPS_DEMO_MODE", False),
        )


# --------------------------------------------------------------------------- #
# Frame helpers
# --------------------------------------------------------------------------- #
def make_placeholder(config: Config, text: str) -> np.ndarray:
    """Return a solid frame with a centered status message."""
    frame = np.zeros((config.camera_height, config.camera_width, 3), dtype=np.uint8)
    cv2.putText(
        frame, text, (15, config.camera_height // 2),
        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA,
    )
    return frame


def make_demo_frame(config: Config, elapsed: float) -> np.ndarray:
    """
    Render an animated placeholder frame for demo mode.

    Shows the project name, a "demo mode" notice, a cycling rock/paper/scissors
    label, and a moving accent bar so it is obviously a live stream rather than
    a frozen image.
    """
    w, h = config.camera_width, config.camera_height
    frame = np.full((h, w, 3), 18, dtype=np.uint8)  # dark background

    moves = ["ROCK", "PAPER", "SCISSORS"]
    move = moves[int(elapsed) % len(moves)]

    cv2.putText(frame, "RPS-ROBOT", (int(w * 0.06), int(h * 0.30)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 136), 2, cv2.LINE_AA)
    cv2.putText(frame, "DEMO MODE - no camera", (int(w * 0.06), int(h * 0.45)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
    cv2.putText(frame, move, (int(w * 0.06), int(h * 0.70)),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3, cv2.LINE_AA)

    # Moving accent bar along the bottom edge.
    bar_w = max(40, w // 6)
    x = int((elapsed * 120) % (w + bar_w)) - bar_w
    cv2.rectangle(frame, (x, h - 8), (x + bar_w, h), (0, 255, 136), -1)
    return frame


# --------------------------------------------------------------------------- #
# Video sources
# --------------------------------------------------------------------------- #
class VideoSource:
    """
    Base class for a threaded frame producer.

    Subclasses implement :meth:`_run` to continuously publish frames via
    :meth:`_publish`. The most recent frame is always available through
    :meth:`get_frame`.
    """

    name = "video"

    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()
        self._latest = make_placeholder(config, "Starting...")
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name=self.name)
        self._thread.start()
        log.info("%s source started", self.name)

    def _run(self) -> None:  # pragma: no cover - implemented by subclasses
        raise NotImplementedError

    def _publish(self, frame: np.ndarray) -> None:
        with self._lock:
            self._latest = frame

    def get_frame(self) -> np.ndarray:
        with self._lock:
            return self._latest.copy()

    def _cleanup(self) -> None:
        """Release any held resources. Overridden by subclasses as needed."""

    def stop(self) -> None:
        log.info("Stopping %s source", self.name)
        self._running = False
        try:
            self._thread.join(timeout=1.0)
        except Exception:  # pragma: no cover - best effort shutdown
            pass
        self._cleanup()


class CameraSource(VideoSource):
    """Reads a real USB camera with V4L2 and republishes the latest frame."""

    name = "camera"

    def __init__(self, config: Config):
        self._cap: cv2.VideoCapture | None = None
        self._frame_count = 0
        self._last_log = time.time()
        super().__init__(config)

    def _open(self) -> bool:
        if self._cap and self._cap.isOpened():
            return True
        for backend in (cv2.CAP_V4L2, cv2.CAP_ANY):
            try:
                cap = cv2.VideoCapture(self.config.camera_index, backend)
                if not cap.isOpened():
                    cap.release()
                    continue
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.camera_width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.camera_height)
                cap.set(cv2.CAP_PROP_FPS, self.config.camera_fps)
                ok, _ = cap.read()
                if not ok:
                    cap.release()
                    continue
                self._cap = cap
                log.info("Camera opened (backend=%s)", backend)
                return True
            except Exception as exc:
                log.warning("Camera backend %s failed: %s", backend, exc)
        return False

    def _run(self) -> None:
        while self._running:
            if not self._open():
                self._publish(make_placeholder(
                    self.config, f"Waiting for /dev/video{self.config.camera_index}..."))
                time.sleep(CAMERA_RETRY_DELAY_S)
                continue

            ok, frame = self._cap.read()
            if not ok or frame is None:
                log.warning("Camera read failed; reconnecting")
                self._release_capture()
                self._publish(make_placeholder(self.config, "Reconnecting..."))
                time.sleep(CAMERA_RECONNECT_DELAY_S)
                continue

            self._publish(frame)
            self._track_fps()

    def _track_fps(self) -> None:
        self._frame_count += 1
        now = time.time()
        if now - self._last_log >= FPS_LOG_INTERVAL_S:
            fps = self._frame_count / (now - self._last_log)
            log.info("Camera FPS: %.1f", fps)
            self._frame_count = 0
            self._last_log = now

    def _release_capture(self) -> None:
        if self._cap:
            try:
                self._cap.release()
            except Exception:  # pragma: no cover - best effort
                pass
        self._cap = None

    def _cleanup(self) -> None:
        self._release_capture()


class DemoSource(VideoSource):
    """Produces a synthetic animated feed so the app runs without hardware."""

    name = "demo"

    def _run(self) -> None:
        frame_delay = 1.0 / max(1, self.config.camera_fps)
        start = time.time()
        while self._running:
            self._publish(make_demo_frame(self.config, time.time() - start))
            time.sleep(frame_delay)


def create_video_source(config: Config) -> VideoSource:
    """Build the appropriate video source for the current configuration."""
    if config.demo_mode:
        log.info("RPS_DEMO_MODE enabled — using synthetic demo feed")
        return DemoSource(config)
    return CameraSource(config)


class CameraTrack(VideoStreamTrack):
    """WebRTC video track that pulls frames from a :class:`VideoSource`."""

    def __init__(self, source: VideoSource):
        super().__init__()
        self.source = source

    async def recv(self) -> VideoFrame:
        pts, time_base = await self.next_timestamp()
        frame = VideoFrame.from_ndarray(self.source.get_frame(), format="bgr24")
        frame.pts = pts
        frame.time_base = time_base
        return frame


# --------------------------------------------------------------------------- #
# HTTP / WebRTC routes
# --------------------------------------------------------------------------- #
async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(TEMPLATE_DIR / "index.html")


async def health(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    return web.json_response({
        "status": "ok",
        "demo": config.demo_mode,
        "camera": {
            "index": config.camera_index,
            "width": config.camera_width,
            "height": config.camera_height,
            "fps": config.camera_fps,
        },
        "active_connections": len(request.app["pcs"]),
        "debug": config.debug,
    })


async def offer(request: web.Request) -> web.Response:
    pcs: set[RTCPeerConnection] = request.app["pcs"]
    try:
        params = await request.json()
        log.info("Offer received from %s", request.remote)
        offer_sdp = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        pc = RTCPeerConnection()
        pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_state_change():
            log.info("Peer connection state: %s", pc.connectionState)
            if pc.connectionState in ("failed", "closed", "disconnected"):
                pcs.discard(pc)
                try:
                    await pc.close()
                except Exception:  # pragma: no cover - best effort
                    pass

        pc.addTrack(CameraTrack(request.app["video_source"]))
        await pc.setRemoteDescription(offer_sdp)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await _wait_for_ice(pc)

        return web.json_response({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
        })
    except Exception as exc:
        log.exception("Offer handling failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def _wait_for_ice(pc: RTCPeerConnection) -> None:
    """Wait for ICE gathering to complete, with a bounded timeout."""
    if pc.iceGatheringState == "complete":
        return
    done = asyncio.Event()

    @pc.on("icegatheringstatechange")
    def on_ice_change():
        if pc.iceGatheringState == "complete":
            done.set()

    try:
        await asyncio.wait_for(done.wait(), timeout=ICE_GATHERING_TIMEOUT_S)
    except asyncio.TimeoutError:
        log.warning("ICE gathering timed out; proceeding with partial candidates")


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
async def on_shutdown(app: web.Application) -> None:
    log.info("Shutting down")
    coros = [pc.close() for pc in app["pcs"]]
    if coros:
        await asyncio.gather(*coros, return_exceptions=True)
    app["pcs"].clear()
    app["video_source"].stop()


def build_app(config: Config | None = None) -> web.Application:
    config = config or Config.from_env()
    log.setLevel(logging.DEBUG if config.debug else logging.INFO)
    log.info(
        "Config: demo=%s camera=%s %dx%d@%dfps port=%d debug=%s",
        config.demo_mode, config.camera_index, config.camera_width,
        config.camera_height, config.camera_fps, config.port, config.debug,
    )

    app = web.Application()
    app["config"] = config
    app["pcs"] = set()
    app["video_source"] = create_video_source(config)

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_post("/offer", offer)
    app.router.add_static("/static/", path=str(STATIC_DIR), name="static")
    app.on_shutdown.append(on_shutdown)
    return app


def main() -> None:
    config = Config.from_env()
    app = build_app(config)
    log.info("Starting RPS-Robot on http://%s:%d", config.host, config.port)
    web.run_app(app, host=config.host, port=config.port, access_log=None)


if __name__ == "__main__":
    main()
