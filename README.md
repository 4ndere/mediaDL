<<<<<<< HEAD

# mediaDL

# programa jaja

## Features

- Download videos to `mp4/mkv/webm` (optionally with/without audio)
- Download audio to `mp3/m4a/opus` with selectable bitrate (including ultra-low “bad quality” options)
- Quality selection:
  - Video: resolution presets (with optional post-scale if the source doesn’t provide very low resolutions)
  - Audio: bitrate via “Audio quality”
- Optional video bitrate limiter (post-process) to further shrink file size
- Optional “Keep original metadata”
- Optional custom output filename (rename without extension)
- Convert module:
  - Drag & drop input
  - Output folder chooser (same style as Download)
  - Output rename
  - Preview thumbnail (image or a single extracted video frame)
  - Blocks invalid conversions (e.g. images → mp4)
- About tab with links

## How It Works

- **Downloader**
  - Uses `yt-dlp` in a background thread (`python -m yt_dlp ...`) and parses progress from its output.
  - If you pick a very low video resolution that the source can’t provide, the app downloads the closest available stream and then runs an ffmpeg post-process to scale down the final file.
  - For audio-only modes, the app selects a low-quality audio stream (when you pick a low bitrate) and then re-encodes to the selected bitrate.
- **Converter**
  - Uses `ffmpeg` to transcode or extract frames depending on the selected output.
  - Uses a lightweight preview (single image / single video frame) to avoid loading full videos into memory.

## DOWNLOAD BEFORE USING!

- Python 3.10+ recommended
- `ffmpeg` in PATH (recommended; required for Convert, and for some post-processing)
- Windows/macOS/Linux should work (tested mostly on Windows)

## Setup (Windows)

on cmd, travel to folder and escribe "python run.py"
