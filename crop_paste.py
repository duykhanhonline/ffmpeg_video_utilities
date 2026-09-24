#!/usr/bin/env python3
"""Crop a full-width horizontal strip out of a video and paste it onto another.

The strip keeps the source's full width and --ratio of its height (default
0.3 = 30%), taken from --anchor (default bottom). With --target, the strip
is scaled to the target's width and overlaid onto it in a single ffmpeg
pass, so the strip is encoded exactly once (no generation loss). Without
--target, the strip is written on its own at high quality so it can be
pasted later with minimal loss.

Cropping changes pixels, so unlike loop_video.py this always re-encodes
the video. Heights are rounded down to an even number since H.264 with
yuv420p requires even dimensions.

In paste mode the output always matches the target's length: if the
source is shorter it is looped, if longer it is cut off.

Example:
    # Paste the bottom 30% of source.mp4 onto the bottom of target.mp4
    python3 crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4

    # Top 25% of the source, pasted at y=100px, mixing both audio tracks
    python3 crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4 \\
        --ratio 0.25 --anchor top --paste-at 100 --audio mix

    # Strip's top edge at 40% of the target's height
    python3 crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4 \\
        --paste-at 40%

    # Bottom 35% of the source, squashed to 90% of its cropped height
    # (720px source: 252px crop -> 226px), pasted at the bottom
    python3 crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4 \\
        --ratio 0.35 --stretch 10

    # Strip appears 10s into the target, showing the source's own 10s mark
    # (both videos play from 0s; the strip is just hidden until then)
    python3 crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4 \\
        --show-at 10

    # Quick 60s test render instead of the full target
    python3 crop_paste.py --source source.mp4 --target target.mp4 --output test.mp4 \\
        --show-at 10 --duration 60

    # Crop only: save the strip for pasting later
    python3 crop_paste.py --source source.mp4 --output strip.mp4
"""

import argparse
import os
import sys
import time

import loop_video as lv
from cut_video import parse_timestamp

ANCHORS = ("bottom", "top", "center")


def probe_size(path):
    out = lv.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        path,
    ])
    width, height = out.strip().split("x")
    return int(width), int(height)


def has_audio_stream(path):
    out = lv.run([
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        path,
    ])
    return bool(out.strip())


def _even(value):
    # Round away float noise first (720 * 0.35 == 251.99999999999997, which
    # would otherwise floor to 250 instead of 252), then floor to even.
    return int(round(value, 6) / 2) * 2


def strip_size(source_size, ratio, stretch, target_width=None):
    """Return (crop_h, strip_w, strip_h) in pixels, all even.

    crop_h is the height cut from the source; strip_w x strip_h is the
    strip's final size after --stretch and, when pasting, scaling to the
    target's width. Computed here rather than as ffmpeg expressions so the
    render and the overflow warning always agree.
    """
    source_w, source_h = source_size
    crop_h = _even(source_h * ratio)
    strip_h = _even(crop_h * (1 - stretch / 100))
    strip_w = source_w
    if target_width is not None:
        strip_h = _even(strip_h * target_width / source_w)
        strip_w = target_width
    if crop_h <= 0 or strip_h <= 0:
        raise RuntimeError(
            f"--ratio {ratio} and --stretch {stretch} leave the strip with no height "
            f"(source is {source_h}px tall)"
        )
    return crop_h, strip_w, strip_h


def strip_filter(anchor, crop_h, strip_w, strip_h):
    """Crop a full-width strip of crop_h, then resize it to strip_w x strip_h."""
    # oh (output height) is usable in the y expression, so the strip's
    # position is expressed relative to its own height.
    y = {"bottom": "ih-oh", "top": "0", "center": "trunc((ih-oh)/4)*2"}[anchor]
    return f"crop=w=iw:h={crop_h}:x=0:y={y},scale={strip_w}:{strip_h}"


def overlay_y(paste_at):
    if paste_at == "bottom":
        return "H-h"
    if paste_at == "top":
        return "0"
    if paste_at == "center":
        return "(H-h)/2"
    if paste_at.endswith("%"):
        return f"trunc(H*{float(paste_at[:-1]) / 100})"  # percent of target height
    return paste_at  # pixel offset from the top


