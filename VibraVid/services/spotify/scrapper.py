# 14.05.26

import logging

from .client import JumoClient, resolve_format_id


logger = logging.getLogger(__name__)


class TrackInfo:
    def __init__(self, url: str, audio_format=None) -> None:
        self.url = str(url).strip()
        self.client = JumoClient()
        self._track_id: int | None = self._parse_id(self.url)
        # audio_format may be a string ('flac' / 'mp3'), an int, or None (→ default).
        self._format_id: int = resolve_format_id(audio_format)

        # Metadata populated after fetch()
        self.title: str = ""
        self.artist: str = ""
        self.album: str = ""
        self.year: str = ""
        self.genre: str = ""
        self.cover_url: str = ""
        self.stream_url: str = ""
        self.ext: str = "flac"
        self.track_num: int | None = None
        self.duration: int = 0

    @staticmethod
    def _parse_id(value: str) -> int | None:
        raw = value.strip()
        if raw.startswith("jumo:"):
            raw = raw.split(":", 1)[1]
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None

    def fetch(self) -> None:
        """Fetch metadata and stream info from jumo-dl."""
        if self._track_id is None:
            raise ValueError(f"Cannot parse track id from url: {self.url!r}")

        logger.debug(f"Fetching info for track id {self._track_id} with format_id={self._format_id}")
        data = self.client.fetch_stream(self._track_id, format_id=self._format_id)
        self._process_data(data)

    def _process_data(self, data: dict) -> None:
        meta = data.get("metadataTrack", {})
        album_meta = meta.get("album", {})

        self.title = meta.get("title", "Unknown Track")
        self.artist = (meta.get("performer", {}).get("name") or album_meta.get("artist", {}).get("name") or "")
        self.album = album_meta.get("title", "")

        release = (
            album_meta.get("release_date_original")
            or album_meta.get("release_date_stream")
            or album_meta.get("release_date_download")
            or ""
        )
        self.year = release[:4] if release else ""

        mime = data.get("mime_type", "")
        self.ext = "flac" if "flac" in mime else "mp3"
        self.stream_url = data.get("directUrl") or data.get("url") or ""

        cover = album_meta.get("image")
        self.cover_url = cover.get("large", "") if isinstance(cover, dict) else ""

        self.track_num = meta.get("track_number")
        self.duration = meta.get("duration", 0)

        genre = album_meta.get("genre")
        self.genre = genre.get("name", "") if isinstance(genre, dict) else ""

        logger.info(f"Track resolved: {self.artist} - {self.title}  ext={self.ext}  year={self.year}")

    @property
    def display_name(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title