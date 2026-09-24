# ffmpeg Video Utilities

Small Python command-line tools around `ffmpeg` for making long background
videos: loop a short clip into hours of footage with music, cut or rotate
clips, and crop a strip out of one video and paste it onto another.

## Files

| File | What it does |
|---|---|
| `loop_video.py` | Loops a short clip into a long video (by `--duration` or to the length of `--music`) with **no video re-encoding**. Also holds the shared helpers the other scripts import (`run`, `run_with_spinner`, `run_with_progress`, `probe_duration`, `check_dependencies`). |
| `crop_paste.py` | Crops a full-width horizontal strip from a source video and pastes it onto a target video in one pass, or saves the strip on its own. |
| `cut_video.py` | Cuts a time segment (`--from` / `--to`). Frame-accurate re-encode by default, `--copy` for a fast keyframe-snapped cut. Also provides `parse_timestamp` (used by `crop_paste.py`). |
| `merge_video.py` | Despite the name, it **rotates** a clip's starting point: splits at `--split` and plays `[split, end)` then `[0, split)`, with optional seam handling (`crossfade`, `interpolate`, `match-cut`). It does not merge two videos. |
| `run_loop.py` | Empty placeholder. |
| `docs/` | Feature request notes (e.g. `cut-video-by-proportion.md`, which led to `crop_paste.py`). |
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
