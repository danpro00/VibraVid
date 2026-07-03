# 16.04.24

import json
import logging
import subprocess

from VibraVid.setup import get_ffprobe_path
from VibraVid.core.utils.codec import get_short_codec


logger = logging.getLogger(__name__)

_RESOLUTION_TIERS = [
    (4320, 7680, "4320p"),
    (2880, 5120, "2880p"),
    (2160, 3840, "2160p"),
    (1440, 2560, "1440p"),
    (1080, 1920, "1080p"),
    (900, 1600, "900p"),
    (768, 1366, "768p"),
    (720, 1280, "720p"),
    (540, 960, "540p"),
    (480, 854, "480p"),
    (360, 640, "360p"),
    (240, 426, "240p"),
    (144, 256, "144p"),
]


def _classify_resolution(width, height) -> str:
    for min_h, min_w, label in _RESOLUTION_TIERS:
        if (height and height >= min_h) or (width and width >= min_w):
            return label
    if height:
        return f"{height}p"
    return ""


def get_media_metadata(file_path: str) -> dict:
    """
    Extract quality (resolution), languages, and codecs from a media file using ffprobe.

    Returns:
        dict: {'quality': str, 'language': str, 'video_codec': str, 'audio_codec': str}
    """
    cmd = [get_ffprobe_path(), '-v', 'error', '-show_streams', '-print_format', 'json', file_path]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.error(f"ffprobe error while extracting metadata for file {file_path}: {result.stderr.strip()}")
            return {"quality": "", "language": "", "video_codec": "", "audio_codec": ""}

        info = json.loads(result.stdout)
        streams = info.get('streams', [])
        quality_val = ""
        vcodec_val = ""

        for s in streams:
            if s.get('codec_type') == 'video':
                quality_val = _classify_resolution(s.get('width'), s.get('height'))

                raw_vcodec = s.get('codec_name', '')
                vcodec_val = get_short_codec("video", raw_vcodec)
                break

        languages_found = []
        acodecs_found = []
        for s in streams:
            if s.get('codec_type') == 'audio':
                lang = s.get('tags', {}).get('language')
                if lang:
                    lang = lang.upper()
                    if lang not in languages_found:
                        languages_found.append(lang)

                raw_acodec = s.get('codec_name', '')
                short_acodec = get_short_codec("audio", raw_acodec)
                if short_acodec and short_acodec not in acodecs_found:
                    acodecs_found.append(short_acodec)

        return {
            "quality": quality_val,
            "language": "-".join(languages_found) if languages_found else "",
            "video_codec": vcodec_val,
            "audio_codec": "-".join(acodecs_found) if acodecs_found else ""
        }

    except Exception:
        return {"quality": "", "language": "", "video_codec": "", "audio_codec": ""}