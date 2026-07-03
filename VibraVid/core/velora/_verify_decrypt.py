# 05.05.26

import logging
import subprocess
from pathlib import Path
from typing import Tuple

from VibraVid.utils.os import os_manager
from VibraVid.setup import get_ffprobe_path
from VibraVid.setup import get_mp4dump_path



logger = logging.getLogger(__name__)
_MP4DUMP_SCAN_BYTES = 1 * 1024 * 1024  # 1 MB


def _ffprobe_streams(ffprobe: str, file_path: str) -> Tuple[bool, str]:
    """Return (ok, message). ok=True means at least one decodable stream."""
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_streams",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "ffprobe timed out"
    
    except Exception as exc:
        return False, f"ffprobe failed to launch: {exc}"

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return False, f"ffprobe exit={result.returncode}: {output.strip()[:200]}"

    streams: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if line == "[STREAM]":
            current = {}
        elif line == "[/STREAM]":
            streams.append(current)
            current = {}
        elif "=" in line:
            key, _, value = line.partition("=")
            current[key.strip()] = value.strip()

    if not streams:
        return False, "ffprobe reported no streams"

    media_streams = [s for s in streams if s.get("codec_type", "") in {"video", "audio", "subtitle"}]
    if not media_streams:
        codec_names = ", ".join(s.get("codec_name", "?") for s in streams) or "(none)"
        return False, f"no audio/video stream (codec_type=data only): {codec_names}"

    # Only video/audio streams indicate encryption when reported as unknown.
    av_streams = [s for s in media_streams if s.get("codec_type", "") in {"video", "audio"}]
    bad = [s for s in av_streams if s.get("codec_name", "unknown") in {"unknown", "none", ""}]
    if bad:
        return False, "ffprobe still reports unknown codec — file likely encrypted"

    summary = ", ".join(f"{s.get('codec_type','?')}={s.get('codec_name','?')}" for s in media_streams)
    return True, summary


def _mp4dump_clean(mp4dump: str, file_path: str) -> Tuple[bool, str]:
    """
    Best-effort encryption-residue scan with Bento4's mp4dump.
    """
    try:
        with open(file_path, "rb") as fh:
            head = fh.read(_MP4DUMP_SCAN_BYTES)

        with os_manager.temp_binary_file(head, suffix=".mp4") as tmp_path:
            result = subprocess.run(
                [mp4dump, "--verbosity", "0", tmp_path],
                capture_output=True,
                timeout=5,
            )

    except Exception as exc:
        return True, f"mp4dump failed to launch: {exc} (skipped)"

    if result.returncode != 0:
        return True, "mp4dump non-zero exit (skipped)"

    text = ""
    for enc in ("utf-8", "utf-16", "utf-16-le", "latin-1"):
        try:
            text = result.stdout.decode(enc).lstrip("\ufeff")
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return True, "mp4dump produced no decodable output (skipped)"

    flagged = [
        marker
        for marker in ("[encv]", "[enca]", "[sinf]", "[saiz]", "[saio]", "[senc]")
        if marker in text.lower()
    ]
    if flagged:
        return False, f"residual encryption boxes: {','.join(flagged)}"
    return True, "no residual encryption boxes"


def verify_decrypted_media(file_path) -> Tuple[bool, str]:
    """Verify that *file_path* is a playable, fully decrypted media file."""
    p = Path(file_path)
    if not p.exists():
        return False, "output file missing"

    if p.stat().st_size == 0:
        return False, "output file is empty"

    ok, ffprobe_msg = _ffprobe_streams(get_ffprobe_path(), str(p))
    if not ok:
        return False, ffprobe_msg

    mp4dump_path = get_mp4dump_path()
    if mp4dump_path:
        clean, mp4dump_msg = _mp4dump_clean(mp4dump_path, str(p))
        if not clean:
            return False, f"{ffprobe_msg}; {mp4dump_msg}"
        return True, f"{ffprobe_msg}; {mp4dump_msg}"

    return True, ffprobe_msg