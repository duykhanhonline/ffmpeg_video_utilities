# ffmpeg Video Utilities: User Guide

A set of small command-line tools for making long background videos
(relaxation, study, music channels) and preparing their music. Each tool
is one Python script that does one job.

## Contents

- [Getting started](#getting-started)
- [Which script do I need?](#which-script-do-i-need)
- Video
  - [loop_video.py: loop a short clip into a long video](#loop_videopy)
  - [loop_video_random.py: loop with a second clip shown at random](#loop_video_randompy)
  - [merge_video2.py: join several videos into one](#merge_video2py)
  - [cut_video.py: cut out a part of a video](#cut_videopy)
  - [merge_video.py: make a clip start from a different point](#merge_videopy)
  - [crop_paste.py: cut a strip from one video, paste it on another](#crop_pastepy)
  - [add_text.py: put a title or text on a video](#add_textpy)
  - [last_frame.py: save a frame as an image](#last_framepy)
- Audio
  - [no_sound.py: find silences, split into songs, find repeats](#no_soundpy)
  - [clean_info.py: remove tags and cover art from MP3s](#clean_infopy)
- [Example workflow: a 2-hour relaxation video](#example-workflow-a-2-hour-relaxation-video)
- [Troubleshooting](#troubleshooting)

---

## Getting started

### What you need

- **Python 3.8 or newer**
- **ffmpeg** (which includes `ffprobe`). On Windows:

  ```
  winget install ffmpeg
  ```

  On macOS: `brew install ffmpeg`. On Debian/Ubuntu: `sudo apt-get install ffmpeg`.

Check that both work by opening a new terminal and running `ffmpeg -version`
and `python --version`.

### Running a script

Open a terminal in the project folder and run:

```
python <script>.py --option value ...
```

On macOS/Linux use `python3` instead of `python`. Every script explains
its options with `--help`, for example `python no_sound.py --help`.

The examples in this guide put long commands on one line. Paths with
spaces need quotes: `--input "D:/My Music/song.mp3"`. Vietnamese and
other non-English file names work.

### Writing times

Wherever a script asks for a time, you can write it in any of these ways:

| You write | Means |
|---|---|
| `8` or `8.5` | 8 or 8.5 seconds |
| `00:08` or `1:30` | minutes:seconds |
| `1:02:00` | hours:minutes:seconds |

(Exception: `merge_video.py --split` takes plain seconds only.)

### Quick test renders

Several video scripts take `--duration 60` (render only the first minute)
and `--preset ultrafast` (encode quickly at lower efficiency). Use them to
check a result in seconds before rendering a multi-hour video.

### Your files are safe

No script changes its input files. Results always go to the output name
you give (or a new name the script picks). Pick an output name that
doesn't already exist, because existing files with that name are replaced.

---

## Which script do I need?

| I want to... | Use |
|---|---|
| Turn a 5-second clip into a 2-hour video with music | [`loop_video.py`](#loop_videopy) |
| ...and have another clip appear now and then | [`loop_video_random.py`](#loop_video_randompy) |
| Join several videos one after another | [`merge_video2.py`](#merge_video2py) |
| Keep only part of a video (e.g. 1:30 to 2:00) | [`cut_video.py`](#cut_videopy) |
| Make a looping clip start from its middle | [`merge_video.py`](#merge_videopy) |
| Take the bottom of one video and put it on another | [`crop_paste.py`](#crop_pastepy) |
| Write a title on a video | [`add_text.py`](#add_textpy) |
| Save a thumbnail / a frame as an image | [`last_frame.py`](#last_framepy) |
| Find the silent gaps in a music file | [`no_sound.py`](#no_soundpy) |
| Split a long music mix into separate songs | [`no_sound.py --output-folder`](#split-into-separate-files) |
| Find songs that repeat in a mix, keep each once | [`no_sound.py --unique-only`](#find-repeated-songs) |
| Remove title, artist, cover art etc. from MP3s | [`clean_info.py`](#clean_infopy) |

---

## loop_video.py

Turns a short clip into a long video by repeating it, and adds music.
**Very fast**: a 2-hour video takes minutes, not hours, because the video
is never re-encoded. The repeats are joined as they are.

```
# 2 hours (7200 seconds) with music
python loop_video.py --clip clip_5s.mp4 --music music.mp3 --duration 7200 --output video_2h.mp4

# Exactly as long as the music
python loop_video.py --clip clip_5s.mp4 --music music.mp3 --match-music --output video.mp4

# No music
python loop_video.py --clip clip_5s.mp4 --duration 7200 --output video_2h.mp4
```

| Option | Default | Meaning |
|---|---|---|
| `--clip` | *(required)* | The short clip to repeat |
| `--output` | *(required)* | The video to create |
| `--duration` | | Length in **seconds** (`7200` = 2 hours) |
| `--match-music` | | Make the video as long as `--music` instead of using `--duration` |
| `--music` | none | Music file. If shorter than the video, it repeats |
| `--keep-temp` | off | Keep the in-between files (for troubleshooting) |

Give either `--duration` or `--match-music`, not both.

**Good to know**

- The clip should loop smoothly on its own (its last frame flows into its
  first). The script repeats it exactly; it can't blend the joins.
- Works best with ordinary MP4/MOV clips (H.264/H.265). If you get an
  error at the joining step, convert the clip to a standard MP4 first,
  for example with [`cut_video.py`](#cut_videopy) (without `--copy`).
- With `--match-music` the video can end a fraction of a second before
  the music.

---

## loop_video_random.py

Like `loop_video.py`, but a **second clip** appears at random moments for
a share of the time you choose, e.g. a bird flying past 10% of the time.
The video always **ends on the main clip**. Also very fast.

```
# 60 minutes, second clip on screen about 10% of the time, with music
python loop_video_random.py --main main_5s.mp4 --second bird_4s.mp4 --percent 10 --minutes 60 --music music.mp3 --output video.mp4

# Repeat exactly the same random placement as an earlier run
python loop_video_random.py --main main_5s.mp4 --second bird_4s.mp4 --percent 10 --minutes 60 --seed 42 --output video.mp4
```

| Option | Default | Meaning |
|---|---|---|
| `--main` | *(required)* | The clip shown most of the time |
| `--second` | *(required)* | The clip shown at random moments |
| `--minutes` | *(required)* | Length of the video in minutes (`90.5` works) |
| `--output` | *(required)* | The video to create |
| `--percent` | `10` | Share of the **time** the second clip is on screen |
| `--music` | none | Music for the soundtrack (repeats if shorter) |
| `--seed` | random | A number that reproduces a placement (see below) |

The script prints what it did, for example:

```
60 minutes: second clip shown 90 times (10.0% of the time), seed 482913
```

**Good to know**

- **Placement**: random, but spread out over the whole video. The second
  clip always plays in full and never twice in a row.
- **Seed**: each run places the clip differently. If you like a result,
  run again with the printed seed (`--seed 482913`) to get the same
  placement, for example after changing the music.
- **Sound**: comes only from `--music`. The clips' own sound is not used,
  so without `--music` the video is silent.
- **The second clip can be any size or frame rate.** It's converted once
  to match the main clip (with black bars if its shape differs).
- **Maximum percent**: about `second ÷ (main + second)` (44% for a 5s main
  and a 4s second clip). Ask for more and the script tells you the limit.
- Switches between the clips are hard cuts. A smooth result needs clips
  that start and end on similar frames.
- The two clips are joined without re-encoding. Common players
  (ffmpeg-based players, VLC, browsers, YouTube) handle this; a very
  strict older hardware player might glitch at the switches.

---

## merge_video2.py

Joins **two or more videos** one after another into a single video.
(For the older `merge_video.py`, which does something else, see
[below](#merge_videopy).)

```
# In the order given
python merge_video2.py --inputs intro.mp4 part1.mp4 part2.mp4 --output all.mp4

# Files in one folder: name the folder once
python merge_video2.py --folder D:/videos/project --inputs intro.mp4 part1.mp4 --output all.mp4

# Every video in a folder, sorted by name
python merge_video2.py --folder D:/videos/project --output all.mp4

# Every .mp4 matching a pattern, sorted by name
python merge_video2.py --inputs "clips/*.mp4" --output all.mp4

# Force a size and frame rate
python merge_video2.py --inputs a.mp4 b.mov c.mkv --output all.mp4 --size 1920x1080 --fps 30
```

| Option | Default | Meaning |
|---|---|---|
| `--inputs` | | The videos, in order. Patterns like `"clips/*.mp4"` work |
| `--folder` | | Folder holding the inputs. Alone: joins every video in it |
| `--output` | *(required)* | The video to create (a normal path, not inside `--folder`) |
| `--copy` | off | Fast join without re-encoding; only for videos in the same format |
| `--size` | first video's | Output size, e.g. `1920x1080` |
| `--fps` | first video's | Output frame rate, e.g. `30` |
| `--crf` | `18` | Quality: lower = better and larger |
| `--preset` | `medium` | Encoding speed: `ultrafast` … `slow` |

**Good to know**

- **Any mix of videos works** (by default): each is fitted into the output
  size with black bars, converted to the same frame rate, and given
  silence if it has no sound.
- **`--copy`** is much faster, but only works when all videos have the same
  format, e.g. pieces cut from the same video. If they don't, the script
  tells you exactly what differs; just run again without `--copy`.
- Changes between videos are hard cuts (no crossfade).
- "Folder" mode picks up `.mp4 .mov .mkv .avi .webm .m4v .ts` files and
  sorts them by name, so number them (`01_intro.mp4`, `02_main.mp4`).

---

## cut_video.py

Keeps only the part of a video between two times.

```
# From 5s to 8s, frame-exact
python cut_video.py --input video.mp4 --from 5 --to 8 --output clip.mp4

# From 1:30 to 2:00, fast but not frame-exact
python cut_video.py --input video.mp4 --from 1:30 --to 2:00 --output clip.mp4 --copy
```

| Option | Default | Meaning |
|---|---|---|
| `--input` | *(required)* | The video |
| `--from` / `--to` | *(required)* | Start and end time |
| `--output` | *(required)* | The clip to create |
| `--copy` | off | Much faster, no quality loss, but the cut moves to the nearest keyframe, which can be a few seconds off |

Use the default for short clips you want to loop. Use `--copy` to quickly
cut a long piece where a second or two doesn't matter.

---

## merge_video.py

Despite its name, this does **not** join videos (use
[`merge_video2.py`](#merge_video2py) for that). It **re-arranges one clip**
so it starts from a different point: with `--split 5`, a 10-second clip
plays seconds 5–10, then 0–5.

This is useful when a clip doesn't loop smoothly (common with
AI-generated clips). The rough join, where the clip's end runs into its
start, moves to the middle, where you can smooth it with a transition.
The new loop point is seamless, because the frames that meet there
(just before and at `--split`) were already next to each other in the
original.

```
python merge_video.py --clip clip_10s.mp4 --split 5 --output rotated.mp4
python merge_video.py --clip clip_10s.mp4 --split 5 --output rotated.mp4 --transition crossfade --transition-duration 0.4
```

| Option | Default | Meaning |
|---|---|---|
| `--clip` | *(required)* | The clip |
| `--split` | *(required)* | Where the new start is, in **seconds** (e.g. `5` or `4.5`) |
| `--output` | *(required)* | The clip to create |
| `--transition` | `off` | How to handle the join in the middle (see below) |
| `--transition-duration` | `0.4` | Seconds of blending for `crossfade` / `interpolate` |
| `--match-window` | `1.0` | Seconds around `--split` to search for `match-cut` |
| `--crf` | `16` | Quality: lower = better |
| `--preset` | `slow` | Encoding speed (the clip is short, so slow is fine) |
| `--keep-temp` | off | Keep the in-between files |

Transitions:

| `--transition` | What it does | Length |
|---|---|---|
| `off` | Hard cut | Same as the input |
| `crossfade` | Dissolves one side into the other; simple and reliable, can double-expose fast motion | Shorter by `--transition-duration` |
| `interpolate` | Creates in-between frames across the join; can look smoother for moving subjects, can also warp | Same as the input |
| `match-cut` | No blending: looks near `--split` for the frame that best matches the clip's end and cuts there | Same as the input |

---

## crop_paste.py

Cuts a **full-width strip** (for example the bottom 30%) out of one video
and pastes it onto another, in one go. It can also just save the strip.

```
# Bottom 30% of source.mp4, pasted at the bottom of target.mp4
python crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4

# Bottom 35%, made 10% less tall, pasted at the bottom
python crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4 --ratio 0.35 --stretch 10

# The strip appears 30 seconds in
python crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4 --show-at 30

# Top 25%, its top edge at 40% of the target's height, both soundtracks mixed
python crop_paste.py --source source.mp4 --target target.mp4 --output out.mp4 --ratio 0.25 --anchor top --paste-at 40% --audio mix

# Quick 60-second test
python crop_paste.py --source source.mp4 --target target.mp4 --output test.mp4 --duration 60 --preset ultrafast

# Only save the strip (high quality, for pasting later)
python crop_paste.py --source source.mp4 --output strip.mp4
```

| Option | Default | Meaning |
|---|---|---|
| `--source` | *(required)* | The video to cut the strip from |
| `--target` | none | The video to paste onto. Leave out to only save the strip |
| `--output` | *(required)* | The video to create |
| `--ratio` | `0.3` | How much of the source's height to cut (`0.3` = 30%) |
| `--anchor` | `bottom` | Cut from the `bottom`, `top` or `center` of the source |
| `--stretch` | `0` | Make the strip **less tall** by this percent (`10` = 90% as tall). Negative makes it taller |
| `--paste-at` | `bottom` | Where the strip's **top edge** goes: `bottom`, `top`, `center`, pixels from the top (`100`), or percent of the height (`40%`) |
| `--show-at` | `0` | Hide the strip until this time |
| `--duration` | full length | Only render the first part (for tests) |
| `--audio` | `target` | Sound to keep: `target`, `source`, `mix` or `none` |
| `--crf` | `18` (strip only: `10`) | Quality: lower = better |
| `--preset` | `medium` | Encoding speed |

**Good to know**

- The strip is resized to the target's width (keeping its shape), so
  `--ratio 0.35` covers exactly 35% of the target only if both videos have
  the same shape (e.g. both 16:9).
- **Both videos play in sync.** With `--show-at 30`, the strip shows the
  source at its own 30-second mark, not from its beginning.
- A source shorter than the target repeats; a longer one is cut off.
- If `--paste-at` pushes the strip past the bottom edge, you get a warning
  with the numbers (the video is still made). To sit flush at the bottom,
  use `bottom`, not `100%`.
- With `--audio source` or `mix` and `--show-at`, the source's sound is
  silent until the strip appears.
- `--audio mix` is quieter than either sound on its own.

---

## add_text.py

Writes a title or any text (one or several lines, Vietnamese supported)
onto a video.

```
# Text at the bottom: white with a black outline
python add_text.py --input video.mp4 --output out.mp4 --text "Nhạc thư giãn"

# Two lines, top right, gold, on a see-through box, visible from 10s to 60s
python add_text.py --input video.mp4 --output out.mp4 --text "Nhạc thư giãn\nThư giãn và ngủ ngon" --position top-right --align right --color "#FFD700" --box --show-at 10 --hide-at 60

# Text from a file, at a custom spot, quick 30-second test
python add_text.py --input video.mp4 --output test.mp4 --text-file title.txt --position 10%,80% --font-size 72 --duration 30 --preset ultrafast
```

| Option | Default | Meaning |
|---|---|---|
| `--input` / `--output` | *(required)* | The video and the result |
| `--text` | | The text. `\n` starts a new line |
| `--text-file` | | Or: read the text from a file (save it as UTF-8) |
| `--font-size` | `5%` | Pixels (`48`) or percent of the video's height (`5%`) |
| `--position` | `bottom` | `top-left`, `top`, `top-right`, `left`, `center`, `right`, `bottom-left`, `bottom`, `bottom-right`; or the text's top-left corner as `x,y` in pixels (`100,50`) or percent (`10%,80%`) |
| `--margin` | `4%` | Distance from the edges for the named positions |
| `--align` | `center` | How several lines line up: `left`, `center`, `right` |
| `--font` | `arial` | A font file, or a font name from Windows' Fonts folder: `arialbd` (Arial Bold), `segoeui`, `tahoma` … |
| `--color` | `white` | A color name or code like `#FFD700` |
| `--outline` / `--outline-color` | `2` / `black` | Outline thickness in pixels (`0` = none) and color |
| `--box` | off | Draw a box behind the text |
| `--box-color` | `black@0.5` | Box color; `@0.5` = half see-through |
| `--box-padding` | ¼ of the font size | Space between text and box edge, in pixels |
| `--shadow` / `--shadow-color` | `0` / `black@0.6` | Drop shadow distance in pixels, and color |
| `--show-at` / `--hide-at` | `0` / the end | When the text appears and disappears |
| `--duration` | full length | Only render the first part (for tests) |
| `--crf` / `--preset` | `18` / `medium` | Quality and encoding speed |

**Good to know**

- Use one of `--text` or `--text-file`. A file is easiest for long text or
  text with quotes.
- For Vietnamese, Arial (default), Segoe UI and Tahoma all show the accents
  correctly. Some decorative fonts don't.
- Sizes in `%` scale with the video, so the same command looks the same on
  a 720p and a 4K video.
- The sound is kept unchanged.

---

## last_frame.py

Saves a frame from a video as an image, by default the **last frame**.
Handy for thumbnails or for checking that a loop's end matches its start.

```
# Last frame -> video_last.png
python last_frame.py --input video.mp4

# The frame at 8 seconds -> video_8s.png
python last_frame.py --input video.mp4 --at 00:08

# As a JPEG
python last_frame.py --input video.mp4 --at 1:30 --output thumb.jpg
```

| Option | Default | Meaning |
|---|---|---|
| `--input` | *(required)* | The video |
| `--at` | the last frame | Take the frame at this time instead |
| `--output` | `<video>_last.png` or `<video>_8s.png` | Image to save: `.png` (best quality) or `.jpg` (smaller) |
| `--quality` | `2` | JPEG quality: `2` (best) to `31` (worst). Ignored for PNG |

The frame is exact: `--at 00:08` gives the frame showing at exactly 8
seconds. A time past the end of the video gives an error; leave out `--at`
for the last frame.

---

## no_sound.py

Works on a **music file** (MP3, WAV, M4A …) or the sound of a video. It
can:

1. list the silent gaps,
2. split the file into separate parts at those gaps, and
3. find parts that are the same song, and keep each song only once.

### Find the silences

```
# Silences of 3 seconds or longer
python no_sound.py --input music.mp3 --period 3
```

```
music.mp3 (01:34): silences of 3s or longer, quieter than -50 dB
    1. 00:09 - 00:13  (4.0s)
    2. 01:10 - 01:16  (5.5s)
    3. 01:30 - 01:34  (4.0s)
3 found, 13.5s silent in total
```

| Option | Default | Meaning |
|---|---|---|
| `--input` | *(required)* | The music file or video |
| `--period` | `3` | Only list silences at least this many seconds long |
| `--noise` | `-50` | How quiet counts as silence, in dB. Use `-40` to also count soft background hiss; `-60` for only true silence |

Times are rounded to the nearest second; the exact length is in brackets.

### Split into separate files

Add `--output-folder` to also save every part **between** the silences as
its own file: `music_part01.mp3`, `music_part02.mp3` …

```
python no_sound.py --input music.mp3 --period 3 --output-folder parts
```

| Option | Default | Meaning |
|---|---|---|
| `--output-folder` | | Folder for the parts (created if needed) |
| `--pad` | `1` | Seconds of silence kept before and after each part, so songs don't start or stop abruptly. `0` cuts tight |

- Parts are split only at silences at least `--period` long, so short
  pauses inside a song don't split it. If songs in your mix are separated
  by short gaps, lower `--period` (e.g. `1.5`).
- Music files are cut **without re-encoding** (no quality loss). Videos are
  re-encoded so the cuts are frame-exact.
- Cover art isn't copied into the parts. Title and other tags are.

### Find repeated songs

Long mixes often play the same song more than once. `--find-similar`
lists which parts are the same song, even if one copy is cut differently,
is only part of the song, or is louder or quieter.

```
python no_sound.py --input music.mp3 --period 3 --find-similar
```

```
Same song in more than one part (match 80%+ over 15s+), comparing 6 parts:
  Group 1: part 01 (00:00 - 00:40), part 03 (01:28 - 02:05), part 06 (03:22 - 04:02)
      part 01 ~ part 03: 92% match over 37s
      ...
  Group 2: part 02 (00:44 - 01:24), part 05 (02:53 - 03:18)
      part 02 ~ part 05: 96% match over 25s
```

To **save each song only once**, use `--unique-only` together with
`--output-folder`:

```
python no_sound.py --input music.mp3 --period 3 --output-folder parts --unique-only
```

```
  part 01 00:00 - 00:41 done
  part 02 00:43 - 01:25 done
  part 03 skipped: same song as part 01
  part 04 02:08 - 02:50 done
  ...
```

| Option | Default | Meaning |
|---|---|---|
| `--find-similar` | off | List the repeated songs |
| `--unique-only` | off | With `--output-folder`: skip repeats, keep the **longest** copy of each song (the first one if equally long) |
| `--min-match` | `80` | How alike two parts must be, in percent. The same song usually scores 90–100 |
| `--min-overlap` | `15` | Seconds that must match, so short coincidences don't count |

- Part numbers match the file names, so skipped repeats leave gaps
  (`_part01`, `_part02`, `_part04`).
- It finds the **same recording**, not different songs that sound alike in
  style. A song played live versus the studio version, or in a different
  key, counts as different.
- The check reads through the whole file once, so a 3-hour file takes
  roughly half a minute or so, depending on the computer.

---

## clean_info.py

Removes everything from MP3 files **except the sound**: title, artist,
album, comments (often a website or YouTube link), dates, the encoder
note, cover art, chapters and the old ID3v1 tag. The sound is copied,
not re-encoded, so it stays **exactly** the same.

```
# One file -> song_clean.mp3 next to it
python clean_info.py --input song.mp3

# Choose the output name
python clean_info.py --input song.mp3 --output clean.mp3

# Clean the file itself (replace it)
python clean_info.py --input song.mp3 --output song.mp3

# Every .mp3 in a folder, into another folder (same file names)
python clean_info.py --folder D:/music --output-folder D:/music_clean
```

```
dirty.mp3 -> dirty_clean.mp3
    removed: title, artist, comment, date, encoder, cover art, ID3v1 tag
```

| Option | Default | Meaning |
|---|---|---|
| `--input` | | One MP3 file |
| `--folder` | | Or: every `.mp3` in this folder |
| `--output` | `<name>_clean.mp3` | With `--input`: where to save. The input's own name replaces it |
| `--output-folder` | next to each file, as `<name>_clean.mp3` | Folder for the cleaned files (created if needed) |

**Good to know**

- Originals are never replaced unless you ask with `--output <same name>`.
  An `--output-folder` that is the input folder itself is refused.
- Running a folder again skips the `_clean` files from the last run.
- If one file in a folder fails (e.g. it isn't really an MP3), the rest are
  still cleaned and the problem file is reported.
- MP3 only. Two technical parts stay on purpose: a small header that tells
  players where the sound starts and ends, and the encoder's name built
  into the sound data itself. Neither says anything about the song, and
  removing them would change the sound.

---

## Example workflow: a 2-hour relaxation video

1. **Clean the music** you downloaded:

   ```
   python clean_info.py --input "zen mix.mp3" --output music.mp3
   ```

2. *(Optional)* **Check the mix for repeated songs** and silent gaps:

   ```
   python no_sound.py --input music.mp3 --find-similar
   ```

3. **Prepare the loop clip**: cut 10 seconds from a longer video, then
   check that its last frame matches its first:

   ```
   python cut_video.py --input nature.mp4 --from 1:05 --to 1:15 --output clip.mp4
   python last_frame.py --input clip.mp4 --at 0 --output first.png
   python last_frame.py --input clip.mp4 --output last.png
   ```

   If the two images differ a lot, use [`merge_video.py`](#merge_videopy)
   with a `crossfade` to hide the join.

4. **Make the long video**, as long as the music:

   ```
   python loop_video.py --clip clip.mp4 --music music.mp3 --match-music --output long.mp4
   ```

   Or with a second clip appearing now and then:

   ```
   python loop_video_random.py --main clip.mp4 --second bird.mp4 --percent 10 --minutes 120 --music music.mp3 --output long.mp4
   ```

5. **Add a title**. Test first, then render the whole video:

   ```
   python add_text.py --input long.mp4 --output test.mp4 --text "Nhạc thư giãn" --duration 30 --preset ultrafast
   python add_text.py --input long.mp4 --output final.mp4 --text "Nhạc thư giãn"
   ```

   Adding text re-encodes the whole video, so this step takes much longer
   than step 4 on a 2-hour video. `--preset fast` speeds it up.

6. **Make a thumbnail**:

   ```
   python last_frame.py --input final.mp4 --at 0:05 --output thumbnail.jpg
   ```

---

## Troubleshooting

| Message or problem | What to do |
|---|---|
| `'ffmpeg' not found on PATH` | Install ffmpeg (see [Getting started](#getting-started)), then open a **new** terminal |
| `error: argument --at: invalid timestamp` | Write times as `8`, `1:30` or `1:02:00` |
| `input not found` | Check the path; put it in quotes if it contains spaces |
| `--copy needs every input in the same format` (merge_video2) | Run again without `--copy` |
| `--percent ... try at most about 44%` (loop_video_random) | Lower `--percent`, or use a shorter second clip |
| `--at ... is past the end of the video` (last_frame) | Use an earlier time, or leave out `--at` for the last frame |
| A video plays but glitches where the second clip appears (loop_video_random) | Try another player. If it must work on that player, ask the maintainer: an option to re-encode the main clip once (so both clips share the same settings) can be added |
| no_sound finds no silences | Lower `--period`, or raise `--noise` (e.g. `-40`) if the gaps have background noise |
| no_sound splits in the middle of songs | Raise `--period` above the length of pauses inside songs |
| no_sound doesn't group two copies of a song | Lower `--min-match` (e.g. `70`) or `--min-overlap` |
| `this is aac audio, not MP3` (clean_info) | clean_info works on MP3s only |
| loop_video fails at the joining step | Convert the clip to a standard MP4 first: `python cut_video.py --input clip.mov --from 0 --to <length> --output clip.mp4` |


## download text
yt-dlp -f "bv*+ba/b" --merge-output-format mp4 -o "%(id)s.%(ext)s" "REEL_URL"