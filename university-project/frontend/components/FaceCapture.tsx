"use client";

import { useEffect, useRef, useState } from "react";

export type Challenge = { token: string; steps: string[]; instructions: string[] } | null;
export type Capture = { frames: string[]; offsets_ms: number[]; challenge: string };

// Timing, in milliseconds. The person gets a countdown, then a moment to look straight (the server compares every turn
// with this first stretch), then a slow, clearly marked move. Each move has two parts: the movement itself, then coming
// back (head) or closing (mouth). The server checks the same things as before; it is the pace that changed.
const COUNTDOWN_S = 3;
const LEAD_MS = 2500;
const STEP_MS = 5000;
const FIRST_PART = 0.7;
const MAX_FRAMES = 20; // the server accepts at most 20 frames

type Icon = "straight" | "left" | "right" | "open" | "close" | "centre";
type Phase = { text: string; icon: Icon; move: number };

function parts(step: string): [string, Icon, string, Icon] {
  switch (step) {
    case "turn_left":
      return ["Slowly turn your head to your LEFT", "left", "Good. Now come back to the middle", "centre"];
    case "turn_right":
      return ["Slowly turn your head to your RIGHT", "right", "Good. Now come back to the middle", "centre"];
    case "open_mouth":
      return ["Open your mouth wide and hold it", "open", "Now close your mouth", "close"];
    default:
      return ["Follow the instruction", "straight", "Hold still", "straight"];
  }
}

