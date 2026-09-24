#!/usr/bin/env python3
"""Cut a segment out of a video between two timestamps.

By default this re-encodes so the cut lands exactly on --from/--to (ffmpeg
can only stream-copy on keyframe boundaries, so a copy-mode cut can be off
by up to one GOP). Pass --copy for a fast, keyframe-snapped cut instead.

Example:
    # Frame-accurate cut from 5s to 8s (re-encodes)
    python3 cut_video.py --input source.mp4 --from 5 --to 8 --output clip.mp4

    # Fast cut, no re-encode, snapped to nearest keyframes
    python3 cut_video.py --input source.mp4 --from 5 --to 8 --output clip.mp4 --copy
"""

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time

SPINNER_FRAMES = "|/-\\"
BAR_WIDTH = 30


def run(cmd):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout


def _clear_line():
    sys.stdout.write("\r" + " " * 70 + "\r")
    sys.stdout.flush()


def _format_bar(pct):
    filled = int(BAR_WIDTH * pct / 100)
    return "[" + "#" * filled + "-" * (BAR_WIDTH - filled) + "]"


def _drain_stream(stream, chunks):
    chunks.append(stream.read())


def run_with_spinner(cmd, label):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stderr_chunks = []
    stderr_thread = threading.Thread(target=_drain_stream, args=(proc.stderr, stderr_chunks))
    stderr_thread.start()

    frame = 0
    while proc.poll() is None:
        sys.stdout.write(f"\r{label} {SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]}")
        sys.stdout.flush()
        frame += 1
        time.sleep(0.1)
    stderr_thread.join()
    _clear_line()
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{stderr_chunks[0]}")
    print(f"{label} done")


def _parse_timecode(value):
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def run_with_progress(cmd, label, total_duration):
    full_cmd = cmd + ["-progress", "pipe:1", "-nostats"]
    proc = subprocess.Popen(
        full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
    )
    stderr_chunks = []
    stderr_thread = threading.Thread(target=_drain_stream, args=(proc.stderr, stderr_chunks))
    stderr_thread.start()

    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time="):
            value = line.split("=", 1)[1]
            if value == "N/A":
                continue
            elapsed = _parse_timecode(value)
            pct = max(0.0, min(100.0, elapsed / total_duration * 100))
            sys.stdout.write(f"\r{label} {_format_bar(pct)} {pct:5.1f}%")
            sys.stdout.flush()
    proc.wait()
    stderr_thread.join()
    _clear_line()
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(full_cmd)}\n{stderr_chunks[0]}")
    print(f"{label} {_format_bar(100)} 100.0%")


def parse_timestamp(value):
    """Parse a plain seconds value or a YouTube-style timestamp.

    Accepts "8", "8.5", "2:00" (M:SS), or "1:02:00" (H:MM:SS).
    """
    value = value.strip()
    if ":" not in value:
        try:
            return float(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid timestamp: {value!r}")

    parts = value.split(":")
    if len(parts) == 2:
        h = "0"
        m, s = parts
    elif len(parts) == 3:
        h, m, s = parts
    else:
        raise argparse.ArgumentTypeError(
            f"invalid timestamp: {value!r} (expected SS, M:SS, or H:MM:SS)"
        )

    try:
        return int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid timestamp: {value!r}")


def check_dependencies():
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"'{tool}' not found on PATH. See CLAUDE.md for install instructions.")


def cut_video(input_path, start, duration, output_path, copy_mode):
    # -ss before -i is both fast (input seeking) and frame-accurate when
    # combined with re-encoding (ffmpeg decodes from the nearest keyframe
    # and discards frames before `start`). -t is used instead of -to so
    # the meaning ("duration to output") doesn't shift with -ss placement.
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
    ]
    if copy_mode:
        cmd += ["-c", "copy"]
        run_with_spinner(cmd + [output_path], "Cutting (stream copy)")
    else:
        cmd += [
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac",
        ]
        run_with_progress(cmd + [output_path], "Cutting", duration)


def main():
    parser = argparse.ArgumentParser(
        description="Cut a segment from a video between two timestamps (e.g. --from 5 --to 8)."
    )
    parser.add_argument("--input", required=True, help="Path to the source video.")
    parser.add_argument(
        "--from", dest="start", required=True, type=parse_timestamp,
        help="Start time: seconds (8), M:SS (2:00), or H:MM:SS (1:02:00).",
    )
    parser.add_argument(
        "--to", dest="end", required=True, type=parse_timestamp,
        help="End time: seconds (8), M:SS (3:00), or H:MM:SS (1:05:00).",
    )
    parser.add_argument("--output", required=True, help="Path to the output video file.")
    parser.add_argument(
        "--copy", action="store_true",
        help="Stream-copy instead of re-encoding: much faster, but the cut snaps to the "
             "nearest keyframes instead of landing exactly on --from/--to.",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        parser.error(f"input not found: {args.input}")
    if args.start < 0:
        parser.error("--from must be >= 0")
    if args.end <= args.start:
        parser.error("--to must be greater than --from")

    try:
        check_dependencies()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    start_time = time.monotonic()
    duration = args.end - args.start

    try:
        cut_video(args.input, args.start, duration, args.output, args.copy)
        elapsed = time.monotonic() - start_time
        print(f"Done: {args.output} ({args.start}s to {args.end}s, took {elapsed:.1f}s)")
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
