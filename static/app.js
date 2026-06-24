/**
 * RPS Robot — Browser Client
 * - Receives video via WebRTC from Pi server
 * - Runs MediaPipe GestureRecognizer in WASM
 * - Dual classifier: model gesture + landmark fallback
 * - Lock-hold to commit moves
 * - Impossible mode (always wins)
 * - Debug overlay (toggle with 'd' key)
 */
import {
  FilesetResolver,
  GestureRecognizer
} from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@latest/vision_bundle.mjs";

const CONFIG = {
  detectIntervalMs: 70,
  detectWidth: 320,
  lockHoldMs: 180,
  releaseAfterMs: 350,
  gestureMinConfidence: 0.50,
  fingerExtendThreshold: 1.22,
};

const $ = (id) => document.getElementById(id);
const els = {
  status: $("statusPill"),
  video: $("piStream"),
  canvas: $("analysisCanvas"),
  skeleton: $("skeletonCanvas"),
  moveOverlay: $("moveOverlay"),
  detectedMove: $("detectedMove"),
  gestureDetail: $("gestureDetail"),
  userHand: $("userHand"),
  userMoveLabel: $("userMoveLabel"),
  computerHand: $("computerHand"),
  computerMoveLabel: $("computerMoveLabel"),
  playBtn: $("playBtn"),
  resetBtn: $("resetBtn"),
  modeBtn: $("modeBtn"),
  resultText: $("resultText"),
  winsCount: $("winsCount"),
  lossesCount: $("lossesCount"),
  drawsCount: $("drawsCount"),
  debugPanel: $("debugPanel"),
  dbgFps: $("dbgFps"),
  dbgInfer: $("dbgInfer"),
  dbgFingers: $("dbgFingers"),
  dbgGesture: $("dbgGesture"),
  dbgSource: $("dbgSource"),
};
const ctx = els.canvas.getContext("2d", { willReadFrequently: true });
const skelCtx = els.skeleton.getContext("2d");

const EMOJI = { rock: "✊", paper: "✋", scissors: "✌️", unknown: "❔", "no hand": "❔" };

// MediaPipe hand bone connections (21 landmarks)
const HAND_CONNECTIONS = [
  [0,1],[1,2],[2,3],[3,4],          // thumb
  [0,5],[5,6],[6,7],[7,8],          // index
  [5,9],[9,10],[10,11],[11,12],     // middle
  [9,13],[13,14],[14,15],[15,16],   // ring
  [13,17],[17,18],[18,19],[19,20],  // pinky
  [0,17]                            // palm base
];
const COUNTERS = { rock: "paper", paper: "scissors", scissors: "rock" };

const state = {
  pc: null,
  detector: null,
  detectorReady: false,
  lastInferTime: 0,
  roundBusy: false,
  currentMove: "no hand",
  lastCandidate: "no hand",
  candidateSince: 0,
  lastCleanLockAt: 0,
  lastHandSeenAt: 0,
  impossibleMode: false,
  debug: false,
  skeletonMirrored: true,  // true = matches your hand, false = AI raw view
  inferTimes: [],
  inferCount: 0,
  lastFpsUpdate: 0,
};
const score = { wins: 0, losses: 0, draws: 0 };

const log = (...a) => console.log("[RPS]", ...a);
const warn = (...a) => console.warn("[RPS]", ...a);
const err = (...a) => console.error("[RPS]", ...a);

