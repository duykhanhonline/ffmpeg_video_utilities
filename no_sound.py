#!/usr/bin/env python3
"""Find the silent parts of an audio (or video) file.

Lists every stretch that stays silent for at least --period seconds, as
time points like 00:09 - 00:13. Uses ffmpeg's silencedetect filter.

"Silent" means quieter than --noise (default -50 dB). Raise it (e.g. -40)
if quiet hiss or room noise should count as silence too; lower it (e.g.
-60) if only true digital silence should count.

Times are shown as MM:SS (H:MM:SS past an hour), rounded to the nearest
second, with the exact length in seconds next to each.

--output-folder also saves each part with sound (between the silences) as
its own file: music_part01.mp3, music_part02.mp3, ... Each part keeps
--pad seconds (default 1) of the silence before and after it, so it doesn't
start or stop abruptly. Audio files are cut without re-encoding (no quality
loss; MP3 cuts land on its ~26 ms frames). Videos are re-encoded so the
cuts are frame-accurate.

--find-similar lists parts that are the same song, e.g. a track repeated
in a long compilation, even if cut at a different point or at another
volume. It uses audio fingerprints (ffmpeg's chromaprint), so it finds
the same recording, not different songs that merely sound alike.

Example:
    # Silences of 3 seconds or longer
    python3 no_sound.py --input music.mp3 --period 3

    # Also count quiet background noise as silence
    python3 no_sound.py --input music.mp3 --period 2 --noise -40

    # Save each part between the silences, with 1s of silence either side
    python3 no_sound.py --input music.mp3 --period 3 --output-folder parts

    # Which parts are the same song?
    python3 no_sound.py --input music.mp3 --period 3 --find-similar
"""

import argparse
import os
import re
import struct
import subprocess
import sys

import loop_video as lv
from cut_video import parse_timestamp

START_RE = re.compile(r"silence_start: (-?[\d.]+)")
END_RE = re.compile(r"silence_end: (-?[\d.]+)")


def parse_noise(value):
    """Accept -50, -50dB or -50db."""
    number = re.sub(r"\s*db$", "", value.strip(), flags=re.IGNORECASE)
    try:
        db = float(number)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a level in dB like -50, got {value!r}")
    if db >= 0:
        raise argparse.ArgumentTypeError(f"the noise level must be below 0 dB, got {value!r}")
    return db


