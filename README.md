# RPS-Robot

A real-time Rock Paper Scissors game you play with your bare hand in front of a Raspberry Pi camera — the Pi streams video to the browser, and the browser recognizes your gesture and plays against you.

> Built as a senior project at Laingsburg High School, 2026. Scored 185/200.

---

## What it does

Show your hand to a USB camera connected to a Raspberry Pi 5. The Pi streams the
live video to a web browser over WebRTC, where a hand-tracking model classifies
your pose as **rock**, **paper**, or **scissors**. Press play and the computer
makes its move, scores the round, and keeps a running tally — all in real time.

It also has an unbeatable **"impossible" mode** (the computer always counters
your move) and a debug overlay that draws the detected hand skeleton on top of
the video.

## Why I built it

I wanted a hands-on project that combined embedded hardware (Raspberry Pi 5 +
camera) with real-time computer vision, and that ended up as something fun and
visual rather than just a script in a terminal. Streaming the video off the Pi
and running inference in the browser keeps the Pi light and makes the whole
thing easy to demo from any device on the network.

## Senior project background

RPS-Robot started as my 2026 senior project at Laingsburg High School. I wanted
to build something more complete than a simple assignment, so I combined
Raspberry Pi hardware, real-time video streaming, browser-based hand tracking,
and a custom web interface into one playable system. The project scored 185/200
and became one of the first hardware-focused projects in my portfolio.

## Key features

- **Real-time hand-gesture recognition** — rock / paper / scissors from a live feed.
- **Low-latency WebRTC streaming** from the Raspberry Pi to the browser.
- **Browser-side inference** using MediaPipe, so the Pi only has to capture and stream.
- **Dual classifier** — a trained gesture model with a finger-counting landmark fallback for robustness.
- **Move "lock" logic** so a pose has to be held briefly before it counts (no accidental moves).
- **Impossible mode** — the computer always wins, for fun.
- **Debug overlay** — live FPS, inference time, and a hand-skeleton drawing.
- **Demo mode** — run the whole UI on a laptop with no Raspberry Pi or camera required.

## Tech stack

| Layer    | Tools |
|----------|-------|
| Server   | Python, aiohttp, aiortc (WebRTC), PyAV, OpenCV |
| Client   | Vanilla JavaScript, MediaPipe Tasks Vision, WebRTC, Canvas |
| Hardware | Raspberry Pi 5, USB camera |

## Hardware used

- **Raspberry Pi 5** running 64-bit Raspberry Pi OS.
- A **USB webcam** exposed at `/dev/video0` (configurable).
- Any device with a modern browser to play on (the Pi itself, a phone, or a laptop).

## How it works

```
USB camera ──► Pi (app.py) ──WebRTC video──► Browser (app.js)
                 capture                        MediaPipe gesture recognition
                 + encode                       + game logic + scoring + UI
```

1. `app.py` reads frames from the USB camera in a background thread and exposes
   the latest frame to a WebRTC video track.
2. The browser opens a WebRTC connection (signaled over a simple `/offer`
   endpoint) and receives the live stream.
3. The browser runs MediaPipe's gesture recognizer on each frame, classifies
   the move, and handles the game loop, scoring, and overlays.

Keeping inference in the browser means the Pi stays responsive and you can play
from whatever device is most convenient.

## Setup

```bash
git clone https://github.com/DawsonCodes/RPS-Robot.git
cd RPS-Robot
./setup.sh
```

`setup.sh` creates a virtual environment in `.venv` and installs the Python
dependencies. On a Raspberry Pi it also installs `ffmpeg` and `v4l-utils`.

> **Note:** On a Raspberry Pi you can optionally use the system OpenCV package
> (`sudo apt install python3-opencv`) for better hardware acceleration instead
> of the pip `opencv-python-headless` wheel.

Copy `.env.example` to `.env` if you want to customize the camera index, port,
resolution, or other settings.

## Running it

**Normal mode (Raspberry Pi + camera):**

```bash
./run.sh
```

Then open `http://<pi-ip-address>:5000` in a browser.

### Hotkeys

| Key     | Action |
|---------|--------|
| `Space` | Play a round |
| `I`     | Toggle impossible mode |
| `D`     | Toggle debug overlay + hand skeleton |
| `V`     | Flip the skeleton view (mirrored vs. raw) |

## Demo mode (no hardware required)

You can run the whole thing on a normal laptop or desktop without a Raspberry
Pi or camera. In demo mode the server streams a synthetic animated feed, and
pressing **Play** generates a random round, so recruiters can see the full UI
and game flow:

```bash
RPS_DEMO_MODE=true ./run.sh
```

Then open `http://localhost:5000`.

> In demo mode there's no real hand to detect, so "your move" is chosen
> randomly each round. Everything else — the UI, scoring, impossible mode,
> and the live-feed streaming pipeline — works exactly as it does on the Pi.

## Project structure

```
RPS-Robot/
├── app.py                  # Pi server: camera capture + WebRTC streaming
├── requirements.txt        # Python dependencies
├── setup.sh                # creates the venv and installs dependencies
├── run.sh                  # starts the server (supports RPS_DEMO_MODE)
├── .env.example            # documented configuration options
├── docs/                   # senior project presentation (PDF)
├── scripts/
│   └── cleanup_old_instances.sh
├── static/
│   ├── app.js              # browser client: gesture recognition + game logic
│   └── style.css
└── templates/
    └── index.html          # game UI
```

## Screenshots / demo

<!-- TODO: add screenshots or a short GIF of the game in action here. -->
- [View the senior project presentation](docs/RPS-Robot-Senior-Project.pdf)

_Screenshots and a demo clip will be added here._

## Future improvements

- Best-of-N match mode with a round timer.
- On-device inference option for fully offline play.
- Persisting scores / a simple match history.
- Multiple-hand and two-player support.

## License

© 2026 DawsonCodes. Released under the [MIT License](LICENSE).
