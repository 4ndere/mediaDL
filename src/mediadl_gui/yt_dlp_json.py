from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, Iterable


def _extract_video_heights(info: dict[str, Any]) -> list[int]:
    formats: Iterable[dict[str, Any]] = info.get("formats") or []
    heights: set[int] = set()
    for f in formats:
        if f.get("vcodec") and f.get("vcodec") != "none":
            h = f.get("height")
            if isinstance(h, int) and h > 0:
                heights.add(h)
    return sorted(heights, reverse=True)


def _first_entry_info(info: dict[str, Any]) -> dict[str, Any]:
    if "entries" in info and isinstance(info["entries"], list) and info["entries"]:
        first = info["entries"][0]
        if isinstance(first, dict):
            return first
    return info


def _run_yt_dlp_json(url: str, cookies_path: str | None) -> dict[str, Any]:
    args = [sys.executable, "-m", "yt_dlp", "-J", "--no-color", "--no-warnings", url]
    if cookies_path:
        args[4:4] = ["--cookies", cookies_path]
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        msg = proc.stdout.strip() or proc.stderr.strip() or "yt-dlp failed"
        raise RuntimeError(msg)
    data = json.loads(proc.stdout)
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected yt-dlp JSON output")
    return data

