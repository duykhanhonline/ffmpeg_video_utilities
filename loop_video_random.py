#!/usr/bin/env python3
"""Loop a main clip into a long video, with a second clip dropped in at random.

Like loop_video.py, the long video is stitched with the concat demuxer and
-c copy, so it is never re-encoded. The playlist is the main clip over and
over, with the second clip inserted between repeats at random places:

    main main main SECOND main main main main SECOND main main ... main

--percent sets how much of the total time the second clip is on screen
(by time, not by count), rounded to whole showings. The showings are
random but spread out: the timeline is split into equal sections and one
showing goes at a random spot in each, so they never bunch up. The second
clip never plays twice in a row, and the video always ends on the main clip.

-c copy can only join clips with the same size, frame rate and codec
settings, so the second clip is re-encoded once (a few seconds of work) to
match the main clip. The main clip is left untouched. After joining, the
first seam is test-decoded; if it shows errors (the main clip came from an
unusual encoder), the main clip is re-encoded once too and the join redone.

Audio comes only from --music; the clips' own sound is dropped, since the
two clips' audio would not line up. Without --music the output is silent.

Example:
    # 60 minutes, second clip on screen ~10% of the time, with music
    python3 loop_video_random.py --main main_5s.mp4 --second bird_4s.mp4 \\
        --percent 10 --minutes 60 --music music.mp3 --output out.mp4

    # Same placement again (reproducible)
    python3 loop_video_random.py --main main_5s.mp4 --second bird_4s.mp4 \\
        --percent 10 --minutes 60 --seed 42 --output out.mp4
"""

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

import loop_video as lv

ENCODERS = {"h264": "libx264", "hevc": "libx265"}

# ffprobe profile names -> encoder -profile:v values.
PROFILES = {
    "constrained baseline": "baseline",
    "baseline": "baseline",
    "main": "main",
    "high": "high",
    "high 10": "high10",
    "high 4:2:2": "high422",
    "high 4:4:4 predictive": "high444",
    "main 10": "main10",
}


def probe_video(path):
    out = lv.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-of", "json",
        "-show_entries", "stream=codec_name,profile,width,height,pix_fmt,r_frame_rate,time_base",
        path,
    ])
    streams = json.loads(out)["streams"]
    if not streams:
        raise RuntimeError(f"{path} has no video stream")
    info = streams[0]
    info["duration"] = lv.probe_duration(path)
    if info["duration"] <= 0:
        raise RuntimeError(f"could not determine a valid duration for {path}")
    return info


def encode_like(src, dst, ref, label):
    """Re-encode src's video to match ref's codec, size, frame rate and pixel format."""
    encoder = ENCODERS.get(ref["codec_name"])
    if encoder is None:
        raise RuntimeError(
            f"the main clip is {ref['codec_name']}; only H.264 and H.265 main clips are supported. "
            f"Convert it to H.264 MP4 first."
        )
    w, h = ref["width"], ref["height"]
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
               f"setsar=1,fps={ref['r_frame_rate']},format={ref['pix_fmt']}",
        "-an", "-c:v", encoder, "-crf", "16", "-preset", "medium",
        # Same timescale as the main clip, so the joined timestamps line up.
        "-video_track_timescale", ref["time_base"].split("/")[1],
    ]
    profile = PROFILES.get((ref.get("profile") or "").lower())
    if profile:
        cmd += ["-profile:v", profile]
    cmd.append(dst)
    lv.run_with_spinner(cmd, label)


def plan_sequence(main_len, second_len, total, percent, rng):
    """Return the playlist as a list of "main"/"second", and the number of showings."""
    showings = round(total * percent / 100 / second_len)
    main_time = total - showings * second_len
    # Enough main repeats to fill the rest; the final trim then cuts inside
    # the last main repeat, so the video always ends on the main clip.
    mains = max(1, math.ceil(main_time / main_len))
    if showings > mains:
        # At most one showing per main repeat: the time share tops out at
        # second / (main + second).
        max_pct = second_len / (main_len + second_len) * 100
        raise RuntimeError(
            f"--percent {percent} would need the second clip {showings} times but there are only "
            f"{mains} main repeats to put it between; try at most about {max_pct:.0f}%"
        )

    # Gap i means "just before main repeat i", so nothing can go after the
    # last main. Split the gaps into equal sections, one random pick each:
    # random, but spread out and never two in a row.
    chosen = set()
    for k in range(showings):
        lo = k * mains // showings
        hi = (k + 1) * mains // showings
        chosen.add(rng.randrange(lo, hi))

    sequence = []
    for i in range(mains):
        if i in chosen:
            sequence.append("second")
        sequence.append("main")
    return sequence, showings