def format_time(seconds):
    # Nearest second: MP3 edges land a hair early (8.998s), which would
    # otherwise show as 00:08.
    total = int(round(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def find_silences(path, period, noise_db, file_duration):
    """Return (start, end) pairs of silences lasting at least period seconds."""
    result = {}

    def detect():
        result["proc"] = subprocess.run([
            "ffmpeg", "-hide_banner", "-nostats", "-i", path,
            "-map", "0:a:0", "-af", f"silencedetect=noise={noise_db}dB:d={period}",
            "-f", "null", "-",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")

    lv.run_fn_with_spinner(detect, "Listening for silence")
    proc = result["proc"]
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read the audio in {path}:\n{proc.stderr[-2000:]}")

    silences = []
    start = None
    for line in proc.stderr.splitlines():
        m = START_RE.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = END_RE.search(line)
        if m and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    # Silent right up to the end: ffmpeg reports a start but no end.
    if start is not None and file_duration - start >= period:
        silences.append((start, file_duration))
    return silences


def sound_parts(silences, duration, pad):
    """The stretches between silences, each widened by pad seconds on both sides."""
    parts = []
    start = 0.0
    for s_start, s_end in silences + [(duration, duration)]:
        if s_start - start > 0.05:  # skip slivers, e.g. before a silence at 0:00
            parts.append((max(0.0, start - pad), min(duration, s_start + pad)))
        start = s_end
    return parts


def is_real_video(path):
    """True if the file has a video stream that isn't just cover art.

    Cover art in an MP3/M4A shows up as a video stream marked attached_pic;
    it doesn't count, and it's left out of the saved parts.
    """
    out = lv.run([
        "ffprobe", "-v", "error", "-select_streams", "v",
        "-show_entries", "stream_disposition=attached_pic", "-of", "csv=p=0", path,
    ])
    return any(line.strip() == "0" for line in out.splitlines())


def save_part(input_path, output_path, start, end, is_video):
    cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(end - start)]
    if is_video:
        cmd += [
            "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
        ]
    else:
        # Audio only: any cover art is dropped.
        cmd += ["-map", "0:a:0", "-c", "copy"]
    cmd.append(output_path)
    lv.run(cmd)


# Chromaprint works on 11025 Hz audio in steps of 1365 samples, so each
# fingerprint value covers about 0.124s.
FP_STEP = 1365 / 11025

try:
    _popcount = int.bit_count  # Python 3.10+
except AttributeError:
    def _popcount(x):
        return bin(x).count("1")


def fingerprint(path):
    """Chromaprint the whole file's audio in one pass: a list of 32-bit values."""
    result = {}

    def run():
        result["proc"] = subprocess.run([
            "ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0",
            "-f", "chromaprint", "-fp_format", "raw", "-",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    lv.run_fn_with_spinner(run, "Fingerprinting the audio")
    proc = result["proc"]
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg could not fingerprint the audio (needs an ffmpeg built with chromaprint):\n"
            + proc.stderr.decode("utf-8", "replace")[-2000:]
        )
    data = proc.stdout
    return list(struct.unpack(f"<{len(data) // 4}I", data[: len(data) // 4 * 4]))


def compare(a, b):
    """How alike two fingerprints are where they overlap best.

    Returns (match 0..1, overlap in seconds). Finds the time offset where
    the most values agree exactly, then measures the share of bits that
    agree over the overlap there: same recording ~0.9+, unrelated ~0.5.
    """
    positions = {}
    for j, v in enumerate(b):
        positions.setdefault(v, []).append(j)
    votes = {}
    for i, v in enumerate(a):
        for j in positions.get(v, ()):
            votes[i - j] = votes.get(i - j, 0) + 1
    if not votes:
        return 0.0, 0.0

    best = (0.0, 0)
    # The top few candidate offsets, each nudged +-1 step.
    for offset in sorted(votes, key=votes.get, reverse=True)[:5]:
        for off in (offset - 1, offset, offset + 1):
            lo, hi = max(0, off), min(len(a), len(b) + off)
            if hi - lo <= 0:
                continue
            diff = sum(_popcount(a[i] ^ b[i - off]) for i in range(lo, hi))
            match = 1 - diff / (32 * (hi - lo))
            if match > best[0]:
                best = (match, hi - lo)
    return best[0], best[1] * FP_STEP


def find_similar(fp, parts, min_match, min_overlap):
    """Pairs of parts (numbered from 1) that sound like the same recording."""
    slices = [fp[int(start / FP_STEP): int(end / FP_STEP)] for start, end in parts]
    pairs = []
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            if len(slices[i]) * FP_STEP < min_overlap or len(slices[j]) * FP_STEP < min_overlap:
                continue
            match, overlap = compare(slices[i], slices[j])
            if match >= min_match and overlap >= min_overlap:
                pairs.append((i + 1, j + 1, match, overlap))
    return pairs


def group_pairs(pairs):
    """Join matching pairs into groups: 1~3 and 3~7 make one group [1, 3, 7]."""
    group_of = {}
    for a, b, _, _ in pairs:
        ga, gb = group_of.get(a, {a}), group_of.get(b, {b})
        merged = ga | gb
        for n in merged:
            group_of[n] = merged
    unique = {id(g): g for g in group_of.values()}
    return sorted(sorted(g) for g in unique.values())


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Find the silent parts of an audio or video file.")
    parser.add_argument("--input", required=True, help="Audio file (mp3, wav, m4a...) or a video with sound.")
    parser.add_argument(
        "--period", type=parse_timestamp, default=3,
        help="Only report silences at least this long, in seconds (default 3). Also accepts M:SS.",
    )
    parser.add_argument(
        "--noise", type=parse_noise, default=-50.0,
        help="Anything quieter than this counts as silence, in dB (default -50).",
    )
    parser.add_argument(
        "--output-folder",
        help="Also save each part between the silences as its own file in this folder "
             "(created if missing).",
    )
    parser.add_argument(
        "--pad", type=float, default=1.0,
        help="Seconds of silence to keep before and after each saved part (default 1).",
    )
    parser.add_argument(
        "--find-similar", action="store_true",
        help="Also list parts that are the same song (same recording heard more than once).",
    )
    parser.add_argument(
        "--min-match", type=float, default=80,
        help="With --find-similar / --unique-only: how alike two parts must be, in percent (default 80). "
             "Same recording is usually 90+; unrelated audio about 50.",
    )
    parser.add_argument(
        "--min-overlap", type=float, default=15,
        help="With --find-similar / --unique-only: seconds of matching audio needed (default 15), "
             "so short look-alike moments don't count.",
    )
    parser.add_argument(
        "--unique-only", action="store_true",
        help="With --output-folder: save each song once, skipping repeats (keeps the longest copy). "
             "Uses the same check as --find-similar.",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        parser.error(f"input not found: {args.input}")
    if args.period <= 0:
        parser.error("--period must be greater than 0")
    if args.pad < 0:
        parser.error("--pad must be 0 or more")
    if args.unique_only and not args.output_folder:
        parser.error("--unique-only needs --output-folder (it decides which parts to save)")

    try:
        lv.check_dependencies()
        has_audio = lv.run([
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=index", "-of", "csv=p=0", args.input,
        ]).strip()
        if not has_audio:
            raise RuntimeError(f"{args.input} has no audio")
        duration = lv.probe_duration(args.input)
        silences = find_silences(args.input, args.period, args.noise, duration)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"{args.input} ({format_time(duration)}): silences of {args.period:g}s or longer, "
          f"quieter than {args.noise:g} dB")
    if not silences:
        print("  none found")
        if args.output_folder:
            print("Nothing to split: the whole file is one part.")
        return 0
    for i, (start, end) in enumerate(silences, 1):
        print(f"  {i:>3}. {format_time(start)} - {format_time(end)}  ({end - start:.1f}s)")
    total = sum(end - start for start, end in silences)
    print(f"{len(silences)} found, {total:.1f}s silent in total")

    base, ext = os.path.splitext(os.path.basename(args.input))
    try:
        # Part n here and in the saved file names is the same part; the
        # similarity check just leaves out the padding.
        skip = {}  # part number -> the part it repeats
        if args.find_similar or args.unique_only:
            # Compare without the padding: it's silence, and all silence looks alike.
            parts = sound_parts(silences, duration, 0)
            print()
            fp = fingerprint(args.input)
            pairs = find_similar(fp, parts, args.min_match / 100, args.min_overlap)

            def label(n):
                start, end = parts[n - 1]
                return f"part {n:02d} ({format_time(start)} - {format_time(end)})"

            print(f"Same song in more than one part (match {args.min_match:g}%+ over "
                  f"{args.min_overlap:g}s+), comparing {len(parts)} parts:")
            if not pairs:
                print("  none found")
            for g, group in enumerate(group_pairs(pairs), 1):
                # Keep the longest copy (the full song rather than a partial
                # repeat); the earliest one if they're equally long.
                keep = max(group, key=lambda n: (round(parts[n - 1][1] - parts[n - 1][0]), -n))
                print(f"  Group {g}: " + ", ".join(label(n) for n in group))
                for a, b, match, overlap in pairs:
                    if a in group:
                        print(f"      part {a:02d} ~ part {b:02d}: {match * 100:.0f}% match "
                              f"over {overlap:.0f}s")
                if args.unique_only:
                    print(f"      keeping part {keep:02d} (longest)")
                    skip.update({n: keep for n in group if n != keep})

        if args.output_folder:
            parts = sound_parts(silences, duration, args.pad)
            is_video = is_real_video(args.input)
            os.makedirs(args.output_folder, exist_ok=True)
            count = len(parts) - len(skip)
            what = f"{count} unique parts" if args.unique_only else f"{count} parts"
            print(f"\nSaving {what} to {args.output_folder} "
                  f"(with {args.pad:g}s of silence either side):")
            for i, (start, end) in enumerate(parts, 1):
                if i in skip:
                    print(f"  part {i:02d} skipped: same song as part {skip[i]:02d}")
                    continue
                path = os.path.join(args.output_folder, f"{base}_part{i:02d}{ext}")
                lv.run_fn_with_spinner(
                    lambda: save_part(args.input, path, start, end, is_video),
                    f"  part {i:02d} {format_time(start)} - {format_time(end)}",
                )
                print(f"         -> {path}  ({end - start:.1f}s)")
    except (RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