def parse_paste_at(value):
    value = value.strip()
    if value in ANCHORS:
        return value
    error = argparse.ArgumentTypeError(
        f"invalid --paste-at: {value!r} (expected bottom, top, center, "
        f"a pixel offset >= 0 like 100, or a percentage 0-100 like 30%)"
    )
    try:
        if value.endswith("%"):
            if not 0 <= float(value[:-1]) <= 100:
                raise error
        elif int(value) < 0:
            raise error
    except ValueError:
        raise error
    return value


def warn_if_overflow(strip_h, target_h, paste_at):
    """Warn when a numeric --paste-at pushes the strip past the target's bottom edge."""
    if paste_at in ANCHORS:
        return
    if paste_at.endswith("%"):
        y = int(target_h * float(paste_at[:-1]) / 100)
    else:
        y = int(paste_at)
    if y + strip_h > target_h:
        print(
            f"warning: strip is {strip_h}px tall, pasted at y={y}px it ends at {y + strip_h}px "
            f"but the target is only {target_h}px tall; the bottom {y + strip_h - target_h}px "
            f"will be cut off (use --paste-at bottom to sit flush with the bottom edge)"
        )


def output_duration(full_duration, limit):
    """Clamp --duration to the input's length (None = the whole input)."""
    if limit is None:
        return full_duration
    if limit > full_duration:
        print(f"note: --duration {limit}s is longer than the video ({full_duration:.2f}s); using the full length")
        return full_duration
    return limit


def crop_only(source, output, ratio, anchor, stretch, limit, crf, preset):
    duration = output_duration(lv.probe_duration(source), limit)
    crop_h, strip_w, strip_h = strip_size(probe_size(source), ratio, stretch)
    audio = ["-c:a", "copy"] if has_audio_stream(source) else ["-an"]
    lv.run_with_progress([
        "ffmpeg", "-y", "-i", source,
        "-t", str(duration),
        "-vf", strip_filter(anchor, crop_h, strip_w, strip_h),
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
    ] + audio + [output], "Cropping strip", duration)


def crop_and_paste(source, target, output, ratio, anchor, stretch, paste_at, show_at, limit, audio_mode, crf, preset):
    duration = output_duration(lv.probe_duration(target), limit)
    if show_at >= duration:
        raise RuntimeError(
            f"--show-at ({show_at}s) must be before the end of the output ({duration:.2f}s), "
            f"or the strip would never appear"
        )
    target_w, target_h = probe_size(target)
    crop_h, strip_w, strip_h = strip_size(probe_size(source), ratio, stretch, target_w)
    warn_if_overflow(strip_h, target_h, paste_at)

    # Both inputs play from t=0; before --show-at the strip is simply not
    # drawn (and its audio muted), so when it appears the source is at the
    # same point as the target (--show-at 30 shows the source's 30s mark).
    enable = f":enable='gte(t,{show_at})'" if show_at else ""
    mute = f"volume=0:enable='lt(t,{show_at})'" if show_at else "anull"
    filters = [
        f"[1:v]{strip_filter(anchor, crop_h, strip_w, strip_h)}[strip]",
        f"[0:v][strip]overlay=x=0:y={overlay_y(paste_at)}:shortest=0{enable},format=yuv420p[v]",
    ]
    maps = ["-map", "[v]"]

    target_audio = has_audio_stream(target)
    source_audio = has_audio_stream(source)
    if audio_mode == "mix" and not (target_audio and source_audio):
        print("warning: --audio mix needs audio in both videos; using whichever has audio")
        audio_mode = "target" if target_audio else "source"

    if audio_mode == "target" and target_audio:
        maps += ["-map", "0:a"]
        audio_args = ["-c:a", "copy"]
    elif audio_mode == "source" and source_audio:
        filters.append(f"[1:a]{mute}[a]")
        maps += ["-map", "[a]"]
        audio_args = ["-c:a", "aac", "-b:a", "192k"]
    elif audio_mode == "mix":
        filters.append(f"[1:a]{mute}[sa];[0:a][sa]amix=inputs=2:duration=first[a]")
        maps += ["-map", "[a]"]
        audio_args = ["-c:a", "aac", "-b:a", "192k"]
    else:
        audio_args = ["-an"]

    lv.run_with_progress([
        "ffmpeg", "-y",
        "-i", target,
        # Loop the source indefinitely; -t below cuts the output to the
        # target's length, so a short source repeats and a long one is cut.
        "-stream_loop", "-1", "-i", source,
        "-filter_complex", ";".join(filters),
    ] + maps + [
        "-t", str(duration),
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
    ] + audio_args + [output], "Cropping and pasting", duration)


