#!/usr/bin/env python3
"""Draw a line (or block) of text onto a video.

Uses ffmpeg's drawtext filter. The text is written to a temporary UTF-8
file and passed with textfile= rather than inline, so Vietnamese and other
accented characters render correctly and ffmpeg's filter escaping
(':', "'", '%', '\\') never has to be dealt with. Pick a font that has the
glyphs you need; Arial (the default) and Segoe UI both cover Vietnamese.

Drawing text changes pixels, so the video is re-encoded; audio is copied.

Sizes and positions accept pixels or percentages:
  --font-size 48       48px
  --font-size 5%       5% of the video's height
  --position bottom    one of 9 named spots, --margin away from the edges
  --position 100,50    text's top-left corner at x=100px, y=50px
  --position 10%,80%   same, as percentages of the video's width/height

Example:
    python3 add_text.py --input video.mp4 --output out.mp4 \\
        --text "Nhạc thư giãn" --font-size 6% --position bottom

    # Two lines, top-right, yellow with a translucent box, from 10s to 60s
    python3 add_text.py --input video.mp4 --output out.mp4 \\
        --text "Nhạc thư giãn\\nThư giãn và ngủ ngon" --position top-right \\
        --color "#FFD700" --box --show-at 10 --hide-at 60

    # Quick 30s test render
    python3 add_text.py --input video.mp4 --output test.mp4 \\
        --text "Xin chào" --duration 30 --preset ultrafast
"""

import argparse
import os
import shutil
import sys
import tempfile
import time

import loop_video as lv
from cut_video import parse_timestamp

# Named position -> (x, y) drawtext expressions. tw/th are the rendered
# text's width/height, w/h the video's, {m} the margin in pixels.
POSITIONS = {
    "top-left": ("{m}", "{m}"),
    "top": ("(w-tw)/2", "{m}"),
    "top-right": ("w-tw-{m}", "{m}"),
    "left": ("{m}", "(h-th)/2"),
    "center": ("(w-tw)/2", "(h-th)/2"),
    "right": ("w-tw-{m}", "(h-th)/2"),
    "bottom-left": ("{m}", "h-th-{m}"),
    "bottom": ("(w-tw)/2", "h-th-{m}"),
    "bottom-right": ("w-tw-{m}", "h-th-{m}"),
}

FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"),
    "/Library/Fonts",
    "/System/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
]


def parse_length(value):
    """Parse '48' (pixels) or '5%' (percentage). Returns (number, is_percent)."""
    value = value.strip()
    try:
        if value.endswith("%"):
            number, is_percent = float(value[:-1]), True
        else:
            number, is_percent = float(value), False
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid size: {value!r} (expected pixels like 48 or a percentage like 5%)")
    if number < 0:
        raise argparse.ArgumentTypeError(f"invalid size: {value!r} (must be >= 0)")
    return number, is_percent


def to_pixels(length, reference):
    number, is_percent = length
    return int(round(reference * number / 100)) if is_percent else int(round(number))


def parse_position(value):
    value = value.strip().lower()
    if value in POSITIONS:
        return value
    parts = value.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"invalid --position: {value!r} (expected one of {', '.join(POSITIONS)}, "
            f"or x,y like 100,50 or 10%,80%)"
        )
    return parse_length(parts[0]), parse_length(parts[1])


def find_font(font):
    """Resolve --font to a font file: an existing path, or a name looked up in the system font folders."""
    if os.path.isfile(font):
        return font
    name = font.lower()
    candidates = {name, name + ".ttf", name + ".otf", name + ".ttc"}
    for font_dir in FONT_DIRS:
        if not os.path.isdir(font_dir):
            continue
        for root, _, files in os.walk(font_dir):
            for file in files:
                if file.lower() in candidates:
                    return os.path.join(root, file)
    raise RuntimeError(
        f"font not found: {font!r}. Pass a .ttf/.otf path, or a file name from your fonts folder "
        f"(e.g. arial, arialbd for bold, segoeui, tahoma)"
    )


def filter_path(path):
    """Quote a file path for use as a filter option value.

    Forward slashes avoid backslash escaping; the drive colon must be
    escaped because ':' separates filter options.
    """
    path = os.path.abspath(path).replace("\\", "/")
    return "'" + path.replace("'", r"'\''").replace(":", r"\:") + "'"


