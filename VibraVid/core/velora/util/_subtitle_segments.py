# 10.07.26

import re
import gzip
import logging
from typing import Any, Dict, List, Tuple
from urllib.parse import urljoin

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers


logger = logging.getLogger(__name__)

_CUE_TIME_RE = re.compile(
    r"^(\d{2,}):(\d{2}):(\d{2})\.(\d{3})(\s*-->\s*)(\d{2,}):(\d{2}):(\d{2})\.(\d{3})(.*)$"
)
RESTART_MAX_START = 3.0
RESTART_MIN_GAP = 15.0


def get_subtitle_resolve_workers() -> int:
    """Number of concurrent workers used to resolve/download HLS subtitle renditions. ``1`` preserves the original strictly-sequential behaviour."""
    return max(1, config_manager.config.get_int("DOWNLOAD", "subtitle_resolve_workers"))


def _ext_from_url(url: str, fallback: str = "") -> str:
    """Detect subtitle format from URL path, ignoring query string."""
    from VibraVid.core.velora.util._stream_helpers import _ext_from_url_canon
    _SUB_EXTENSIONS = ("webvtt", "vtt", "srt", "ass", "ssa", "ttml2", "ttml", "xml", "dfxp")
    return _ext_from_url_canon(url, _SUB_EXTENSIONS, default=fallback)


def parse_subtitle_playlist_segments(text: str, base_url: str) -> List[Tuple[str, float]]:
    """Parse every media segment (url, duration_seconds) referenced by an HLS subtitle child playlist, in order, including segments after ``#EXT-X-DISCONTINUITY`` tags."""
    segments: List[Tuple[str, float]] = []
    duration = 0.0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#EXTINF:"):
            try:
                duration = float(line[len("#EXTINF:"):].split(",", 1)[0])
            except ValueError:
                duration = 0.0
            continue

        if line.startswith("#"):
            continue

        segments.append((urljoin(base_url, line), duration))
        duration = 0.0
    return segments


