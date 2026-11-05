#!/usr/bin/env python3
"""Save the last frame of a video (or the frame at --at) as an image.

Seeks to a few seconds before the end (-sseof) and decodes forward with
-update 1, so the image file is overwritten by every frame and what is
left is the last frame that actually decodes. This is more reliable than
seeking to probe_duration() minus one frame, since the container's
duration often runs slightly past the final video frame (audio can be
longer, or the last frame's timestamp is rounded), which gives an empty
result.

--at takes the frame showing at a given time instead: 8, 8.5, 00:08,
1:30 or 1:02:00. The seek is frame-accurate (ffmpeg decodes up to that
exact point rather than snapping to a keyframe).

The image format follows the output's extension: .png (lossless, default)
or .jpg (smaller; --quality sets the JPEG quality).

Example:
    python3 last_frame.py --input video.mp4
    # -> video_last.png next to the video

    python3 last_frame.py --input video.mp4 --output thumb.jpg --quality 2

    # The frame at 8 seconds -> video_8s.png
    python3 last_frame.py --input video.mp4 --at 00:08
"""

import argparse
import os
import sys

import loop_video as lv
from cut_video import parse_timestamp

# Decode this many seconds from the end. Must cover at least one keyframe
# interval's worth of frames after the seek point; a few seconds is plenty
# for normal videos, and -sseof clamps to the start for shorter ones.
TAIL_SECONDS = 3


def default_output(input_path, at=None):
    base, _ = os.path.splitext(input_path)
    if at is None:
        return f"{base}_last.png"
    return f"{base}_{at:g}s.png"


def _image_args(output_path, quality):
    if os.path.splitext(output_path)[1].lower() in (".jpg", ".jpeg"):
        return ["-q:v", str(quality)]
    return []


def extract_last_frame(input_path, output_path, quality):
    lv.run([
        "ffmpeg", "-y", "-v", "error",
        "-sseof", f"-{TAIL_SECONDS}", "-i", input_path,
        "-map", "0:v:0", "-an",
        "-update", "1",
        *_image_args(output_path, quality),
        output_path,
    ])


def extract_frame_at(input_path, output_path, at, quality):
    lv.run([
        "ffmpeg", "-y", "-v", "error",
        "-ss", str(at), "-i", input_path,
        "-map", "0:v:0", "-an",
        "-frames:v", "1", "-update", "1",
        *_image_args(output_path, quality),
        output_path,
    ])


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Save the last frame of a video as an image.",
    )
    parser.add_argument("--input", required=True, help="Input video.")
    parser.add_argument(
        "--output",
        help="Image to write, .png or .jpg (default: <input name>_last.png next to the input, "
             "or <input name>_8s.png with --at 8).",
    )
    parser.add_argument(
        "--at", type=parse_timestamp,
        help="Take the frame at this time instead of the last one: 8, 8.5, 00:08, 1:30 or 1:02:00.",
    )
    parser.add_argument(
        "--quality", type=int, default=2,
        help="JPEG quality from 2 (best) to 31 (worst); ignored for PNG (default 2).",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        parser.error(f"input not found: {args.input}")
    if not 2 <= args.quality <= 31:
        parser.error("--quality must be between 2 and 31")
    if args.at is not None and args.at < 0:
        parser.error("--at must be >= 0")
    output = args.output or default_output(args.input, args.at)

    try:
        lv.check_dependencies()
        has_video = lv.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=index", "-of", "csv=p=0", args.input,
        ]).strip()
        if not has_video:
            raise RuntimeError(f"{args.input} has no video stream")
        if os.path.exists(output):
            os.remove(output)
        if args.at is None:
            extract_last_frame(args.input, output, args.quality)
        else:
            duration = lv.probe_duration(args.input)
            if args.at >= duration:
                raise RuntimeError(
                    f"--at {args.at:g}s is past the end of the video ({duration:.2f}s); "
                    f"leave out --at to get the last frame"
                )
            extract_frame_at(args.input, output, args.at, args.quality)
        if not os.path.isfile(output):
            raise RuntimeError(f"ffmpeg wrote no frame from {args.input}")
        print(f"Done: {output}")
    except (RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