function prettyMove(m) {
  if (m === "unknown") return "HAND SEEN";
  if (m === "no hand") return "NO HAND";
  return m.toUpperCase();
}
function setUserMove(m) {
  els.userHand.textContent = EMOJI[m] || "❔";
  els.userMoveLabel.textContent =
    m === "unknown" ? "hand seen" :
    m === "no hand" ? "show your hand" :
    prettyMove(m);
}
function setComputerMove(m) {
  els.computerHand.textContent = EMOJI[m] || "❔";
  els.computerMoveLabel.textContent = m === "no hand" ? "press play" : prettyMove(m);
}
function updateScore() {
  els.winsCount.textContent = String(score.wins);
  els.lossesCount.textContent = String(score.losses);
  els.drawsCount.textContent = String(score.draws);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function decideWinner(u, c) {
  if (u === c) return "draw";
  // COUNTERS[x] = the move that BEATS x
  // So if CPU's move beats user's move, user loses
  return COUNTERS[u] === c ? "lose" : "win";
}
function resultMessage(r, u, c) {
  if (r === "draw") return `Draw. You both picked ${prettyMove(u)}.`;
  if (r === "win")  return `You win. ${prettyMove(u)} beats ${prettyMove(c)}.`;
  return `Computer wins. ${prettyMove(c)} beats ${prettyMove(u)}.`;
}
function setMode(impossible) {
  state.impossibleMode = impossible;
  els.modeBtn.textContent = impossible ? "MODE: IMPOSSIBLE" : "MODE: FAIR";
  els.modeBtn.classList.toggle("impossible", impossible);
  log("Mode:", impossible ? "IMPOSSIBLE" : "FAIR");
}
function toggleDebug() {
  state.debug = !state.debug;
  els.debugPanel.hidden = !state.debug;
  els.skeleton.classList.toggle("visible", state.debug);
  if (!state.debug) skelCtx.clearRect(0, 0, els.skeleton.width, els.skeleton.height);
  log("Debug:", state.debug ? "on" : "off");
}

function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
function countExtendedFingers(lm) {
  const checks = [[8, 5], [12, 9], [16, 13], [20, 17]];
  const wrist = lm[0];
  let count = 0;
  const flags = [];
  for (const [tip, mcp] of checks) {
    const ext = dist(lm[tip], wrist) > dist(lm[mcp], wrist) * CONFIG.fingerExtendThreshold;
    flags.push(ext);
    if (ext) count++;
  }
  return { count, flags };
}
function classifyFromLandmarks(lm) {
  const { count, flags } = countExtendedFingers(lm);
  let move;
  if (count >= 3) move = "paper";
  else if (count === 2) move = "scissors";
  else move = "rock";
  return { move, count, flags };
}
function classify(result) {
  const lm = result?.landmarks?.[0];
  const g = result?.gestures?.[0]?.[0];
  let gestureMove = null;
  let gestureName = "";
  let gestureScore = 0;
  if (g) {
    gestureName = g.categoryName;
    gestureScore = g.score;
    if (g.score >= CONFIG.gestureMinConfidence) {
      if (g.categoryName === "Closed_Fist") gestureMove = "rock";
      else if (g.categoryName === "Open_Palm") gestureMove = "paper";
      else if (g.categoryName === "Victory") gestureMove = "scissors";
    }
  }
  if (!lm) return { move: "no hand", source: "none", gestureName, gestureScore, fingers: 0, flags: [] };
  // Reject tiny detections — real hands have a meaningful palm size
  const palmSize = dist(lm[0], lm[9]);
  if (palmSize < 0.08) {
    return { move: "no hand", source: "too-small", gestureName, gestureScore, fingers: 0, flags: [] };
  }
  const lmResult = classifyFromLandmarks(lm);
  if (gestureMove) {
    return { move: gestureMove, source: "gesture", gestureName, gestureScore, fingers: lmResult.count, flags: lmResult.flags };
  }
  return { move: lmResult.move, source: "landmarks", gestureName, gestureScore, fingers: lmResult.count, flags: lmResult.flags };
}

function waitIce(pc) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((res) => {
    const check = () => {
      if (pc.iceGatheringState === "complete") {
        pc.removeEventListener("icegatheringstatechange", check);
        res();
      }
    };
    pc.addEventListener("icegatheringstatechange", check);
  });
}
async function startWebRTC() {
  log("Starting WebRTC");
  els.status.textContent = "connecting...";
  state.pc = new RTCPeerConnection({ iceServers: [] });
  state.pc.addTransceiver("video", { direction: "recvonly" });
  state.pc.addEventListener("connectionstatechange", () => {
    const s = state.pc.connectionState;
    log("PC state:", s);
    if (s === "connected") els.status.textContent = state.detectorReady ? "ready" : "stream connected";
    else if (s === "connecting") els.status.textContent = "connecting...";
    else els.status.textContent = "disconnected";
  });
  state.pc.ontrack = (e) => {
    log("Track received");
    els.video.srcObject = e.streams[0];
    els.video.play().catch(() => {});
  };
  const offer = await state.pc.createOffer();
  await state.pc.setLocalDescription(offer);
  await waitIce(state.pc);
  const res = await fetch("/offer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sdp: state.pc.localDescription.sdp, type: state.pc.localDescription.type }),
  });
  if (!res.ok) throw new Error(`Offer failed: ${res.status}`);
  const answer = await res.json();
  await state.pc.setRemoteDescription(answer);
  await new Promise((r) => {
    if (els.video.readyState >= 2) return r();
    els.video.addEventListener("loadedmetadata", () => r(), { once: true });
  });
  await els.video.play().catch(() => {});
  log("WebRTC ready");
}

async function initDetector() {
  log("Initializing detector");
  els.gestureDetail.textContent = "loading model...";
  const vision = await FilesetResolver.forVisionTasks(
    "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@latest/wasm"
  );
  state.detector = await GestureRecognizer.createFromOptions(vision, {
    baseOptions: { modelAssetPath: "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task" },
    runningMode: "VIDEO",
    numHands: 1,
    minHandDetectionConfidence: 0.65,
    minHandPresenceConfidence: 0.65,
    minTrackingConfidence: 0.55,
  });
  state.detectorReady = true;
  els.gestureDetail.textContent = "model ready";
  if (state.pc?.connectionState === "connected") els.status.textContent = "ready";
  log("Detector ready");
  requestAnimationFrame(detectLoop);
}

