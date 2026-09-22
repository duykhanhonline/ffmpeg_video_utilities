#!/usr/bin/env python3
"""Loop a short video clip into a long video, optionally muxing in music.

Uses ffmpeg's concat demuxer with -c copy so the video is never
re-encoded. See CLAUDE.md for the full design rationale.

Example:
    # Video + music, looped to 2 hours (7200s)
    python3 loop_video.py --clip source_5s.mp4 --music music.mp3 \\
        --duration 7200 --output looped_2h.mp4

    # Video only, no music
    python3 loop_video.py --clip source_5s.mp4 --duration 7200 --output looped_2h.mp4

    # Keep intermediate files for inspection
    python3 loop_video.py --clip source_5s.mp4 --music music.mp3 \\
        --duration 7200 --output looped_2h.mp4 --keep-temp
"""

import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

SPINNER_FRAMES = "|/-\\"
BAR_WIDTH = 30


def run(cmd):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n{result.stderr}"
        )
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
    """Run a fast -c copy ffmpeg command with an indeterminate spinner.

    These steps (concat, trim) don't touch pixels, so ffmpeg finishes
    them near-instantly with no meaningful progress to report — a
    spinner just shows the process is alive, not a percentage.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # ffmpeg streams continuous stats to stderr; if nothing drains that
    # pipe while we poll(), it fills up and ffmpeg blocks on write(),
    # so it never exits and poll() never returns. Drain it in a thread.
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


def run_fn_with_spinner(fn, label):
    """Run a plain Python callable (e.g. a file copy) with a spinner."""
    error = {}

    def target():
        try:
            fn()
        except Exception as e:
            error["exc"] = e

    thread = threading.Thread(target=target)
    thread.start()
    frame = 0
    while thread.is_alive():
        sys.stdout.write(f"\r{label} {SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]}")
        sys.stdout.flush()
        frame += 1
        time.sleep(0.1)
    thread.join()
    _clear_line()
    if "exc" in error:
        raise error["exc"]
    print(f"{label} done")


def _parse_timecode(value):
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def run_with_progress(cmd, label, total_duration):
    """Run an ffmpeg command showing a real 0-100% progress bar.

    Appends -progress pipe:1 -nostats so ffmpeg emits machine-readable
    out_time=HH:MM:SS.ffffff lines we can turn into a percentage of
    total_duration.
    """
    full_cmd = cmd + ["-progress", "pipe:1", "-nostats"]
    proc = subprocess.Popen(
        full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
    )
    # Drain stderr concurrently for the same reason as run_with_spinner:
    # an unread stderr pipe can fill up and deadlock ffmpeg.
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


def probe_duration(path):
    out = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ])
    return float(out.strip())


def build_concat_list(list_path, clip_path, repeats):
    clip_abs = os.path.abspath(clip_path).replace("'", "'\\''")
    with open(list_path, "w", encoding="utf-8") as f:
        for _ in range(repeats):
            f.write(f"file '{clip_abs}'\n")


def loop_clip(clip_path, duration, tmp_dir, label="[1/3] Looping clip"):
    clip_len = probe_duration(clip_path)
    if clip_len <= 0:
        raise RuntimeError(f"Could not determine a valid duration for {clip_path}")

    repeats = math.ceil(duration / clip_len)
    list_path = os.path.join(tmp_dir, "concat_list.txt")
    build_concat_list(list_path, clip_path, repeats)

    looped_path = os.path.join(tmp_dir, "looped.mp4")
    run_with_spinner([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        looped_path,
    ], label)
    return looped_path


def trim_to_duration(looped_path, duration, tmp_dir, label="[2/3] Trimming to exact duration"):
    trimmed_path = os.path.join(tmp_dir, "trimmed.mp4")
    run_with_spinner([
        "ffmpeg", "-y",
        "-i", looped_path,
        "-t", str(duration),
        "-c", "copy",
        trimmed_path,
    ], label)
    return trimmed_path


def mux_music(video_path, music_path, duration, output_path, label="[3/3] Muxing music"):
    run_with_progress([
        "ffmpeg", "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", music_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-t", str(duration),
        "-shortest",
        output_path,
    ], label, duration)


def check_dependencies():
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"'{tool}' not found on PATH. See CLAUDE.md for install instructions.")


def main():
    parser = argparse.ArgumentParser(
        description="Loop a short video clip into a long video, using ffmpeg -c copy (no re-encode)."
    )
    parser.add_argument("--clip", required=True, help="Path to the source video clip.")
    parser.add_argument("--music", help="Optional path to a music file to mux in.")
    parser.add_argument("--duration", required=True, type=float, help="Target output duration in seconds.")
    parser.add_argument("--output", required=True, help="Path to the output video file.")
    parser.add_argument(
        "--keep-temp", action="store_true",
        help="Keep intermediate files (looped/trimmed) for inspection instead of deleting them.",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.clip):
        parser.error(f"clip not found: {args.clip}")
    if args.music and not os.path.isfile(args.music):
        parser.error(f"music file not found: {args.music}")
    if args.duration <= 0:
        parser.error("--duration must be positive")

    try:
        check_dependencies()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    start_time = time.monotonic()

    output_dir = os.path.dirname(os.path.abspath(args.output))
    if args.keep_temp:
        tmp_dir = os.path.join(output_dir, "loop_video_temp")
        os.makedirs(tmp_dir, exist_ok=True)
    else:
        tmp_dir = tempfile.mkdtemp(prefix="loop_video_")

    try:
        looped_path = loop_clip(args.clip, args.duration, tmp_dir)
        trimmed_path = trim_to_duration(looped_path, args.duration, tmp_dir)

        if args.music:
            mux_music(trimmed_path, args.music, args.duration, args.output)
        else:
            run_fn_with_spinner(
                lambda: shutil.copyfile(trimmed_path, args.output),
                "[3/3] Writing output",
            )

        elapsed = time.monotonic() - start_time
        print(f"Done: {args.output} (took {elapsed:.1f}s)")
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        if not args.keep_temp:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            print(f"Intermediate files kept in: {tmp_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