def main():
    # Windows consoles default to a legacy code page; force UTF-8 so
    # non-ASCII paths in messages and ffmpeg errors print cleanly.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Crop a full-width strip (e.g. bottom 30%) from a video and paste it onto another video."
    )
    parser.add_argument("--source", required=True, help="Video to crop the strip from.")
    parser.add_argument(
        "--target",
        help="Video to paste the strip onto. Omit to only crop and save the strip.",
    )
    parser.add_argument("--output", required=True, help="Path to the output video file.")
    parser.add_argument(
        "--ratio", type=float, default=0.3,
        help="Fraction of the source height to keep, 0-1 (default 0.3 = 30%%).",
    )
    parser.add_argument(
        "--anchor", choices=ANCHORS, default="bottom",
        help="Which part of the source to crop from (default bottom).",
    )
    parser.add_argument(
        "--stretch", type=float, default=0,
        help="After cropping, shrink the strip's height by this percentage of its cropped height, "
             "keeping its width (10 = 90%% as tall; negative makes it taller; default 0).",
    )
    parser.add_argument(
        "--paste-at", type=parse_paste_at, default="bottom",
        help="Where the strip's top edge goes on the target: bottom, top, center, a pixel offset "
             "from the top (100), or a percentage of the target's height (30%%) (default bottom).",
    )
    parser.add_argument(
        "--show-at", type=parse_timestamp, default=0,
        help="Keep the strip hidden until this point: seconds (30), M:SS (1:30), or H:MM:SS "
             "(default 0 = from the start). Both videos play from 0s, so at --show-at 30 the "
             "strip appears at the source's own 30s mark.",
    )
    parser.add_argument(
        "--duration", type=parse_timestamp,
        help="Only render the first part of the video, e.g. 60 or 1:00 for a quick test "
             "(default: full length of the target, or of the source in crop-only mode).",
    )
    parser.add_argument(
        "--audio", choices=["target", "source", "mix", "none"], default="target",
        help="Audio for paste mode: keep the target's, the source's, mix both, or drop it (default target).",
    )
    parser.add_argument(
        "--crf", type=int,
        help="x264 CRF, lower = higher quality (default 18 when pasting, 10 for crop-only "
             "so the strip survives being re-encoded again later).",
    )
    parser.add_argument("--preset", default="medium", help="x264 preset (default medium).")
    args = parser.parse_args()

    if not os.path.isfile(args.source):
        parser.error(f"source not found: {args.source}")
    if args.target and not os.path.isfile(args.target):
        parser.error(f"target not found: {args.target}")
    if not 0 < args.ratio <= 1:
        parser.error("--ratio must be greater than 0 and at most 1")
    if args.stretch >= 100:
        parser.error("--stretch must be less than 100 (100 would leave no height)")
    if args.show_at < 0:
        parser.error("--show-at must be >= 0")
    if args.duration is not None and args.duration <= 0:
        parser.error("--duration must be positive")
    if args.show_at and not args.target:
        parser.error("--show-at only applies when pasting (needs --target)")

    try:
        lv.check_dependencies()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    start_time = time.monotonic()
    try:
        if args.target:
            crf = args.crf if args.crf is not None else 18
            crop_and_paste(
                args.source, args.target, args.output, args.ratio, args.anchor,
                args.stretch, args.paste_at, args.show_at, args.duration, args.audio, crf, args.preset,
            )
        else:
            crf = args.crf if args.crf is not None else 10
            crop_only(
                args.source, args.output, args.ratio, args.anchor, args.stretch,
                args.duration, crf, args.preset,
            )
        elapsed = time.monotonic() - start_time
        print(f"Done: {args.output} (took {elapsed:.1f}s)")
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