function drawCanvas() {
  const sw = els.video.videoWidth, sh = els.video.videoHeight;
  if (!sw || !sh) return false;
  const ow = CONFIG.detectWidth;
  const oh = Math.round((sh / sw) * ow);
  if (els.canvas.width !== ow) els.canvas.width = ow;
  if (els.canvas.height !== oh) els.canvas.height = oh;
  ctx.save();
  ctx.translate(ow, 0);
  ctx.scale(-1, 1);
  ctx.filter = "brightness(1.05) contrast(1.05)";
  ctx.drawImage(els.video, 0, 0, ow, oh);
  ctx.restore();
  return true;
}

function lockUpdate(candidate, ts) {
  const valid = ["rock", "paper", "scissors"].includes(candidate);
  if (candidate !== "no hand") state.lastHandSeenAt = ts;
  if (candidate !== state.lastCandidate) {
    state.lastCandidate = candidate;
    state.candidateSince = ts;
  }
  const heldFor = ts - state.candidateSince;
  if (valid && heldFor >= CONFIG.lockHoldMs) {
    state.currentMove = candidate;
    state.lastCleanLockAt = ts;
    return { previewMove: state.currentMove, locked: true };
  }
  if (!valid && (ts - state.lastCleanLockAt > CONFIG.releaseAfterMs)) state.currentMove = "no hand";
  if (candidate === "no hand" && (ts - state.lastHandSeenAt > CONFIG.releaseAfterMs)) state.currentMove = "no hand";
  return {
    previewMove: valid ? candidate : (state.currentMove === "no hand" ? candidate : state.currentMove),
    locked: false,
  };
}

function updateDebug(c, inferMs) {
  if (!state.debug) return;
  state.inferTimes.push(inferMs);
  if (state.inferTimes.length > 30) state.inferTimes.shift();
  state.inferCount++;
  const now = performance.now();
  if (now - state.lastFpsUpdate >= 500) {
    const avgInfer = state.inferTimes.reduce((s, x) => s + x, 0) / state.inferTimes.length;
    const elapsed = (now - state.lastFpsUpdate) / 1000;
    const fps = state.inferCount / elapsed;
    els.dbgFps.textContent = fps.toFixed(1);
    els.dbgInfer.textContent = `${avgInfer.toFixed(0)}ms`;
    state.lastFpsUpdate = now;
    state.inferCount = 0;
  }
  els.dbgFingers.textContent = c.flags.length ? c.flags.map(f => f ? "1" : "0").join(" ") + ` (${c.fingers})` : "-";
  els.dbgGesture.textContent = c.gestureName ? `${c.gestureName} ${(c.gestureScore*100).toFixed(0)}%` : "-";
  els.dbgSource.textContent = c.source || "-";
}

function drawSkeleton(landmarks, locked, isRealHand) {
  const c = els.skeleton;
  const w = els.video.clientWidth;
  const h = els.video.clientHeight;
  if (c.width !== w) c.width = w;
  if (c.height !== h) c.height = h;
  skelCtx.clearRect(0, 0, w, h);
  if (!landmarks || !state.debug || !isRealHand) return;

  const color = locked ? "#00ff88" : "#ffffff";
  const glow = locked ? "rgba(0,255,136,0.6)" : "rgba(255,255,255,0.4)";

  // Toggle: mirrored matches user's hand on flipped video; raw shows what AI sees
  const mx = state.skeletonMirrored ? (x) => (1 - x) * w : (x) => x * w;
  const my = (y) => y * h;

  skelCtx.strokeStyle = color;
  skelCtx.lineWidth = 3;
  skelCtx.lineCap = "round";
  skelCtx.shadowColor = glow;
  skelCtx.shadowBlur = 8;
  for (const [a, b] of HAND_CONNECTIONS) {
    const p1 = landmarks[a], p2 = landmarks[b];
    skelCtx.beginPath();
    skelCtx.moveTo(mx(p1.x), my(p1.y));
    skelCtx.lineTo(mx(p2.x), my(p2.y));
    skelCtx.stroke();
  }

  skelCtx.fillStyle = color;
  skelCtx.shadowBlur = 12;
  for (const lm of landmarks) {
    skelCtx.beginPath();
    skelCtx.arc(mx(lm.x), my(lm.y), 5, 0, Math.PI * 2);
    skelCtx.fill();
  }
  skelCtx.shadowBlur = 0;
}

