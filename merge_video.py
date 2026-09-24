#!/usr/bin/env python3
"""Rotate a short clip's starting point.

Cuts the clip at --split seconds and reassembles it as
[split, end) + [0, split) -- i.e. playback starts at the split point
and wraps around to the beginning at the end.

The source clip is assumed to be short, so unlike loop_video.py this
does NOT cut with -c copy: -c copy can only cut on a keyframe, and a
short clip often has none near the requested split point, so it would
silently snap to the wrong spot. Instead both pieces are re-encoded
once with accurate seeking for a frame-exact cut -- cheap, since it's
a few seconds of video encoded a single time.

The [end]-to-[0] seam introduced by the split may not be perfectly
continuous (especially for AI-generated clips, which often don't loop
cleanly). --transition controls how that seam is handled:

  off          Hard cut. Output duration == input duration exactly.
               (default)
  crossfade    Dissolve the last --transition-duration seconds of the
               tail into the first --transition-duration seconds of
               the head. Simple and reliable; can ghost/double-expose
               on fast, distinct motion. Output is --transition-duration
               seconds shorter than the input.
  interpolate  Same seam, but instead of a plain dissolve, generates
               motion-compensated in-between frames (ffmpeg
               `minterpolate`) across the cut. Can look smoother for
               moving subjects, but minterpolate isn't designed for
               shot changes and can produce warping artifacts.
               Output duration == input duration exactly.
  match-cut    No blending at all -- instead, search a small window
               around --split for the moment whose frame most closely
               resembles the clip's last frame (via ffmpeg's `ssim`
               filter), and hard-cut there instead. Output duration ==
               input duration exactly, but not exactly at --split.

Example:
    python3 merge_video.py --clip source_10s.mp4 --split 5 --output rotated.mp4
    python3 merge_video.py --clip source_10s.mp4 --split 5 --output rotated.mp4 \\
        --transition crossfade --transition-duration 0.4
    python3 merge_video.py --clip source_10s.mp4 --split 5 --output rotated.mp4 \\
        --transition match-cut --match-window 1.0
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import loop_video as lv


def _run_capture(cmd):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout, result.stderr


def _write_concat_list(list_path, paths):
    with open(list_path, "w", encoding="utf-8") as f:
        for path in paths:
            escaped = os.path.abspath(path).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")


def _audio_args(audio_present, codec="copy"):
    return ["-c:a", codec] if audio_present else ["-an"]


def get_frame_rate(clip_path):
    stdout, _ = _run_capture([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        clip_path,
    ])
    num, _, den = stdout.strip().partition("/")
    return float(num) / float(den) if den else float(num)


def has_audio_stream(clip_path):
    stdout, _ = _run_capture([
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        clip_path,
    ])
    return bool(stdout.strip())


def split_clip(clip_path, split_at, full_duration, tmp_dir, crf, preset, audio_present):
    part_a = os.path.join(tmp_dir, "part_a.mp4")
    lv.run_with_progress([
        "ffmpeg", "-y",
        "-ss", "0", "-i", clip_path,
        "-t", str(split_at),
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
    ] + _audio_args(audio_present) + [part_a], f"Encoding [0-{split_at:.2f}s]", split_at)

    part_b = os.path.join(tmp_dir, "part_b.mp4")
    lv.run_with_progress([
        "ffmpeg", "-y",
        "-ss", str(split_at), "-i", clip_path,
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
    ] + _audio_args(audio_present) + [part_b], f"Encoding [{split_at:.2f}s-end]", full_duration - split_at)

    return part_a, part_b


def assemble_off(part_b, part_a, output_path, tmp_dir):
    list_path = os.path.join(tmp_dir, "assemble_list.txt")
    _write_concat_list(list_path, [part_b, part_a])
    lv.run_with_spinner([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        output_path,
    ], "Assembling rotated clip")


def assemble_crossfade(part_a, part_b, output_path, transition_duration, crf, preset, audio_present):
    dur_a = lv.probe_duration(part_a)
    dur_b = lv.probe_duration(part_b)
    if transition_duration >= dur_a or transition_duration >= dur_b:
        raise RuntimeError(
            f"--transition-duration ({transition_duration}s) must be shorter than both "
            f"split pieces ({dur_a:.2f}s / {dur_b:.2f}s)"
        )

    offset = dur_b - transition_duration
    filters = [f"[0:v][1:v]xfade=transition=fade:duration={transition_duration}:offset={offset}[v]"]
    maps = ["-map", "[v]"]
    codec_args = ["-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p"]
    if audio_present:
        filters.append(f"[0:a][1:a]acrossfade=d={transition_duration}[a]")
        maps += ["-map", "[a]"]
        codec_args += ["-c:a", "aac"]

    total_duration = dur_b + dur_a - transition_duration
    lv.run_with_progress([
        "ffmpeg", "-y",
        "-i", part_b, "-i", part_a,
        "-filter_complex", ";".join(filters),
    ] + maps + codec_args + [output_path], "Crossfading transition", total_duration)


def assemble_interpolate(part_a, part_b, output_path, tmp_dir, transition_duration, crf, preset, fps, audio_present):
    dur_a = lv.probe_duration(part_a)
    dur_b = lv.probe_duration(part_b)
    if transition_duration >= dur_a or transition_duration >= dur_b:
        raise RuntimeError(
            f"--transition-duration ({transition_duration}s) must be shorter than both "
            f"split pieces ({dur_a:.2f}s / {dur_b:.2f}s)"
        )

    codec_args = ["-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p"]

    b_head = os.path.join(tmp_dir, "b_head.mp4")
    lv.run_with_progress(
        ["ffmpeg", "-y", "-i", part_b, "-t", str(dur_b - transition_duration)] + codec_args
        + _audio_args(audio_present) + [b_head],
        "Trimming pre-transition segment", dur_b - transition_duration,
    )

    a_tail = os.path.join(tmp_dir, "a_tail.mp4")
    lv.run_with_progress(
        ["ffmpeg", "-y", "-ss", str(transition_duration), "-i", part_a] + codec_args
        + _audio_args(audio_present) + [a_tail],
        "Trimming post-transition segment", dur_a - transition_duration,
    )

    b_tail = os.path.join(tmp_dir, "b_tail.mp4")
    lv.run_with_progress(
        ["ffmpeg", "-y", "-ss", str(dur_b - transition_duration), "-i", part_b] + codec_args
        + _audio_args(audio_present) + [b_tail],
        "Extracting transition window (before)", transition_duration,
    )

    a_head = os.path.join(tmp_dir, "a_head.mp4")
    lv.run_with_progress(
        ["ffmpeg", "-y", "-i", part_a, "-t", str(transition_duration)] + codec_args
        + _audio_args(audio_present) + [a_head],
        "Extracting transition window (after)", transition_duration,
    )

    bridge_raw = os.path.join(tmp_dir, "bridge_raw.mp4")
    bridge_list = os.path.join(tmp_dir, "bridge_list.txt")
    _write_concat_list(bridge_list, [b_tail, a_head])
    lv.run_with_spinner([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", bridge_list, "-c", "copy", bridge_raw,
    ], "Joining transition window")

    bridge_smoothed = os.path.join(tmp_dir, "bridge_smoothed.mp4")
    lv.run_with_progress(
        ["ffmpeg", "-y", "-i", bridge_raw,
         "-vf", f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1:scd=none"]
        + codec_args + _audio_args(audio_present) + [bridge_smoothed],
        "Motion-interpolating transition", transition_duration * 2,
    )

    final_list = os.path.join(tmp_dir, "final_list.txt")
    _write_concat_list(final_list, [b_head, bridge_smoothed, a_tail])
    lv.run_with_spinner([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", final_list, "-c", "copy", output_path,
    ], "Assembling interpolated transition")


def _ssim_score(clip_path, time_a, time_b, sample=0.3):
    _, stderr = _run_capture([
        "ffmpeg", "-y",
        "-ss", str(time_a), "-t", str(sample), "-i", clip_path,
        "-ss", str(time_b), "-t", str(sample), "-i", clip_path,
        "-filter_complex", "[0:v][1:v]ssim",
        "-f", "null", "-",
    ])
    scores = [float(m) for m in re.findall(r"All:([\d.]+)", stderr)]
    return sum(scores) / len(scores) if scores else 0.0


def refine_split_match_cut(clip_path, split_at, full_duration, match_window):
    sample = 0.3
    step = 0.1
    lo = max(0.05, split_at - match_window)
    hi = min(full_duration - sample - 0.05, split_at + match_window)
    end_ref = max(0.0, full_duration - sample - 0.02)

    best_time, best_score = split_at, -1.0
    candidate = lo
    while candidate <= hi:
        score = _ssim_score(clip_path, end_ref, candidate, sample)
        if score > best_score:
            best_score, best_time = score, candidate
        candidate += step

    return best_time, best_score


def main():
    parser = argparse.ArgumentParser(
        description="Rotate a short clip so playback starts at --split and wraps to the beginning at the end."
    )
    parser.add_argument("--clip", required=True, help="Path to the source video clip.")
    parser.add_argument("--split", required=True, type=float, help="Seconds into the clip to split/rotate at.")
    parser.add_argument("--output", required=True, help="Path to the output video file.")
    parser.add_argument(
        "--transition", choices=["off", "crossfade", "interpolate", "match-cut"], default="off",
        help="How to handle the seam introduced by the split (default off = hard cut).",
    )
    parser.add_argument(
        "--transition-duration", type=float, default=0.4,
        help="Seconds of overlap/blend used by --transition crossfade or interpolate (default 0.4).",
    )
    parser.add_argument(
        "--match-window", type=float, default=1.0,
        help="Seconds to search around --split for --transition match-cut (default 1.0).",
    )
    parser.add_argument(
        "--crf", type=int, default=16,
        help="x264 CRF for the one-time re-encode work (lower = higher quality, default 16).",
    )
    parser.add_argument(
        "--preset", default="slow",
        help="x264 preset for the re-encode work (default slow; the clip is short so the cost is negligible).",
    )
    parser.add_argument(
        "--keep-temp", action="store_true",
        help="Keep intermediate files for inspection instead of deleting them.",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.clip):
        parser.error(f"clip not found: {args.clip}")
    if args.split <= 0:
        parser.error("--split must be positive")
    if args.transition_duration <= 0:
        parser.error("--transition-duration must be positive")

    try:
        lv.check_dependencies()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    start_time = time.monotonic()

    output_dir = os.path.dirname(os.path.abspath(args.output))
    if args.keep_temp:
        tmp_dir = os.path.join(output_dir, "merge_video_temp")
        os.makedirs(tmp_dir, exist_ok=True)
    else:
        tmp_dir = tempfile.mkdtemp(prefix="merge_video_")

    try:
        full_duration = lv.probe_duration(args.clip)
        if args.split >= full_duration:
            print(
                f"error: --split ({args.split}s) must be less than the clip's own "
                f"duration ({full_duration:.2f}s)",
                file=sys.stderr,
            )
            return 1

        split_at = args.split
        if args.transition == "match-cut":
            split_at, score = refine_split_match_cut(args.clip, args.split, full_duration, args.match_window)
            print(f"match-cut: refined split {args.split:.2f}s -> {split_at:.2f}s (similarity {score:.4f})")

        audio_present = has_audio_stream(args.clip)
        part_a, part_b = split_clip(args.clip, split_at, full_duration, tmp_dir, args.crf, args.preset, audio_present)

        if args.transition in ("off", "match-cut"):
            assemble_off(part_b, part_a, args.output, tmp_dir)
        elif args.transition == "crossfade":
            assemble_crossfade(part_a, part_b, args.output, args.transition_duration, args.crf, args.preset, audio_present)
        elif args.transition == "interpolate":
            fps = get_frame_rate(args.clip)
            assemble_interpolate(
                part_a, part_b, args.output, tmp_dir, args.transition_duration,
                args.crf, args.preset, fps, audio_present,
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