def resolve_subtitle_segments_sync(url: str, headers: Dict) -> Tuple[List[Tuple[str, float]], str]:
    """
    Synchronously probe *url* and return every subtitle segment ``(url, duration)``
    referenced by the manifest, in playback order, plus the detected extension.

    If *url* does not point to an HLS manifest, it is treated as a single segment.
    """
    try:
        hdrs = dict(headers)
        hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
        with create_client(headers=hdrs, timeout=15, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            text = resp.text.strip()
    except Exception as exc:
        logger.info(f"resolve_subtitle_segments_sync: request failed for {url!r}: {exc}")
        return [(url, 0.0)], _ext_from_url(url, "")

    if not text.startswith("#EXTM3U"):
        content_type = resp.headers.get("content-type", "").lower()
        for mime, ext in (
            ("vtt", "vtt"), ("webvtt", "vtt"), ("srt", "srt"),
            ("ttml", "ttml"), ("xml", "xml"), ("dfxp", "dfxp"),
        ):
            if mime in content_type:
                return [(url, 0.0)], ext
        return [(url, 0.0)], _ext_from_url(url, "")

    segments = parse_subtitle_playlist_segments(text, url)
    if not segments:
        logger.info(f"resolve_subtitle_segments_sync: manifest at {url!r} had no segments")
        return [(url, 0.0)], ""

    resolved_ext = _ext_from_url(segments[0][0], "")
    total_dur = sum(d for _, d in segments)
    logger.info(f"Resolved HLS subtitle manifest -> {len(segments)} segment(s), total {total_dur:.1f}s (ext={resolved_ext!r})")
    return segments, resolved_ext


async def resolve_subtitle_segments_async(client: Any, url: str) -> Tuple[List[Tuple[str, float]], str]:
    """Async counterpart of ``resolve_subtitle_segments_sync``, used at download time."""
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text.strip()
    except Exception as exc:
        logger.error(f"resolve_subtitle_segments_async: probe failed for {url!r}: {exc}")
        return [(url, 0.0)], _ext_from_url(url, "UNK")

    if not text.startswith("#EXTM3U"):
        return [(url, 0.0)], _ext_from_url(url, "UNK")

    segments = parse_subtitle_playlist_segments(text, url)
    if not segments:
        logger.error(f"resolve_subtitle_segments_async: manifest parsed but no segment found in {url!r}")
        return [(url, 0.0)], _ext_from_url(url, "UNK")

    fmt = _ext_from_url(segments[0][0], "UNK")
    logger.info(f"Resolved HLS subtitle manifest -> {len(segments)} segment(s) (fmt={fmt})")
    return segments, fmt


async def download_and_merge_subtitle_segments(client: Any, segments: List[Tuple[str, float]]) -> str:
    """Fetch every subtitle segment concurrently and merge them (in order) into a single WebVTT track with cue timestamps shifted by cumulative offset."""
    import asyncio

    texts: List[str] = [""] * len(segments)

    async def _fetch(index: int, url: str) -> None:
        resp = await client.get(url)
        resp.raise_for_status()
        texts[index] = resp.text

    await asyncio.gather(*(_fetch(i, url) for i, (url, _dur) in enumerate(segments)))
    durations = [dur for _url, dur in segments]
    return merge_vtt_segments(texts, durations)


def merge_vtt_segments(segment_texts: List[str], durations: List[float]) -> str:
    """Concatenate consecutive WebVTT segments into a single track, shifting every cue's timestamps by the cumulative duration of the segments before it."""
    out_lines: List[str] = ["WEBVTT", ""]
    offset = 0.0

    for text, dur in zip(segment_texts, durations):
        body = text.strip("﻿ \r\n")
        for raw_line in body.splitlines():
            line = raw_line.rstrip("\r")
            stripped = line.strip()
            if stripped.startswith("WEBVTT") or stripped.startswith("X-TIMESTAMP-MAP"):
                continue

            m = _CUE_TIME_RE.match(stripped)
            if m:
                start = _shift_timestamp(m.group(1), m.group(2), m.group(3), m.group(4), offset)
                end = _shift_timestamp(m.group(6), m.group(7), m.group(8), m.group(9), offset)
                out_lines.append(f"{start}{m.group(5)}{end}{m.group(10)}")
            else:
                out_lines.append(line)

        offset += dur

    return "\n".join(out_lines) + "\n"


def _ts_to_seconds(hh: str, mm: str, ss: str, ms: str) -> float:
    """Convert a WebVTT cue timestamp to seconds."""
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def _seconds_to_ts(total: float) -> str:
    """Convert seconds to a WebVTT cue timestamp string."""
    if total < 0:
        total = 0.0
    h = int(total // 3600)
    m = int((total % 3600) // 60)
    s = int(total % 60)
    ms = int(round((total - int(total)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _extract_vtt_cues(text: str):
    """Return a list of cues ``(start_sec, end_sec, timestamp_line, [text_lines])`` from one WebVTT."""
    lines = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues = []
    i, n = 0, len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("WEBVTT") or stripped.startswith("X-TIMESTAMP-MAP"):
            i += 1
            continue
        if stripped.startswith(("NOTE", "STYLE", "REGION")):
            i += 1
            while i < n and lines[i].strip():   # skip the whole block until a blank line
                i += 1
            continue
        m = _CUE_TIME_RE.match(stripped)
        if not m:                               # a cue identifier line: the timestamp is next
            i += 1
            if i >= n:
                break
            m = _CUE_TIME_RE.match(lines[i].strip())
            if not m:
                continue
        start = _ts_to_seconds(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _ts_to_seconds(m.group(6), m.group(7), m.group(8), m.group(9))
        i += 1
        body = []
        while i < n and lines[i].strip():
            body.append(lines[i])
            i += 1
        cues.append((start, end, m, body))
    return cues


def _read_vtt_segment_text(path) -> str:
    """Read a WebVTT segment file, decompressing if it is gzipped, and return its text content."""
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


def merge_vtt_files(paths, merge_logger=None) -> str:
    """Merge multiple WebVTT files into a single WebVTT track, shifting cue timestamps by"""
    log = merge_logger or logger
    per_segment = []
    for p in paths:
        try:
            text = _read_vtt_segment_text(p)
            per_segment.append(_extract_vtt_cues(text))
        except Exception as exc:
            log.warning(f"[merge_vtt] skipping unreadable segment {getattr(p, 'name', p)}: {exc}")

    out_lines = ["WEBVTT", ""]
    running_end = 0.0
    seen = set()
    emitted = 0
    for idx, cues in enumerate(per_segment):
        if not cues:
            continue

        first_start = cues[0][0]
        is_real_restart = (first_start < RESTART_MAX_START) and (running_end > RESTART_MIN_GAP)
        seg_offset = running_end if is_real_restart else 0.0

        if seg_offset > 0:
            log.debug(f"[merge_vtt] segment {idx}: detected relative restart (first_start={first_start:.2f}s, running_end={running_end:.2f}s) -> offset={seg_offset:.2f}s")

        for start, end, m, body in cues:
            s, e = start + seg_offset, end + seg_offset
            text = "\n".join(body)
            key = (round(s, 3), round(e, 3), text)
            if key in seen:
                continue
            
            seen.add(key)
            out_lines.append(f"{_seconds_to_ts(s)}{m.group(5)}{_seconds_to_ts(e)}{m.group(10)}")
            out_lines.extend(body)
            out_lines.append("")
            running_end = max(running_end, e)
            emitted += 1

    log.info(f"[merge_vtt] merged {len(per_segment)} segment(s) -> {emitted} cue(s), final_end={running_end:.2f}s")
    if emitted == 0:
        log.warning(f"[merge_vtt] 0 cues extracted from {len(paths)} segment(s)")
    return "\n".join(out_lines) + "\n"


def _shift_timestamp(hh: str, mm: str, ss: str, ms: str, offset_seconds: float) -> str:
    """Shift a WebVTT cue timestamp by *offset_seconds* and return the new timestamp string."""
    total = int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0 + offset_seconds
    if total < 0:
        total = 0.0

    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    seconds = int(total % 60)
    millis = round((total - int(total)) * 1000)
    if millis == 1000:
        millis = 0
        seconds += 1
        if seconds == 60:
            seconds = 0
            minutes += 1
            if minutes == 60:
                minutes = 0
                hours += 1
    
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"