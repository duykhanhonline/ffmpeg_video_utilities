#!/usr/bin/env python3
"""Join two or more videos end to end into one.

Unlike merge_video.py (which rotates a single clip), this plays --inputs
one after another in the order given.

Two modes:

  default   Re-encode everything through ffmpeg's concat filter. Works with
            any mix of videos: each one is scaled to fit the output size
            (letterboxed/pillarboxed to keep its shape), converted to the
            output frame rate, and given silence if it has no audio. The
            output size and frame rate default to the first input's.
  --copy    Stitch the files with the concat demuxer, no re-encoding, like
            loop_video.py. Very fast, but every input must have the same
            codecs, size, pixel format and audio format (e.g. clips cut
            from the same video, or exported with the same settings). The
            script checks this first and tells you what differs.

Wildcards in --inputs are expanded here and sorted by name, since the
Windows shell does not expand them.

--folder saves typing the same folder for every input: names in --inputs
are looked up inside it. With --folder and no --inputs, every video in the
folder is joined, sorted by name.

Example:
    python3 merge_video2.py --inputs intro.mp4 part1.mp4 part2.mp4 --output all.mp4

    # Same, with all three in D:/videos/project
    python3 merge_video2.py --folder D:/videos/project \\
        --inputs intro.mp4 part1.mp4 part2.mp4 --output all.mp4

    # Every video in the folder, sorted by name
    python3 merge_video2.py --folder D:/videos/project --output all.mp4

    # Every .mp4 in a folder, sorted by name, no re-encoding
    python3 merge_video2.py --inputs "clips/*.mp4" --output all.mp4 --copy

    # Different sources, forced to 1080p at 30 fps
    python3 merge_video2.py --inputs a.mp4 b.mov c.mkv --output all.mp4 \\
        --size 1920x1080 --fps 30
"""

import argparse
import glob
import json
import os
import shutil
import sys
import tempfile
import time

import loop_video as lv

AUDIO_RATE = 48000

# What counts as a video when joining a whole --folder.
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts")


def folder_videos(folder):
    return sorted(
        os.path.join(folder, name) for name in os.listdir(folder)
        if name.lower().endswith(VIDEO_EXTENSIONS) and os.path.isfile(os.path.join(folder, name))
    )


def expand_inputs(patterns, folder=None):
    if folder:
        if not patterns:
            return folder_videos(folder)
        patterns = [os.path.join(folder, p) for p in patterns]
    paths = []
    for pattern in patterns:
        if glob.has_magic(pattern):
            matches = sorted(p for p in glob.glob(pattern) if os.path.isfile(p))
            if not matches:
                raise RuntimeError(f"no files match {pattern}")
            paths.extend(matches)
        else:
            paths.append(pattern)
    return paths