function phaseAt(elapsed: number, steps: string[]): Phase {
  if (elapsed < LEAD_MS) return { text: "Look straight at the camera and hold still", icon: "straight", move: 0 };
  const into = elapsed - LEAD_MS;
  const index = Math.min(steps.length - 1, Math.floor(into / STEP_MS));
  const within = Math.min(1, (into - index * STEP_MS) / STEP_MS);
  const [first, firstIcon, second, secondIcon] = parts(steps[index]);
  return within < FIRST_PART ? { text: first, icon: firstIcon, move: index + 1 } : { text: second, icon: secondIcon, move: index + 1 };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function Cue({ icon }: { icon: Icon }) {
  if (icon === "left" || icon === "right") {
    return (
      <div className={`cs-cue ${icon}`} aria-hidden="true">
        <svg viewBox="0 0 64 64" width="84" height="84">
          <path d={icon === "left" ? "M40 8 L16 32 L40 56" : "M24 8 L48 32 L24 56"} fill="none" stroke="#fff" strokeWidth="9" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
    );
  }
  if (icon === "open" || icon === "close") {
    return (
      <div className="cs-cue mouth" aria-hidden="true">
        <svg viewBox="0 0 64 40" width="96" height="60">
          {icon === "open" ? <ellipse cx="32" cy="20" rx="18" ry="14" fill="rgba(0,0,0,0.55)" stroke="#fff" strokeWidth="5" /> : <path d="M10 20 Q32 30 54 20" fill="none" stroke="#fff" strokeWidth="6" strokeLinecap="round" />}
        </svg>
      </div>
    );
  }
  return (
    <div className="cs-cue centre" aria-hidden="true">
      <svg viewBox="0 0 64 64" width="56" height="56">
        <circle cx="32" cy="32" r="8" fill="#fff" />
        <circle cx="32" cy="32" r="20" fill="none" stroke="#fff" strokeWidth="4" />
      </svg>
    </div>
  );
}

/** The camera, the instructions, and a short burst of frames timed against them.
 * With a challenge it records about thirteen seconds while the person follows two slow, clearly marked moves.
 * Without one (the demo setting) it takes six frames, as the first version did. */
export function FaceCapture({
  challenge,
  busy,
  hints,
  onCapture,
  label = "Start face scan",
}: {
  challenge: Challenge;
  busy?: boolean;
  hints?: string[];
  onCapture: (capture: Capture) => void;
  label?: string;
}) {
  const video = useRef<HTMLVideoElement | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const [cameraError, setCameraError] = useState("");
  const [running, setRunning] = useState(false);
  const [ready, setReady] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const [phase, setPhase] = useState<Phase | null>(null);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    let cancelled = false;
    navigator.mediaDevices
      ?.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } }, audio: false })
      .then((media) => {
        if (cancelled) return media.getTracks().forEach((t) => t.stop());
        stream.current = media;
        if (video.current) {
          video.current.srcObject = media;
          video.current.play().catch(() => undefined);
        }
      })
      .catch(() => setCameraError("The camera could not be opened. Allow camera access in your browser, then try again."));
    return () => {
      cancelled = true;
      stream.current?.getTracks().forEach((t) => t.stop());
      stream.current = null;
    };
  }, []);

  function grab(): string {
    const el = video.current!;
    const canvas = document.createElement("canvas");
    canvas.width = el.videoWidth || 640;
    canvas.height = el.videoHeight || 480;
    canvas.getContext("2d")?.drawImage(el, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.85).split(",")[1];
  }

  async function start() {
    if (!video.current || !stream.current) return;
    setRunning(true);
    const frames: string[] = [];
    const offsets: number[] = [];
    const steps = challenge?.steps || [];

    if (steps.length === 0) {
      setPhase({ text: "Look at the camera", icon: "straight", move: 0 });
      const began = performance.now();
      for (let i = 0; i < 6; i++) {
        frames.push(grab());
        offsets.push(Math.round(performance.now() - began));
        await sleep(400);
        setProgress((i + 1) / 6);
      }
    } else {
      // Get ready. Nothing is recorded yet.
      for (let n = COUNTDOWN_S; n >= 1; n--) {
        setCountdown(n);
        await sleep(1000);
      }
      setCountdown(0);
      const total = LEAD_MS + STEP_MS * steps.length;
      const began = performance.now();
      // The words and the arrow follow the clock, not the frames, so they change exactly on time.
      const ticker = window.setInterval(() => setPhase(phaseAt(performance.now() - began, steps)), 100);
      setPhase(phaseAt(0, steps));
      const gap = total / (MAX_FRAMES - 1);
      for (let i = 0; i < MAX_FRAMES; i++) {
        const target = i * gap;
        const wait = target - (performance.now() - began);
        if (wait > 0) await sleep(wait);
        const at = performance.now() - began;
        frames.push(grab());
        offsets.push(Math.round(at));
        setProgress((i + 1) / MAX_FRAMES);
      }
      window.clearInterval(ticker);
    }
    setRunning(false);
    setPhase(null);
    setProgress(0);
    onCapture({ frames, offsets_ms: offsets, challenge: challenge?.token || "" });
  }

  const steps = challenge?.steps || [];
  return (
    <div>
      <div className="cs-camera">
        {cameraError ? <div className="off">{cameraError}</div> : <video ref={video} autoPlay playsInline muted onPlaying={() => setReady(true)} />}
        {!cameraError && <div className="ring" />}
        {running && countdown > 0 && (
          <div className="cs-countdown" role="status" aria-live="assertive">
            <div className="n">{countdown}</div>
            <div>Get ready. Look straight at the camera.</div>
          </div>
        )}
        {running && countdown === 0 && phase && <Cue icon={phase.icon} />}
        {running && countdown === 0 && phase && (
          <div className="cs-move" aria-hidden="true">Step {phase.move + 1} of {steps.length + 1}</div>
        )}
        {running && countdown === 0 && phase && (
          <div className="hint" role="status" aria-live="assertive" style={{ fontSize: 17, fontWeight: 600 }}>{phase.text}</div>
        )}
        {running && <div style={{ position: "absolute", left: 0, bottom: 0, height: 4, width: `${progress * 100}%`, background: "var(--cs-mint)" }} />}
      </div>
      {hints && hints.length > 0 && !running && (
        <ul className="cs-hints">
          {hints.map((h) => <li key={h}>{h}</li>)}
        </ul>
      )}
      {challenge && challenge.instructions.length > 0 && !running && (
        <div style={{ color: "var(--cs-muted)", fontSize: 14.5, margin: "12px 0 0" }}>
          <p style={{ margin: 0 }}>
            The scan takes about {Math.round(COUNTDOWN_S + (LEAD_MS + STEP_MS * steps.length) / 1000)} seconds. It starts with a countdown, then you make <b>{steps.length} slow moves</b>:{" "}
            <b>{challenge.instructions.map((text) => text.toLowerCase()).join(", then ")}</b>.
          </p>
          <p style={{ margin: "6px 0 0" }}>
            The scan has three steps: look straight, then turn to your left, then turn to your right. An arrow on the picture shows which way to go. Move only as far as is comfortable and keep your eyes open.
          </p>
        </div>
      )}
      <button className="cs-btn primary block" style={{ marginTop: 14 }} type="button" onClick={start} disabled={running || busy || !ready || !!cameraError}>
        {running ? "Scanning, follow the arrow" : busy ? "Checking" : !ready && !cameraError ? "Starting the camera" : label}
      </button>
    </div>
  );
}
