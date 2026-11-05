#!/usr/bin/env python3
"""Strip everything but the sound from MP3 files.

Removes the ID3v2 tag (title, artist, comment/URL, date, encoder stamp...),
embedded cover art, chapters and the old ID3v1 tag at the end of the file.
The audio is copied, not re-encoded, so the sound is bit-identical.

Kept on purpose:
  - The encoder header in the first MP3 frame (Xing/Info + LAME). It tells
    players where the real sound starts and ends (gapless playback);
    removing it changes the decoded audio and adds ~0.04s. It says nothing
    about the song or where it came from.
  - "LAME..." text inside the audio frames themselves. It's part of the
    encoded sound; only re-encoding removes it, which lowers quality and
    writes new encoder markers anyway.

Example:
    python3 clean_info.py --input song.mp3                  # -> song_clean.mp3
    python3 clean_info.py --input song.mp3 --output clean.mp3

    # Every .mp3 in a folder
    python3 clean_info.py --folder D:/music --output-folder D:/music_clean
"""

import argparse
import json
import os
import sys
import tempfile

import loop_video as lv

CLEAN_SUFFIX = "_clean"


def describe_extras(path):
    """List what the file carries besides the audio, e.g. ["title", "cover art"]."""
    out = lv.run([
        "ffprobe", "-v", "error", "-of", "json",
        "-show_entries", "format_tags:stream=codec_type,codec_name:stream_disposition=attached_pic",
        "-show_chapters", path,
    ])
    data = json.loads(out)
    streams = data.get("streams", [])
    audio = [s for s in streams if s["codec_type"] == "audio"]
    if not audio:
        raise RuntimeError("no audio in this file")
    if audio[0].get("codec_name") != "mp3":
        raise RuntimeError(f"this is {audio[0].get('codec_name')} audio, not MP3")

    extras = [key.lower() for key in data.get("format", {}).get("tags", {})]
    covers = sum(1 for s in streams if s.get("disposition", {}).get("attached_pic"))
    if covers:
        extras.append("cover art" if covers == 1 else f"{covers} cover images")
    others = len(streams) - len(audio) - covers
    if len(audio) > 1:
        others += len(audio) - 1
    if others:
        extras.append(f"{others} other stream(s)")
    if data.get("chapters"):
        extras.append(f"{len(data['chapters'])} chapters")
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() >= 128:
            f.seek(-128, os.SEEK_END)
            if f.read(3) == b"TAG":
                extras.append("ID3v1 tag")
    return extras


def clean(input_path, output_path):
    """Write output_path with only input_path's first audio stream, no metadata."""
    # ffmpeg can't read and write the same file, so go through a temp file
    # in the output's folder and swap it in at the end.
    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp_path = tempfile.mkstemp(suffix=".mp3", dir=out_dir)
    os.close(fd)
    try:
        lv.run([
            "ffmpeg", "-y", "-v", "error", "-i", input_path,
            "-map", "0:a:0", "-c:a", "copy",
            "-map_metadata", "-1", "-map_chapters", "-1",
            "-id3v2_version", "0", "-write_id3v1", "0",
            # No "Lavf..." encoder stamp.
            "-fflags", "+bitexact", "-flags:a", "+bitexact",
            "-f", "mp3", tmp_path,
        ])
        os.replace(tmp_path, output_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def default_output(input_path, output_folder=None):
    base, _ = os.path.splitext(os.path.basename(input_path))
    if output_folder:
        return os.path.join(output_folder, base + ".mp3")
    return os.path.join(os.path.dirname(input_path), base + CLEAN_SUFFIX + ".mp3")


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Strip everything but the sound from MP3 files.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="One MP3 file.")
    source.add_argument("--folder", help="Clean every .mp3 in this folder.")
    parser.add_argument(
        "--output",
        help="With --input: where to save (default: <name>_clean.mp3 next to it). "
             "Giving the input's own path cleans it in place.",
    )
    parser.add_argument(
        "--output-folder",
        help="Save cleaned files here under their original names (created if missing). "
             "Default: <name>_clean.mp3 next to each file.",
    )
    args = parser.parse_args()

    if args.output and args.folder:
        parser.error("--output is for a single --input; use --output-folder with --folder")
    if args.output and args.output_folder:
        parser.error("give --output or --output-folder, not both")
    if args.input and not os.path.isfile(args.input):
        parser.error(f"input not found: {args.input}")
    if args.folder and not os.path.isdir(args.folder):
        parser.error(f"folder not found: {args.folder}")

    if args.input:
        inputs = [args.input]
    else:
        inputs = sorted(
            os.path.join(args.folder, name) for name in os.listdir(args.folder)
            if name.lower().endswith(".mp3")
        )
        if not args.output_folder:
            # Skip results of an earlier run saved next to the originals.
            inputs = [p for p in inputs if not os.path.splitext(p)[0].endswith(CLEAN_SUFFIX)]
        if not inputs:
            parser.error(f"no .mp3 files in {args.folder}")
    if args.output_folder:
        clash = [p for p in inputs if os.path.abspath(default_output(p, args.output_folder)) == os.path.abspath(p)]
        if clash:
            parser.error("--output-folder is the input's own folder, which would overwrite the originals; "
                         "choose another folder, or leave it out to save <name>_clean.mp3 files")

    try:
        lv.check_dependencies()
        if args.output_folder:
            os.makedirs(args.output_folder, exist_ok=True)
    except (RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    failed = 0
    for path in inputs:
        output = args.output or default_output(path, args.output_folder)
        try:
            extras = describe_extras(path)
            clean(path, output)
            removed = ", ".join(extras) if extras else "nothing (already clean)"
            print(f"{path} -> {output}")
            print(f"    removed: {removed}")
        except (RuntimeError, OSError) as e:
            failed += 1
            print(f"error: {path}: {e}", file=sys.stderr)

    if len(inputs) > 1:
        print(f"Done: {len(inputs) - failed} of {len(inputs)} cleaned" + (f", {failed} failed" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
