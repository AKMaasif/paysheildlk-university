"use client";

import { useEffect, useRef, useState } from "react";

export type Challenge = { token: string; steps: string[]; instructions: string[] } | null;
export type Capture = { frames: string[]; offsets_ms: number[]; challenge: string };

const STEP_MS = 2400;
const LEAD_MS = 700;
const MAX_FRAMES = 20;

/** The camera, the instructions, and a short burst of frames timed against them.
 * With a challenge it records about five seconds while the person follows two random instructions.
 * Without one (the demo setting) it takes two frames, as the first version did. */
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
  const [instruction, setInstruction] = useState("");
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
    const began = performance.now();
    if (!challenge || challenge.steps.length === 0) {
      setInstruction("Look at the camera");
      for (let i = 0; i < 6; i++) {
        frames.push(grab());
        offsets.push(Math.round(performance.now() - began));
        await new Promise((r) => setTimeout(r, 400));
        setProgress((i + 1) / 6);
      }
    } else {
      const total = LEAD_MS + STEP_MS * challenge.steps.length;
      const gap = total / (MAX_FRAMES - 1);
      setInstruction("Look straight at the camera");
      for (let i = 0; i < MAX_FRAMES; i++) {
        const at = performance.now() - began;
        const stepIndex = Math.floor((at - LEAD_MS) / STEP_MS);
        if (at >= LEAD_MS && stepIndex < challenge.instructions.length) setInstruction(challenge.instructions[Math.max(0, stepIndex)]);
        frames.push(grab());
        offsets.push(Math.round(at));
        setProgress((i + 1) / MAX_FRAMES);
        await new Promise((r) => setTimeout(r, Math.max(0, gap - (performance.now() - began - at))));
      }
    }
    setRunning(false);
    setInstruction("");
    setProgress(0);
    onCapture({ frames, offsets_ms: offsets, challenge: challenge?.token || "" });
  }

  return (
    <div>
      <div className="cs-camera">
        {cameraError ? <div className="off">{cameraError}</div> : <video ref={video} autoPlay playsInline muted />}
        {!cameraError && <div className="ring" />}
        {(running || instruction) && <div className="hint" style={{ fontSize: 16, fontWeight: 600 }}>{instruction}</div>}
        {running && <div style={{ position: "absolute", left: 0, bottom: 0, height: 4, width: `${progress * 100}%`, background: "var(--cs-mint)" }} />}
      </div>
      {hints && hints.length > 0 && !running && (
        <ul className="cs-hints">
          {hints.map((h) => <li key={h}>{h}</li>)}
        </ul>
      )}
      {challenge && challenge.instructions.length > 0 && !running && (
        <p style={{ color: "var(--cs-muted)", fontSize: 14.5, margin: "12px 0 0" }}>
          You will be asked to: <b>{challenge.instructions.join(", then ").toLowerCase()}</b>. Keep your face in the oval and your eyes open. The instructions change every time.
        </p>
      )}
      <button className="cs-btn primary block" style={{ marginTop: 14 }} type="button" onClick={start} disabled={running || busy || !!cameraError}>
        {running ? "Scanning, keep going" : busy ? "Checking" : label}
      </button>
    </div>
  );
}
