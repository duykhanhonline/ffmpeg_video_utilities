# ffmpeg Video Utilities

Small Python command-line tools around `ffmpeg` for making long background
videos: loop a short clip into hours of footage with music, cut or rotate
clips, and crop a strip out of one video and paste it onto another.

## Files

| File | What it does |
|---|---|
| `loop_video.py` | Loops a short clip into a long video (by `--duration` or to the length of `--music`) with **no video re-encoding**. Also holds the shared helpers the other scripts import (`run`, `run_with_spinner`, `run_with_progress`, `probe_duration`, `check_dependencies`). |
| `loop_video_random.py` | Like `loop_video.py`, but a second clip is dropped in at random places for `--percent` of the time (default 10%); always ends on the main clip. Length in `--minutes`. No re-encode of the long video. |
| `crop_paste.py` | Crops a full-width horizontal strip from a source video and pastes it onto a target video in one pass, or saves the strip on its own. |
| `add_text.py` | Draws one text (single or multi-line, Vietnamese supported) onto a video, with size, position, style and timing options. Standalone: not combined with `crop_paste.py` (user's choice). |
| `cut_video.py` | Cuts a time segment (`--from` / `--to`). Frame-accurate re-encode by default, `--copy` for a fast keyframe-snapped cut. Also provides `parse_timestamp` (used by `crop_paste.py`). |
| `merge_video.py` | Despite the name, it **rotates** a clip's starting point: splits at `--split` and plays `[split, end)` then `[0, split)`, with optional seam handling (`crossfade`, `interpolate`, `match-cut`). It does not merge two videos. |
| `merge_video2.py` | Joins two or more videos end to end (this is the real "merge"). Re-encodes by default so any mix of sizes/frame rates works; `--copy` for same-format inputs. |
| `last_frame.py` | Saves a video's last frame, or the frame at `--at` (e.g. `00:08`), as a `.png` (default) or `.jpg` image. |
| `no_sound.py` | Lists the silent parts of an audio file (or a video's sound) lasting at least `--period` seconds, as time points like `00:09 - 00:13`. With `--output-folder`, also saves each part between the silences as its own file (`--pad` 1s of silence either side). `--find-similar` lists parts that are the same song; `--unique-only` saves each song once. |
| `clean_info.py` | Strips everything but the sound from MP3s (tags, cover art, chapters, ID3v1), without re-encoding. One file or a whole folder. |
| `run_loop.py` | Empty placeholder. |
| `docs/` | `user-guide.md`: the end-user guide for every script (options, examples, workflow, troubleshooting); **update it when a script's options change**. Also feature request notes (e.g. `cut-video-by-proportion.md`, which led to `crop_paste.py`). |
| `videos/` | Sample videos (45–78 minutes each, so make short test clips instead of rendering them whole). |

## Requirements

- Python 3.8+
- `ffmpeg` and `ffprobe` on `PATH`

```bash
# Debian/Ubuntu
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg

# Windows
winget install ffmpeg
```

The main development machine is Windows. Examples use `python3`; on
Windows use `python`.

## Shared conventions

- **Helpers live in `loop_video.py`**; other scripts `import loop_video as lv`
  rather than duplicating them. (`cut_video.py` still has its own copies.)
- **UTF-8**: the helpers in `loop_video.py` decode ffmpeg output as UTF-8
  with `errors="replace"`, and `crop_paste.py` reconfigures stdout/stderr to
  UTF-8, so non-ASCII file names (e.g. Vietnamese) work on Windows.
  `cut_video.py` and `merge_video.py`'s own `_run_capture` still use the
  system default encoding.
- **Errors**: argument problems use `parser.error`; ffmpeg failures raise
  `RuntimeError` and `main()` prints `error: ...` and returns 1.
- **Progress**: long ffmpeg runs go through `run_with_progress` (0–100% bar
  from `-progress pipe:1`) or `run_with_spinner`.

---

## loop_video.py

Turns a short clip (e.g. 5 seconds) into a long video (e.g. 2 hours) with a
music track muxed in, using ffmpeg's concat demuxer with `-c copy`, i.e.
**no video re-encoding**. Every repeat of the loop is byte-identical
footage, so there is nothing to gain from re-compressing it thousands of
times; ffmpeg just stitches the encoded stream together at the container
level.

### Usage

```bash
# Video + music, 2 hours
python3 loop_video.py --clip source_5s.mp4 --music music.mp3 \
    --duration 7200 --output looped_2h.mp4

# Loop the video for exactly as long as the music
python3 loop_video.py --clip source_5s.mp4 --music music.mp3 \
    --match-music --output looped.mp4

# Video only, no music
python3 loop_video.py --clip source_5s.mp4 --duration 7200 --output looped_2h.mp4

# Keep intermediate files for inspection
python3 loop_video.py --clip source_5s.mp4 --music music.mp3 \
    --duration 7200 --output looped_2h.mp4 --keep-temp
```

- `--duration` is in seconds (7200 = 2 hours). If `--music` is shorter than
  `--duration`, it is looped automatically; the output is trimmed to
  `--duration` either way.
- `--match-music` uses the music file's length (via `ffprobe`) as the
  duration. Exactly one of `--duration` / `--match-music` is required, and
  `--match-music` requires `--music`. The video can end up to one frame
  shorter than the audio, since video ends on a frame boundary.

### How it works

1. **Loop, stream-copied**: builds an ffmpeg concat list repeating the clip
   `ceil(duration / clip_length)` times and stitches them with
   `ffmpeg -f concat -c copy`. No pixels are touched.
2. **Trim to exact duration**: another `-c copy` pass cuts the looped file
   to the requested length. `-c copy` can only cut on a keyframe (see
   Caveats).
3. **Mux in music**: the video stays `-c copy`; only the audio is encoded,
   once, to AAC. This is the only real encoding work.

### Performance vs. a normal re-encode loop

Measured on a 2-vCPU machine, 1080p/30fps source clip, 2-hour (7200s) output:

| Approach | Time for 2 hours |
|---|---|
| Re-encode loop, `libx264 -preset ultrafast` | ~40 minutes |
| Re-encode loop, `libx264 -preset fast` | ~3 hours |
| Re-encode loop, `libx264 -preset medium` | ~5–7 hours |
| **This tool (`-c copy` loop + AAC audio mux)** | **~5–6 minutes** |

The stream-copy approach stays close to constant time regardless of output
length (dominated by the audio encode and disk I/O).

### Caveats

- **Trim precision**: a `-c copy` cut can only land on a keyframe. A
  5-second clip typically has one keyframe at its start, so the result is
  exact in practice. With internal keyframes and irregular GOP structure
  the output could differ from `--duration` by a fraction of a second.
- **No per-loop variation**: without re-encoding you cannot apply
  crossfades, color drift or other per-repeat effects. That needs a real
  re-encode (or GPU encoding: NVENC / Quick Sync / VideoToolbox).
- **Codec consistency**: concat `-c copy` needs identical codec parameters
  across inputs. Guaranteed here since the same file repeats.
- **Container support**: works reliably with MP4/MOV (H.264/H.265). If the
  concat step errors, re-encode the clip once to a standard H.264 MP4 first.

---

## loop_video_random.py

```bash
# 60 minutes, second clip on screen ~10% of the time, with music
python3 loop_video_random.py --main main_5s.mp4 --second bird_4s.mp4 \
    --percent 10 --minutes 60 --music music.mp3 --output out.mp4

# Same random placement again
python3 loop_video_random.py --main main_5s.mp4 --second bird_4s.mp4 \
    --percent 10 --minutes 60 --seed 42 --output out.mp4
```

How it works:

1. The second clip is re-encoded once (seconds of work) to match the main
   clip's codec, size, frame rate, pixel format, profile and timescale,
   with black bars if its shape differs. The main clip is not touched.
2. **Plan** (`plan_sequence`): showings = `round(total * percent / second_len)`
   (by time, not count); main repeats = `ceil(remaining time / main_len)`.
   Each showing goes in a gap *before* a main repeat, so the video always
   ends on main and two showings are never adjacent. Gaps are split into
   equal sections with one random pick each: random but evenly spread.
3. Concat demuxer `-c copy` with `-t` = `--minutes`; the trim lands inside
   the last main repeat.
4. `--music` muxed as in `loop_video.py`. The clips' own audio is dropped;
   without `--music` the output is silent.

Details:

- The printed summary shows the actual share (e.g. 15 showings = 10.0%)
  and the seed; pass it as `--seed` to reproduce a placement.
- Max `--percent` is `second / (main + second)` (44% for 5s + 4s); above
  that it errors with the limit.
- **Different encoder settings**: the two clips' H.264 parameter sets
  usually differ. ffmpeg's concat + MP4 muxer then writes them in-band at
  each switch (verified: correct pictures even with a CAVLC main and CABAC
  second). Fine in ffmpeg, VLC, browsers and YouTube; a strict hardware
  player could glitch. As a safety net the first seam is test-decoded and,
  on errors, the main clip is re-encoded to the same settings and the join
  redone. Only H.264/H.265 main clips are supported.
- Switches between clips are hard cuts (no crossfade without re-encoding).

Verified: 10 minutes at 10% with a 5s red main and a 4s green 1280x720/25fps
second: 600.0s, exactly 60s green in 15 whole 4s runs, spread out, ends on
main, no decode errors, music present. Same `--seed` gives an identical
placement; too-high/zero percent and missing files give clear messages.

---

## crop_paste.py

Crops a **full-width** horizontal strip from `--source` and pastes it onto
`--target`. Without `--target` it only crops and saves the strip.

### Usage

```bash
# Bottom 30% of source, pasted at the bottom of target (all defaults)
python3 crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4

# Bottom 35% of source, squashed to 90% of its height, pasted at the bottom
python3 crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4 \
    --ratio 0.35 --anchor bottom --stretch 10 --paste-at bottom

# Strip appears 30s into the target, showing the source's own 30s mark
python3 crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4 \
    --show-at 30

# Top 25% of source, pasted with its top edge at 40% of the target's height,
# mixing both audio tracks
python3 crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4 \
    --ratio 0.25 --anchor top --paste-at 40% --audio mix

# Quick 60s test render instead of the full target
python3 crop_paste.py --source source.mp4 --target target.mp4 --output test.mp4 \
    --show-at 10 --duration 60 --preset ultrafast

# Crop only: save the strip at high quality for pasting later
python3 crop_paste.py --source source.mp4 --output strip.mp4
```

### Options

| Option | Default | Meaning |
|---|---|---|
| `--ratio` | `0.3` | Share of the **source's** height to cut (0–1). This sets the strip's size. |
| `--anchor` | `bottom` | Which part of the source to cut from: `bottom`, `top`, `center`. |
| `--stretch` | `0` | After cropping, **shrink** the strip's height by this percentage of its cropped height; width unchanged. `10` = 90% as tall (252px becomes 226px). Negative makes it taller. Must be < 100. |
| `--paste-at` | `bottom` | Where the strip's **top edge** goes on the target: `bottom` (flush with bottom edge), `top`, `center`, a pixel offset from the top (`100`), or a percentage of the target's height (`40%`). It only moves the strip; it never changes its size. |
| `--show-at` | `0` | Keep the strip hidden until this time: `30`, `12.5`, `1:30`, `1:02:00`. Both videos play from 0s, so `--show-at 30` shows the **source's own 30s mark** (not its beginning). Paste mode only; must be before the target ends. |
| `--duration` | full length | Only render the first part, e.g. `60` or `1:00`, for quick tests. Applies to the target in paste mode and the source in crop-only mode. Longer than the video → full length, with a note. |
| `--audio` | `target` | `target` (copied as-is), `source`, `mix`, or `none`. With `--show-at`, source audio is muted until the strip appears. `mix` falls back to whichever video has audio, with a warning. |
| `--crf` | 18 paste / 10 crop-only | x264 quality; lower is better. Crop-only uses near-lossless so the strip survives a later re-encode. |
| `--preset` | `medium` | x264 preset. Use `fast`/`ultrafast` for quicker renders of long targets. |

### How it works

Paste mode is a **single ffmpeg pass**, so the strip is encoded only once
(no generation loss from saving it first and pasting later):

1. Input 0 is the target; input 1 is the source with `-stream_loop -1`.
2. `crop` a full-width strip of `crop_h` from the source, then `scale` it to
   `strip_w x strip_h`, i.e. apply `--stretch` and scale to the target's
   width (aspect ratio kept).
3. `overlay` onto the target at `--paste-at`, with
   `enable='gte(t,SHOW_AT)'` when `--show-at` is set. Source audio gets
   `volume=0:enable='lt(t,SHOW_AT)'`.
4. `-t <target duration>` (or `--duration`, if shorter) sets the output
   length: a shorter source loops, a longer one is cut off.

**Pixel sizes are computed in Python** (`strip_size`) and passed to ffmpeg
as whole numbers rather than ffmpeg expressions, so the render and the
overflow warning always agree. `_even()` rounds away float noise before
flooring to an even number: `720 * 0.35` is `251.99999999999997`, which a
plain floor turns into 250 instead of 252.

Worked example (1280x720 source, 1920x1078 target,
`--ratio 0.35 --stretch 10`): crop 252px, stretch to 226px, scale x1.5 to
338px, which covers the bottom ~31% of the target.

### Caveats

- **Always re-encodes video**: cropping changes pixels, so the `-c copy`
  approach from `loop_video.py` can't be used.
- **Even dimensions**: every height is floored to an even number (H.264
  with yuv420p requires it).
- **Overflow**: a numeric `--paste-at` can push the strip past the bottom
  of the target. The script prints a warning with the exact pixel numbers
  but still renders. `100%` always pushes the strip fully off-screen; use
  `bottom` to sit flush.
- **Different aspect ratios**: the strip is scaled to the target's width
  keeping its shape, so `--ratio 0.35` covers exactly 35% of the target
  only when both videos have the same aspect ratio.
- **Source shorter than `--show-at`**: it has already looped by then, e.g. a
  20s source with `--show-at 30` appears at its 10s mark.
- **`--audio mix` volume**: `amix` scales each input down by default, so
  the mix is quieter than either track alone.

### Decisions made with the user

- Crop and paste happen in one pass (quality and speed); crop-only mode
  exists for reuse.
- `--show-at` keeps both videos in sync (source at the same time point as
  the target), not "start the source from its beginning". Both were built
  and tried; the user chose in-sync.
- `--stretch` is the user's name for the option even though positive values
  shrink the strip. A rename (e.g. `--shrink`) was offered but not requested.
- Offered but not built: `--hide-at` (strip disappears again), `--fade`
  (fade the strip in), `--strip-height` (force an exact share of the
  target's height).

---

## add_text.py

```bash
# Vietnamese text at the bottom (defaults: 5% font size, 4% margin, white with outline)
python3 add_text.py --input video.mp4 --output out.mp4 --text "Nhạc thư giãn"

# Two lines, top-right, gold, translucent box, visible from 10s to 60s
python3 add_text.py --input video.mp4 --output out.mp4 \
    --text "Nhạc thư giãn\nThư giãn và ngủ ngon" --position top-right --align right \
    --color "#FFD700" --box --show-at 10 --hide-at 60

# Text from a UTF-8 file, custom position, quick 30s test
python3 add_text.py --input video.mp4 --output test.mp4 --text-file title.txt \
    --position 10%,80% --font-size 72 --duration 30 --preset ultrafast
```

| Option | Default | Meaning |
|---|---|---|
| `--text` / `--text-file` | (one required) | The text. `\n` in `--text` is a line break; a file keeps its own line breaks. |
| `--font-size` | `5%` | Pixels (`48`) or percentage of the video's height (`5%`). |
| `--position` | `bottom` | Named: `top-left`, `top`, `top-right`, `left`, `center`, `right`, `bottom-left`, `bottom`, `bottom-right`. Or the text's top-left corner as `x,y` in pixels (`100,50`) or percentages (`10%,80%`). |
| `--margin` | `4%` | Gap from the edges for named positions: pixels or % of video height. |
| `--align` | `center` | How lines of multi-line text line up: `left`, `center`, `right`. |
| `--font` | `arial` | A `.ttf`/`.otf` path, or a file name looked up in the system fonts folders (`arialbd` = bold, `segoeui`, `tahoma`). |
| `--color` | `white` | Name or hex (`#FFD700`). |
| `--outline` / `--outline-color` | `2` / `black` | Outline thickness in pixels (0 = none). |
| `--box` / `--box-color` / `--box-padding` | off / `black@0.5` / font size ÷ 4 | Background box; `@0.5` is opacity. |
| `--shadow` / `--shadow-color` | `0` / `black@0.6` | Drop-shadow offset in pixels. |
| `--show-at` / `--hide-at` | `0` / end | When the text is visible. |
| `--duration` | full length | Only render the first part, for quick tests. |
| `--crf` / `--preset` | `18` / `medium` | x264 quality and speed. Audio is copied. |

How it works: a single ffmpeg `drawtext` pass. The text is written to a
temporary UTF-8 file and passed as `textfile=` with `expansion=none`, so
Vietnamese renders correctly and characters like `:` `'` `%` `\` need no
escaping. Font and text file paths are quoted with the drive colon escaped
(`filter_path`). Font size, margin and x,y percentages are converted to
pixels in Python after probing the video size. Named positions use
drawtext's `tw`/`th` (rendered text size), so centering is exact for any
text length. Multi-line alignment uses `text_align` (needs a recent
ffmpeg; the dev machine's September 2026 build has it).

Verified: Vietnamese accent marks with Arial and Segoe UI; special
characters; Vietnamese file paths; show/hide window (PSNR on the text
area before, during and after).

---

## cut_video.py

```bash
# Frame-accurate cut from 5s to 8s (re-encodes)
python3 cut_video.py --input source.mp4 --from 5 --to 8 --output clip.mp4

# Fast cut, no re-encode, snapped to keyframes
python3 cut_video.py --input source.mp4 --from 1:30 --to 2:00 --output clip.mp4 --copy
```

Times accept `SS`, `M:SS` or `H:MM:SS`.

## merge_video.py

```bash
python3 merge_video.py --clip source_10s.mp4 --split 5 --output rotated.mp4 \
    --transition crossfade --transition-duration 0.4
```

Rotates the clip to start at `--split`. `--transition`: `off` (hard cut),
`crossfade`, `interpolate` (minterpolate across the seam), `match-cut`
(searches near `--split` for the frame most similar to the clip's end via
SSIM). Re-encodes both pieces once for frame-exact cuts.

## merge_video2.py

```bash
# Join in the order given (re-encodes; any mix of videos)
python3 merge_video2.py --inputs intro.mp4 part1.mp4 part2.mp4 --output all.mp4

# Every .mp4 in a folder, sorted by name, no re-encoding
python3 merge_video2.py --inputs "clips/*.mp4" --output all.mp4 --copy

# Force output size and frame rate
python3 merge_video2.py --inputs a.mp4 b.mov --output all.mp4 --size 1920x1080 --fps 30

# All inputs in one folder: give the folder once, then plain file names
python3 merge_video2.py --folder D:/videos/project --inputs intro.mp4 part1.mp4 --output all.mp4

# Every video in the folder, sorted by name
python3 merge_video2.py --folder D:/videos/project --output all.mp4
```

- **`--folder`**: `--inputs` names (and wildcards) are looked up inside it.
  Without `--inputs`, joins every video in it (`.mp4 .mov .mkv .avi .webm
  .m4v .ts`), sorted by name, skipping the output file if it is saved
  there. `--output` is **not** relative to the folder; it's a normal path.

- **Default (re-encode)**: one ffmpeg `concat` filter pass. Each input is
  scaled to fit `--size` (default: first input's) with black bars to keep
  its shape, converted to `--fps` (default: first input's), and its audio
  padded/trimmed to exactly its video length; inputs without audio get
  silence. If no input has audio, the output has none.
- **`--copy`**: concat demuxer with `-c copy`, like `loop_video.py`. First
  checks that every input matches the first one (codec, profile, size,
  pixel format, frame rate, audio codec/rate/channels) and lists what
  differs if not.
- Wildcards in `--inputs` are expanded in Python (Windows' shell doesn't).
- Not built: transitions between clips (crossfades) — hard cuts only.

Verified: red/green/blue clips of different sizes (320x240, 640x360),
frame rates (30, 25), one without audio and one with audio longer than
its video: right order and timings (7.0s), letterboxed, silent during the
no-audio clip. `--copy` joins identical-format clips; mismatch, single
input, no wildcard match and output-equals-input give clear errors.

## last_frame.py

```bash
# Last frame as video_last.png next to the video
python3 last_frame.py --input video.mp4

# As JPEG (--quality 2 = best, 31 = worst)
python3 last_frame.py --input video.mp4 --output thumb.jpg --quality 2

# The frame at 8 seconds -> video_8s.png (also 8.5, 1:30, 1:02:00)
python3 last_frame.py --input video.mp4 --at 00:08
```

`--at` uses `-ss` before `-i` (frame-accurate: ffmpeg decodes up to the
exact time, it doesn't snap to a keyframe) and `-frames:v 1`, giving the
frame showing at that time. Times use `parse_timestamp` from
`cut_video.py`. A time at or past the end is an error that points to
leaving out `--at`. Verified on a clip with a single keyframe: `00:08` =
frame 240 at 30fps (PSNR inf, frame 239 is 18 dB), `8.5` = frame 255,
`1:30` = frame 2700.

Seeks to 3s before the end (`-sseof -3`) and decodes forward with
`-update 1`, so the image is overwritten by each frame and ends up holding
the last one that actually decodes. Seeking to "duration minus one frame"
is unreliable because the container duration can run past the last video
frame (e.g. longer audio). Verified pixel-identical (PSNR inf) to the true
last frame, including a clip whose audio is 1s longer than its video.

## no_sound.py

```bash
# Silences of 3 seconds or longer
python3 no_sound.py --input music.mp3 --period 3

# Also count quiet background hiss as silence
python3 no_sound.py --input music.mp3 --period 2 --noise -40

# Also save each part between the silences: parts/music_part01.mp3, ...
python3 no_sound.py --input music.mp3 --period 3 --output-folder parts

# Which parts are the same song?
python3 no_sound.py --input music.mp3 --period 3 --find-similar

# Save each song once, skipping repeats
python3 no_sound.py --input music.mp3 --period 3 --output-folder parts --unique-only
```

```
music.mp3 (01:34): silences of 3s or longer, quieter than -50 dB
    1. 00:09 - 00:13  (4.0s)
    2. 01:10 - 01:16  (5.5s)
    3. 01:30 - 01:34  (4.0s)
3 found, 13.5s silent in total
```

ffmpeg `silencedetect=noise=<--noise>dB:d=<--period>`; the
`silence_start`/`silence_end` lines are parsed from stderr. A silence that
runs to the end of the file has no `silence_end`, so the file's duration
is used. Times are rounded to the **nearest** second (MP3 edges land a
hair early, e.g. 8.998s, which rounding down would show as 00:08);
H:MM:SS past an hour. `--noise` accepts `-50` or `-50dB`; `--period`
accepts seconds or M:SS. Works on any file with audio, including videos.

Verified on a 94s MP3 with known gaps: 4s, 5.5s and a trailing 4s found
at the right times; a 2s gap skipped at `--period 3` and found at `1.5`;
a -48 dB hiss ignored at the default -50 and found at `--noise -40`.

**Splitting** (`--output-folder`, `--pad`, default 1s): `sound_parts`
takes the stretches between silences (skipping slivers under 0.05s, e.g.
before a silence at 0:00) and widens each by `--pad` into the neighbouring
silence, clamped to the file. So the first part has no lead-in if the
file starts with sound. Gaps shorter than `--period` don't split. Files
are `<input name>_partNN<input ext>`, folder created if missing. Audio
input is cut with `-c copy` (no quality loss; MP3 cuts land on ~26 ms
frames); video input is re-encoded (x264 CRF 18 + AAC) for frame-accurate
cuts. With `--period` below 2 × `--pad`, neighbouring parts share a little
silence.

**Cover art**: MP3s (e.g. downloaded from YouTube) often embed a cover
image, which ffprobe lists as a video stream with `disposition
attached_pic=1`. `is_real_video` doesn't count those as video (treating
one as video once made ffmpeg re-encode it to H.264 and fail with "Exactly
one MP3 audio stream is required"). The parts are audio only: **cover art
is dropped** (user's choice; keeping it was built and removed). Tags
(title etc.) still carry over.

Verified: the 94s MP3 splits into 0–10, 12–71 and 74.5–91 (each padded
end is silent at −91 dB, middle audible); `--pad 0` gives 9, 57 and
14.5s; a video starting with 3s of silence gives one part starting
exactly on source frame 60 (t=2s).

**Same song** (`--find-similar`, `--min-match` 80%, `--min-overlap` 15s):

```
Same song in more than one part (match 80%+ over 15s+), comparing 6 parts:
  Group 1: part 01 (00:00 - 00:40), part 03 (01:28 - 02:05), part 06 (03:22 - 04:02)
      part 01 ~ part 03: 92% match over 37s
      ...
```

- "Similar" means **the same recording** (a track repeated in a
  compilation, possibly cut differently or at another volume), not songs
  that sound alike in style. That was the assumed meaning; the user asked
  only "detect some songs are similar".
- ffmpeg's `chromaprint` muxer (`-f chromaprint -fp_format raw`)
  fingerprints the whole file in one pass: one 32-bit value per
  1365/11025 s (`FP_STEP`, ~0.124s). Parts are slices of that by time
  (verified: song positions land on the expected index). The last ~2.7s of
  a file get no values, so a part at the very end matches over a bit less
  than its length.
- Parts are compared **without padding** (`sound_parts(..., 0)`), since
  every silence fingerprints alike.
- `compare`: vote for the time offset where the most values match exactly,
  then at the top 5 offsets (±1 step) measure the share of equal bits over
  the overlap. Same recording ~90–100%; unrelated audio has no exact
  matches (0%) or ~50% by bits. A part starting mid-step fingerprints a
  little differently, hence 92% for an identical song.
- Matching pairs are merged into groups (`group_pairs`). Pure Python,
  `int.bit_count` with a fallback for Python < 3.10.

Verified: synthetic songs A/B/C in 6 parts (A, B, A from 3s at half
volume, C, last 25s of B, A) → groups {1,3,6} and {2,5}, C alone, all
non-matches 0%, ~1s total. Real music from `videos/` (china_music_1a and
samurai_2): a 60s clip and the same clip from 10s in at 60% volume → 100%;
a different passage of the same video and the other video → 0%.

**Unique songs only** (`--unique-only`, needs `--output-folder`): runs the
same check (and prints the same report) before saving, then saves one part
per group and prints `part 03 skipped: same song as part 01` for the rest.
It keeps the **longest** copy (the full song rather than a partial repeat),
the earliest on a tie (lengths compared to the second). Saved files keep
their original part numbers, so there are gaps (`_part01`, `_part02`,
`_part04`) that match the report. Verified: the 6-part compilation saves
parts 1, 2, 4; with a 45s partial copy first and the 60s full copy later,
the later one is kept; with no repeats everything is saved.

## clean_info.py

```bash
python3 clean_info.py --input song.mp3                  # -> song_clean.mp3
python3 clean_info.py --input song.mp3 --output clean.mp3
python3 clean_info.py --input song.mp3 --output song.mp3   # in place (explicit)
python3 clean_info.py --folder D:/music --output-folder D:/music_clean
```

```
dirty.mp3 -> dirty_clean.mp3
    removed: title, artist, comment, date, encoder, cover art, ID3v1 tag
```

One ffmpeg pass per file: `-map 0:a:0 -c:a copy -map_metadata -1
-map_chapters -1 -id3v2_version 0 -write_id3v1 0 -fflags +bitexact
-flags:a +bitexact` (bitexact drops the `Lavf` encoder stamp). Written to a
temp file in the output folder, then swapped in, so in-place works.

- **Kept on purpose** (agreed with the user in the proposal): the
  Xing/Info + LAME header in the first frame (removing it with
  `-write_xing 0` changed the decoded audio and added 0.036s; it holds
  gapless-playback info, nothing about the song), and `LAME…` filler text
  inside the audio frames (part of the encoded sound; re-encoding would
  cost quality and write new markers anyway).
- MP3 only: other codecs are reported as an error for that file.
- Never overwrites an original unless asked: default `<name>_clean.mp3`;
  `--output-folder` equal to the input folder is refused; in place only via
  `--output <same path>`. `--folder` without `--output-folder` skips
  `*_clean.mp3` so re-runs don't clean the results again.
- Folder mode continues past a failing file, prints `Done: N of M
  cleaned`, and returns 1 if any failed.

Verified: tags (incl. Japanese title), cover art, 2 chapters and ID3v1 all
removed; file starts with the MP3 frame sync (`ff fb`); audio MD5
identical to the original; Vietnamese file names; in-place; re-runs.

---

## Testing

There is no automated test suite. Features were verified by rendering short
clips and measuring the output:

- **Make short test clips** from `videos/` (e.g. 10s target, 4s source; 40s
  clips when testing timing) with `-preset ultrafast`, in a temp folder.
- **Sizes and durations**: `ffprobe -show_entries stream=width,height -show_entries format=duration`.
- **Where the strip is**: grab a frame from the output and the target at the
  same time, `blend=all_mode=difference`, and find which rows differ.
- **Which source frame is shown**: `psnr` between the pasted strip and the
  source's cropped frame at candidate times. Around 50 dB means a match;
  around 35 dB means a different frame.
- **Audio muting**: `volumedetect` on time ranges before and after
  `--show-at` (about −91 dB means silent).