function detectLoop(ts) {
  requestAnimationFrame(detectLoop);
  if (!state.detectorReady) return;
  if (!els.video.videoWidth) return;
  if (ts - state.lastInferTime < CONFIG.detectIntervalMs) return;
  state.lastInferTime = ts;
  try {
    if (!drawCanvas()) return;
    const inferStart = performance.now();
    const result = state.detector.recognizeForVideo(els.canvas, performance.now());
    const inferMs = performance.now() - inferStart;
    const c = classify(result);
    const ls = lockUpdate(c.move, ts);
    const isRealHand = c.source !== "none" && c.source !== "too-small";
    drawSkeleton(result?.landmarks?.[0], ls.locked, isRealHand);
    els.detectedMove.textContent = prettyMove(ls.previewMove);
    els.moveOverlay.classList.toggle("locked", ls.locked);
    if (ls.locked) {
      els.gestureDetail.textContent = `${prettyMove(state.currentMove)} LOCKED`;
    } else if (c.move === "no hand") {
      els.gestureDetail.textContent = "show your hand";
    } else {
      els.gestureDetail.textContent = `${c.fingers} fingers extended`;
    }
    if (!state.roundBusy) setUserMove(state.currentMove);
    updateDebug(c, inferMs);
  } catch (e) {
    err("detectLoop:", e);
    els.gestureDetail.textContent = "error";
  }
}

async function animateComputer(finalMove) {
  const seq = ["rock", "paper", "scissors", "rock", "paper", "scissors", finalMove];
  for (const m of seq) {
    els.computerHand.textContent = EMOJI[m];
    els.computerMoveLabel.textContent = prettyMove(m);
    els.computerHand.classList.remove("bounce");
    void els.computerHand.offsetWidth;
    els.computerHand.classList.add("bounce");
    await sleep(150);
  }
}

async function playRound() {
  if (state.roundBusy) return;
  if (!["rock", "paper", "scissors"].includes(state.currentMove)) {
    els.resultText.textContent = "hold a clean rock, paper, or scissors pose first";
    return;
  }
  // Require fresh lock - prevents cheating with stale move
  const sinceLock = performance.now() - state.lastCleanLockAt;
  if (sinceLock > 600) {
    els.resultText.textContent = "hold your move steady, then press play";
    return;
  }
  state.roundBusy = true;
  els.playBtn.disabled = true;
  const userMove = state.currentMove;
  const computerMove = state.impossibleMode
    ? COUNTERS[userMove]
    : ["rock", "paper", "scissors"][Math.floor(Math.random() * 3)];
  log(`Round: user=${userMove} cpu=${computerMove} impossible=${state.impossibleMode}`);
  setUserMove(userMove);
  els.resultText.textContent = "rock... paper... scissors...";
  await animateComputer(computerMove);
  const r = decideWinner(userMove, computerMove);
  if (r === "win") score.wins++;
  if (r === "lose") score.losses++;
  if (r === "draw") score.draws++;
  updateScore();
  els.resultText.textContent = resultMessage(r, userMove, computerMove);
  // Flash the move overlay based on result
  els.moveOverlay.classList.remove("flash-win", "flash-lose");
  void els.moveOverlay.offsetWidth;
  if (r === "win") els.moveOverlay.classList.add("flash-win");
  else if (r === "lose") els.moveOverlay.classList.add("flash-lose");
  els.playBtn.disabled = false;
  state.roundBusy = false;
}

function resetScore() {
  score.wins = 0; score.losses = 0; score.draws = 0;
  updateScore();
  els.resultText.textContent = "score reset";
  setComputerMove("no hand");
  log("Score reset");
}

els.playBtn.addEventListener("click", playRound);
els.resetBtn.addEventListener("click", resetScore);
els.modeBtn.addEventListener("click", () => setMode(!state.impossibleMode));
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  if (e.key === "i" || e.key === "I") setMode(!state.impossibleMode);
  if (e.key === "d" || e.key === "D") toggleDebug();
  if (e.key === "v" || e.key === "V") {
    state.skeletonMirrored = !state.skeletonMirrored;
    log("Skeleton view:", state.skeletonMirrored ? "MIRRORED (matches you)" : "RAW (AI view)");
  }
  if (e.key === " " || e.key === "Enter") {
    if (!els.playBtn.disabled) { e.preventDefault(); playRound(); }
  }
});

async function boot() {
  setUserMove("no hand");
  setComputerMove("no hand");
  setMode(false);
  updateScore();
  log("Booting RPS Robot");
  log("Hotkeys: [i] impossible | [d] debug | [v] flip skeleton | [space] play");
  try {
    await startWebRTC();
    await initDetector();
  } catch (e) {
    err("Boot failed:", e);
    els.status.textContent = "failed";
    els.gestureDetail.textContent = "check pi server";
    els.resultText.textContent = "could not start stream";
  }
}
boot();
