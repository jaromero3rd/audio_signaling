#!/usr/bin/env python3
"""Record webcam video of the wheat poster for labeling / retraining.

Saves under data/poster_capture/session_*/:
  - poster.mp4 (or .avi)
  - frames/frame_*.jpg (press s, or --auto-frames)

Keys work in EITHER the OpenCV window OR this terminal:
  r  start/stop recording
  s  save one still
  q / Esc / Ctrl+C  quit (always finalizes the file)

Example:
  python -m wheat_detection.record_poster
  python -m wheat_detection.record_poster --camera 0 --auto-frames 2
"""

from __future__ import annotations

import argparse
import select
import sys
import termios
import time
import tty
from datetime import datetime
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "data" / "poster_capture"


def _open_writer(path: Path, fps: float, size: tuple[int, int]) -> tuple[cv2.VideoWriter, Path] | None:
    w, h = size
    for fourcc, suffix in (("mp4v", ".mp4"), ("XVID", ".avi"), ("MJPG", ".avi")):
        out = path.with_suffix(suffix)
        writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*fourcc), fps, (w, h))
        if writer.isOpened():
            return writer, out
        writer.release()
    return None


class _TermKeys:
    """Non-blocking single-char reads from the controlling terminal."""

    def __init__(self) -> None:
        self._fd = None
        self._old = None
        if not sys.stdin.isatty():
            return
        try:
            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        except (termios.error, OSError):
            self._fd = None
            self._old = None

    def poll(self) -> str | None:
        if self._fd is None:
            return None
        try:
            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if not ready:
                return None
            ch = sys.stdin.read(1)
            return ch
        except (OSError, ValueError):
            return None

    def close(self) -> None:
        if self._fd is not None and self._old is not None:
            try:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
            except termios.error:
                pass


def main() -> int:
    p = argparse.ArgumentParser(description="Record wheat-poster webcam video for training")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--fps", type=float, default=15.0)
    p.add_argument(
        "--auto-frames",
        type=float,
        default=0.0,
        help="also dump a still every N seconds while recording (0 = off)",
    )
    p.add_argument(
        "--seconds",
        type=float,
        default=0.0,
        help="auto-start record and stop after N seconds (0 = manual r/q)",
    )
    p.add_argument("--out", type=Path, default=OUT_ROOT)
    args = p.parse_args()

    session = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.out / f"session_{session}"
    frames_dir = out_dir / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"Could not open webcam index {args.camera}")
        return 1

    ok, frame = cap.read()
    if not ok or frame is None:
        print("No frames from webcam")
        return 1
    h, w = frame.shape[:2]
    print(f"Webcam {args.camera}: {w}x{h}")
    print(f"Output: {out_dir}")
    print("Type r/s/q in THIS terminal (or click the video window first). Ctrl+C also quits.")

    writer: cv2.VideoWriter | None = None
    video_file: Path | None = None
    recording = False
    n_frames = 0
    n_stills = 0
    last_auto = 0.0
    video_stem = out_dir / "poster"
    started_at = 0.0
    term = _TermKeys()

    def start_rec() -> None:
        nonlocal writer, video_file, recording, n_frames, last_auto, started_at
        opened = _open_writer(video_stem, args.fps, (w, h))
        if opened is None:
            print("Failed to open VideoWriter")
            return
        writer, video_file = opened
        recording = True
        n_frames = 0
        last_auto = time.time()
        started_at = last_auto
        print(f"RECORDING → {video_file}  (q or Ctrl+C to stop & save)")

    def stop_rec() -> None:
        nonlocal writer, recording
        if writer is not None:
            writer.release()
            writer = None
        if recording:
            print(f"Saved video frames={n_frames} → {video_file}")
        recording = False

    def save_still(img) -> None:
        nonlocal n_stills
        still = frames_dir / f"frame_{n_stills:05d}.jpg"
        cv2.imwrite(str(still), img)
        n_stills += 1
        print(f"Still → {still}")

    if args.seconds > 0:
        start_rec()

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("Webcam stopped delivering frames")
                break
            now = time.time()
            vis = frame.copy()
            status = "REC" if recording else "IDLE"
            color = (0, 0, 255) if recording else (200, 200, 200)
            cv2.putText(vis, status, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
            cv2.putText(
                vis,
                f"vid={n_frames} stills={n_stills}  terminal: r/s/q",
                (12, 64),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                2,
            )
            cv2.imshow("wheat poster capture", vis)

            if recording and writer is not None:
                writer.write(frame)
                n_frames += 1
                if args.auto_frames > 0 and now - last_auto >= args.auto_frames:
                    save_still(frame)
                    last_auto = now
                if args.seconds > 0 and now - started_at >= args.seconds:
                    print(f"--seconds {args.seconds} reached")
                    break

            # Window key (only if window focused) + terminal key
            key = cv2.waitKey(1) & 0xFF
            ch = term.poll()
            if ch:
                key = ord(ch) if len(ch) == 1 else key

            if key in (ord("q"), ord("Q"), 27):
                print("Quit")
                break
            if key in (ord("r"), ord("R")):
                if recording:
                    stop_rec()
                else:
                    start_rec()
            if key in (ord("s"), ord("S")):
                save_still(frame)
    except KeyboardInterrupt:
        print("\nCtrl+C — stopping")
    finally:
        stop_rec()
        term.close()
        cap.release()
        cv2.destroyAllWindows()

    print(f"Done. frames={n_frames}, stills={n_stills} under {out_dir}")
    if video_file and video_file.is_file():
        print(f"Video file: {video_file} ({video_file.stat().st_size} bytes)")
    else:
        print("No video file (press r to record, or use --seconds 20)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