def build_drawtext(args, font_path, text_path, width, height):
    font_size = to_pixels(args.font_size, height)
    if isinstance(args.position, str):
        margin = to_pixels(args.margin, height)
        x, y = (expr.format(m=margin) for expr in POSITIONS[args.position])
    else:
        x = str(to_pixels(args.position[0], width))
        y = str(to_pixels(args.position[1], height))

    opts = [
        f"fontfile={filter_path(font_path)}",
        f"textfile={filter_path(text_path)}",
        "expansion=none",  # treat % literally instead of as %{...} sequences
        f"fontsize={font_size}",
        f"fontcolor={args.color}",
        f"text_align={args.align}",
        f"x={x}",
        f"y={y}",
    ]
    if args.outline:
        opts += [f"borderw={args.outline}", f"bordercolor={args.outline_color}"]
    if args.box:
        padding = args.box_padding if args.box_padding is not None else max(4, font_size // 4)
        opts += ["box=1", f"boxcolor={args.box_color}", f"boxborderw={padding}"]
    if args.shadow:
        opts += [f"shadowx={args.shadow}", f"shadowy={args.shadow}", f"shadowcolor={args.shadow_color}"]
    if args.show_at or args.hide_at is not None:
        end = args.hide_at if args.hide_at is not None else "inf"
        opts.append(f"enable='between(t,{args.show_at},{end})'")
    return "drawtext=" + ":".join(opts), font_size


def main():
    # Windows consoles default to a legacy code page; force UTF-8 so
    # Vietnamese text and paths in messages print cleanly.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Draw text onto a video (Vietnamese supported).")
    parser.add_argument("--input", required=True, help="Video to draw text on.")
    parser.add_argument("--output", required=True, help="Path to the output video file.")
    text = parser.add_mutually_exclusive_group(required=True)
    text.add_argument("--text", help=r"The text to draw. Use \n for a line break.")
    text.add_argument("--text-file", help="UTF-8 text file with the text to draw (keeps its line breaks).")

    parser.add_argument(
        "--font-size", type=parse_length, default=parse_length("5%"),
        help="Pixels (48) or percentage of the video's height (5%%) (default 5%%).",
    )
    parser.add_argument(
        "--position", type=parse_position, default="bottom",
        help=f"One of {', '.join(POSITIONS)}; or the text's top-left corner as x,y in pixels "
             f"(100,50) or percentages (10%%,80%%) (default bottom).",
    )
    parser.add_argument(
        "--margin", type=parse_length, default=parse_length("4%"),
        help="Gap from the video's edges for named positions: pixels or percentage of the "
             "video's height (default 4%%).",
    )
    parser.add_argument(
        "--align", choices=["left", "center", "right"], default="center",
        help="How the lines of multi-line text line up with each other (default center).",
    )
    parser.add_argument(
        "--font", default="arial",
        help="Font file path, or a name from the system fonts folder: arial, arialbd (bold), "
             "segoeui, tahoma... (default arial).",
    )
    parser.add_argument("--color", default="white", help="Text color: a name or hex like #FFD700 (default white).")
    parser.add_argument("--outline", type=int, default=2, help="Outline thickness in pixels, 0 for none (default 2).")
    parser.add_argument("--outline-color", default="black", help="Outline color (default black).")
    parser.add_argument("--box", action="store_true", help="Draw a background box behind the text.")
    parser.add_argument(
        "--box-color", default="black@0.5",
        help="Box color, with @opacity from 0 to 1 (default black@0.5).",
    )
    parser.add_argument(
        "--box-padding", type=int,
        help="Space between the text and the box edge in pixels (default: a quarter of the font size).",
    )
    parser.add_argument("--shadow", type=int, default=0, help="Drop shadow offset in pixels, 0 for none (default 0).")
    parser.add_argument("--shadow-color", default="black@0.6", help="Shadow color (default black@0.6).")

    parser.add_argument(
        "--show-at", type=parse_timestamp, default=0,
        help="Time the text appears: seconds (10), M:SS (1:30), or H:MM:SS (default 0).",
    )
    parser.add_argument(
        "--hide-at", type=parse_timestamp,
        help="Time the text disappears (default: stays until the end).",
    )
    parser.add_argument(
        "--duration", type=parse_timestamp,
        help="Only render the first part of the video, e.g. 30 or 1:00, for a quick test.",
    )
    parser.add_argument("--crf", type=int, default=18, help="x264 CRF, lower = higher quality (default 18).")
    parser.add_argument("--preset", default="medium", help="x264 preset (default medium).")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        parser.error(f"input not found: {args.input}")
    if args.text_file and not os.path.isfile(args.text_file):
        parser.error(f"text file not found: {args.text_file}")
    if args.font_size[0] <= 0:
        parser.error("--font-size must be greater than 0")
    if args.show_at < 0:
        parser.error("--show-at must be >= 0")
    if args.hide_at is not None and args.hide_at <= args.show_at:
        parser.error("--hide-at must be after --show-at")
    if args.duration is not None and args.duration <= 0:
        parser.error("--duration must be positive")

    try:
        lv.check_dependencies()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    start_time = time.monotonic()
    tmp_dir = tempfile.mkdtemp(prefix="add_text_")
    try:
        font_path = find_font(args.font)

        if args.text_file:
            with open(args.text_file, encoding="utf-8-sig") as f:
                content = f.read().rstrip("\r\n")
        else:
            content = args.text.replace("\\n", "\n")
        if not content.strip():
            raise RuntimeError("the text is empty")
        text_path = os.path.join(tmp_dir, "text.txt")
        with open(text_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

        duration = lv.probe_duration(args.input)
        if args.duration is not None:
            if args.duration > duration:
                print(f"note: --duration {args.duration}s is longer than the video ({duration:.2f}s); using the full length")
            duration = min(duration, args.duration)
        if args.show_at >= duration:
            raise RuntimeError(
                f"--show-at ({args.show_at}s) must be before the end of the output ({duration:.2f}s), "
                f"or the text would never appear"
            )

        out = lv.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", args.input,
        ])
        width, height = (int(n) for n in out.strip().split("x"))

        drawtext, font_size = build_drawtext(args, font_path, text_path, width, height)
        print(f"Font: {font_path} at {font_size}px on a {width}x{height} video")

        lv.run_with_progress([
            "ffmpeg", "-y", "-i", args.input,
            "-t", str(duration),
            "-vf", drawtext,
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-crf", str(args.crf), "-preset", args.preset, "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            args.output,
        ], "Adding text", duration)

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