def write_list(list_path, sequence, paths):
    with open(list_path, "w", encoding="utf-8") as f:
        for name in sequence:
            escaped = os.path.abspath(paths[name]).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")


def join(list_path, out_path, total):
    lv.run_with_progress([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-map", "0:v:0", "-c", "copy", "-t", str(total),
        out_path,
    ], "[2/3] Joining (no re-encode)", total)


def seam_ok(video_path, sequence, lengths):
    """Decode across the first main -> second -> main seams; False if ffmpeg reports errors."""
    t = 0.0
    for name in sequence:
        if name == "second":
            start = max(0.0, t - 1)
            # Decode errors go to stderr, which lv.run doesn't return.
            result = subprocess.run([
                "ffmpeg", "-v", "error", "-ss", str(start), "-i", video_path,
                "-t", str(lengths["second"] + 2), "-f", "null", "-",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            return result.returncode == 0 and not result.stderr.strip()
        t += lengths[name]
    return True


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Loop a main clip into a long video, with a second clip shown at random places.",
    )
    parser.add_argument("--main", required=True, help="The main clip, looped for most of the video.")
    parser.add_argument("--second", required=True, help="The clip shown at random places.")
    parser.add_argument(
        "--percent", type=float, default=10,
        help="Share of the total time the second clip is on screen (default 10).",
    )
    parser.add_argument("--minutes", type=float, required=True, help="Length of the output in minutes, e.g. 60 or 90.5.")
    parser.add_argument("--music", help="Music for the audio track (looped if shorter). Without it the output is silent.")
    parser.add_argument("--output", required=True, help="Output video.")
    parser.add_argument(
        "--seed", type=int,
        help="Give a number to get the same random placement again (default: new placement each run).",
    )
    args = parser.parse_args()

    for flag, path in (("--main", args.main), ("--second", args.second), ("--music", args.music)):
        if path and not os.path.isfile(path):
            parser.error(f"{flag} file not found: {path}")
    if not 0 < args.percent < 100:
        parser.error("--percent must be between 0 and 100")
    if args.minutes <= 0:
        parser.error("--minutes must be positive")

    total = args.minutes * 60
    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    rng = random.Random(seed)

    start_time = time.monotonic()
    tmp_dir = tempfile.mkdtemp(prefix="loop_video_random_")
    try:
        lv.check_dependencies()
        main_info = probe_video(args.main)
        second_info = probe_video(args.second)

        sequence, showings = plan_sequence(
            main_info["duration"], second_info["duration"], total, args.percent, rng,
        )
        if showings == 0:
            print(f"note: {args.percent}% of {args.minutes:g} minutes is less than half of one showing "
                  f"of the second clip ({second_info['duration']:.1f}s), so it never appears")
        actual = showings * second_info["duration"] / total * 100
        print(f"{args.minutes:g} minutes: second clip shown {showings} times "
              f"({actual:.1f}% of the time), seed {seed}")

        paths = {"main": args.main, "second": os.path.join(tmp_dir, "second.mp4")}
        encode_like(args.second, paths["second"], main_info, "[1/3] Matching second clip to main clip")

        list_path = os.path.join(tmp_dir, "list.txt")
        video_path = os.path.join(tmp_dir, "joined.mp4")
        write_list(list_path, sequence, paths)
        join(list_path, video_path, total)

        # Re-probe the encoded second clip: fps conversion can shift its length slightly.
        lengths = {"main": main_info["duration"], "second": lv.probe_duration(paths["second"])}
        if showings and not seam_ok(video_path, sequence, lengths):
            print("note: the clips don't join cleanly as-is; re-encoding the main clip once to match")
            paths["main"] = os.path.join(tmp_dir, "main.mp4")
            encode_like(args.main, paths["main"], main_info, "[1/3] Re-encoding main clip")
            encode_like(args.second, paths["second"], main_info, "[1/3] Re-encoding second clip")
            write_list(list_path, sequence, paths)
            join(list_path, video_path, total)

        if args.music:
            lv.mux_music(video_path, args.music, total, args.output)
        else:
            lv.run_fn_with_spinner(lambda: shutil.copyfile(video_path, args.output), "[3/3] Writing output")

        elapsed = time.monotonic() - start_time
        print(f"Done: {args.output} (took {elapsed:.1f}s)")
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