def parse_size(value):
    try:
        w, h = (int(n) for n in value.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT like 1920x1080, got {value!r}")
    if w <= 0 or h <= 0 or w % 2 or h % 2:
        raise argparse.ArgumentTypeError(f"width and height must be positive even numbers, got {value!r}")
    return w, h


def probe(path):
    """Return the first video and audio stream info and the video's duration."""
    out = lv.run([
        "ffprobe", "-v", "error", "-of", "json",
        "-show_entries",
        "stream=codec_type,codec_name,profile,width,height,pix_fmt,r_frame_rate,"
        "sample_rate,channels,duration:format=duration",
        path,
    ])
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if video is None:
        raise RuntimeError(f"{path} has no video stream")
    # Stream duration is missing in some containers (e.g. MKV); fall back
    # to the container's.
    duration = video.get("duration") or data["format"].get("duration")
    return {"path": path, "video": video, "audio": audio, "duration": float(duration)}


def copy_mismatches(infos):
    """Describe how each input differs from the first in ways -c copy can't handle."""
    video_keys = ("codec_name", "profile", "width", "height", "pix_fmt", "r_frame_rate")
    audio_keys = ("codec_name", "sample_rate", "channels")
    first = infos[0]
    problems = []
    for info in infos[1:]:
        for key in video_keys:
            if info["video"].get(key) != first["video"].get(key):
                problems.append(
                    f"{info['path']}: video {key} {info['video'].get(key)} "
                    f"(first input has {first['video'].get(key)})"
                )
        if (info["audio"] is None) != (first["audio"] is None):
            has = "has" if info["audio"] else "has no"
            problems.append(f"{info['path']}: {has} audio, unlike the first input")
        elif info["audio"]:
            for key in audio_keys:
                if info["audio"].get(key) != first["audio"].get(key):
                    problems.append(
                        f"{info['path']}: audio {key} {info['audio'].get(key)} "
                        f"(first input has {first['audio'].get(key)})"
                    )
    return problems


def merge_copy(infos, output, tmp_dir, total_duration):
    list_path = os.path.join(tmp_dir, "list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for info in infos:
            escaped = os.path.abspath(info["path"]).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    lv.run_with_progress([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
        output,
    ], "Joining (no re-encode)", total_duration)


def build_filter(infos, width, height, fps, with_audio):
    parts = []
    labels = []
    for i, info in enumerate(infos):
        parts.append(
            f"[{i}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{i}]"
        )
        labels.append(f"[v{i}]")
        if with_audio:
            # Make each clip's audio exactly as long as its video, so a clip
            # with shorter/longer (or no) audio can't shift the ones after it.
            src = f"[{i}:a:0]" if info["audio"] else f"anullsrc=r={AUDIO_RATE}:cl=stereo,"
            if info["audio"]:
                src += f"aresample={AUDIO_RATE},"
            parts.append(
                f"{src}aformat=sample_fmts=fltp:sample_rates={AUDIO_RATE}:channel_layouts=stereo,"
                f"apad,atrim=0:{info['duration']},asetpts=N/SR/TB[a{i}]"
            )
            labels.append(f"[a{i}]")
    a = 1 if with_audio else 0
    out = "[v][a]" if with_audio else "[v]"
    parts.append(f"{''.join(labels)}concat=n={len(infos)}:v=1:a={a}{out}")
    return ";".join(parts)


def merge_reencode(infos, output, width, height, fps, crf, preset, total_duration):
    with_audio = any(info["audio"] for info in infos)
    cmd = ["ffmpeg", "-y"]
    for info in infos:
        cmd += ["-i", info["path"]]
    cmd += [
        "-filter_complex", build_filter(infos, width, height, fps, with_audio),
        "-map", "[v]",
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
    ]
    if with_audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    cmd.append(output)
    lv.run_with_progress(cmd, "Joining", total_duration)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Join two or more videos end to end into one.")
    parser.add_argument(
        "--inputs", nargs="+",
        help="Videos to join, in order. Wildcards like \"clips/*.mp4\" are expanded and sorted by name. "
             "Required unless --folder is given.",
    )
    parser.add_argument(
        "--folder",
        help="Folder holding the inputs, so --inputs can be plain file names. "
             "Without --inputs, joins every video in the folder, sorted by name.",
    )
    parser.add_argument("--output", required=True, help="Output video.")
    parser.add_argument(
        "--copy", action="store_true",
        help="Join without re-encoding (fast); all inputs must have the same format.",
    )
    parser.add_argument(
        "--size", type=parse_size,
        help="Output size as WIDTHxHEIGHT, e.g. 1920x1080 (default: the first input's). Re-encode mode only.",
    )
    parser.add_argument(
        "--fps",
        help="Output frame rate, e.g. 30 or 30000/1001 (default: the first input's). Re-encode mode only.",
    )
    parser.add_argument("--crf", type=int, default=18, help="x264 CRF, lower = higher quality (default 18).")
    parser.add_argument("--preset", default="medium", help="x264 preset (default medium).")
    args = parser.parse_args()

    if not args.inputs and not args.folder:
        parser.error("give --inputs, --folder, or both")
    if args.folder and not os.path.isdir(args.folder):
        parser.error(f"folder not found: {args.folder}")
    if args.copy and (args.size or args.fps):
        parser.error("--size and --fps need re-encoding; drop --copy to use them")

    try:
        lv.check_dependencies()
        paths = expand_inputs(args.inputs, args.folder)
        out_abs = os.path.abspath(args.output)
        if not args.inputs:
            # Whole-folder mode: skip a previous run's output saved in the same folder.
            paths = [p for p in paths if os.path.abspath(p) != out_abs]
        if len(paths) < 2:
            parser.error("give at least two videos to join")
        for path in paths:
            if not os.path.isfile(path):
                parser.error(f"input not found: {path}")
        if any(os.path.abspath(p) == out_abs for p in paths):
            parser.error("--output must not be one of the inputs")

        infos = [probe(p) for p in paths]
        total_duration = sum(info["duration"] for info in infos)
        print(f"Joining {len(infos)} videos, {total_duration:.1f}s in total:")
        for info in infos:
            v = info["video"]
            audio = "audio" if info["audio"] else "no audio"
            print(f"  {info['path']}  {v['width']}x{v['height']}  {info['duration']:.2f}s  {audio}")

        start_time = time.monotonic()
        if args.copy:
            problems = copy_mismatches(infos)
            if problems:
                raise RuntimeError(
                    "--copy needs every input in the same format, but:\n  "
                    + "\n  ".join(problems)
                    + "\nRun without --copy to re-encode instead."
                )
            tmp_dir = tempfile.mkdtemp(prefix="merge_video2_")
            try:
                merge_copy(infos, args.output, tmp_dir, total_duration)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            first = infos[0]["video"]
            width, height = args.size or (first["width"] // 2 * 2, first["height"] // 2 * 2)
            fps = args.fps or first["r_frame_rate"]
            merge_reencode(infos, args.output, width, height, fps, args.crf, args.preset, total_duration)

        elapsed = time.monotonic() - start_time
        print(f"Done: {args.output} (took {elapsed:.1f}s)")
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
