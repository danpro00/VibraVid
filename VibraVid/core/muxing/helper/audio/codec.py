# 16.04.24

from typing import List, Optional

from VibraVid.core.utils.codec import get_codec_extension


def audio_ext_for_codec(codec: str) -> Optional[str]:
    """Map an audio codec name to a file extension, or None if unknown."""
    if not codec:
        return None
    return get_codec_extension(codec.strip().lower(), default="")


def _detect_output_ext(ffmpeg_params: List[str], fallback_ext: str) -> str:
    """
    Derive the output file extension from the -c:a codec in ffmpeg_params.
    Falls back to fallback_ext if -c:a is absent or unrecognised.
    """
    try:
        idx = ffmpeg_params.index('-c:a') + 1
        codec = ffmpeg_params[idx]
        return get_codec_extension(codec, default=fallback_ext)
    except (ValueError, IndexError):
        return fallback_ext