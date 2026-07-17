# 16.07.26

import logging
from typing import Optional

from VibraVid.services._base.object import Season, SeasonManager

from .client import LucidaClient, LucidaError


logger = logging.getLogger(__name__)


def _year_from_date(value) -> str:
    """Extract a 4-digit year from an RFC3339 date string."""
    if not value:
        return ""
    s = str(value).strip()
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else ""


def _first_artist(artists) -> str:
    if isinstance(artists, list) and artists:
        a = artists[0]
        if isinstance(a, dict):
            return a.get("name", "") or ""
    return ""


def _cover_from(entry: dict, service: str) -> str:
    """Pick the highest-res cover url from a coverArtwork list, upscaling Qobuz."""
    arts = entry.get("coverArtwork") or []
    if not isinstance(arts, list) or not arts:
        return ""
    url = (arts[-1] or {}).get("url", "") if isinstance(arts[-1], dict) else ""
    if not url:
        return ""
    return _upscale_cover(url, service)


def _upscale_cover(url: str, service: str) -> str:
    """Qobuz thumbnails end in _<size>.jpg — swap for the original artwork."""
    if (service or "").lower() != "qobuz" or not url.endswith(".jpg"):
        return url
    stripped = url[:-len(".jpg")]
    pos = stripped.rfind("_")
    if pos == -1:
        return url
    return f"{url[:pos + 1]}org.jpg"


class TrackInfo:
    def __init__(self, source_url: str, client: Optional[LucidaClient] = None, metadata: Optional[dict] = None) -> None:
        self.source_url = str(source_url).strip()
        self.client = client or LucidaClient()

        meta = metadata or {}
        self.title: str = meta.get("title", "")
        self.artist: str = meta.get("artist", "")
        self.album: str = meta.get("album", "")
        self.year: str = meta.get("year", "")
        self.genre: str = meta.get("genre", "")
        self.cover_url: str = meta.get("cover", "")
        self.track_num = meta.get("track_num")

        self.service: str = ""
        self.track_url: str = ""
        self.csrf: str = ""
        self.csrf_fallback: Optional[str] = None
        self.token_expiry: int = 0

    def fetch(self) -> None:
        """Resolve the lucida page and populate download tokens + metadata."""
        data = self.client.resolve(self.source_url)
        info = data.get("info") or {}
        if str(info.get("type")) != "track":
            raise LucidaError(f"Expected a track url, got '{info.get('type')}'.")

        self.service = str(data.get("originalService", ""))
        # For single tracks lucida uses the page-level token as the csrf primary.
        self.token_expiry = int(data.get("tokenExpiry") or 0)
        self.csrf = str(data.get("token") or "")
        self.csrf_fallback = None
        self.track_url = str(info.get("url") or self.source_url)

        album = info.get("album") if isinstance(info.get("album"), dict) else None

        self.title = self.title or info.get("title", "")
        self.artist = self.artist or _first_artist((album or {}).get("artists") or info.get("artists"))
        self.album = self.album or ((album or {}).get("title") or info.get("title", ""))
        self.cover_url = self.cover_url or _cover_from(album or info, self.service)
        self.year = self.year or _year_from_date((album or {}).get("releaseDate") or info.get("releaseDate"))

        # Qobuz: producers=null marks a track that cannot be downloaded yet.
        self._downloadable = not (self.service.lower() == "qobuz" and info.get("producers") is None)

    @property
    def downloadable(self) -> bool:
        return getattr(self, "_downloadable", True)

    @property
    def display_name(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


class AlbumScraper:
    def __init__(self, source_url: str, client: Optional[LucidaClient] = None) -> None:
        self.source_url = str(source_url).strip()
        self.client = client or LucidaClient()

        self.title: str = ""
        self.artist: str = ""
        self.year: str = ""
        self.genre: str = ""
        self.cover_url: str = ""
        self.service: str = ""
        self.token_expiry: int = 0
        self.series_name: str = ""
        self.series_display_name: str = ""

        self.seasons_manager: SeasonManager = SeasonManager()
        self._tracks: list[dict] = []

    def fetch(self) -> None:
        data = self.client.resolve(self.source_url)
        info = data.get("info") or {}
        info_type = str(info.get("type"))

        self.service = str(data.get("originalService", ""))
        self.token_expiry = int(data.get("tokenExpiry") or 0)

        if info_type == "album":
            self._process_album(info)
        elif info_type == "track":
            self._process_single(info, str(data.get("token") or ""))
        else:
            raise LucidaError(f"Unsupported lucida item type '{info_type}'.")

        self.series_name = self.title
        self.series_display_name = self.title
        self.seasons_manager = SeasonManager()
        self.seasons_manager.add(Season(id=1, number=1, name=self.title))
        logger.info(f"AlbumScraper '{self.title}' by {self.artist}: {len(self._tracks)} track(s)")

    def _process_album(self, info: dict) -> None:
        self.title = info.get("title", "Unknown Album")
        self.artist = _first_artist(info.get("artists"))
        self.year = _year_from_date(info.get("releaseDate"))
        self.cover_url = _cover_from(info, self.service)

        self._tracks = []
        for i, t in enumerate(info.get("tracks") or [], start=1):
            self._tracks.append({
                "name": t.get("title", "Unknown Track"),
                "number": i,
                "track_url": t.get("url", ""),
                "csrf": t.get("csrf", ""),
                "csrf_fallback": t.get("csrfFallback"),
                "artist": _first_artist(t.get("artists")) or self.artist,
                "producers": t.get("producers"),
            })

    def _process_single(self, info: dict, page_token: str) -> None:
        album = info.get("album") if isinstance(info.get("album"), dict) else None
        self.title = (album or {}).get("title") or info.get("title", "Unknown Album")
        self.artist = _first_artist((album or {}).get("artists") or info.get("artists"))
        self.year = _year_from_date((album or {}).get("releaseDate") or info.get("releaseDate"))
        self.cover_url = _cover_from(album or info, self.service)

        self._tracks = [{
            "name": info.get("title", "Unknown Track"),
            "number": 1,
            "track_url": info.get("url", ""),
            "csrf": page_token,
            "csrf_fallback": None,
            "artist": _first_artist(info.get("artists")) or self.artist,
            "producers": info.get("producers"),
        }]

    def getEpisodeSeasons(self, season_number: int) -> list[dict]:
        """Return the tracklist as episode dicts for the shared managers."""
        episodes = []
        for t in self._tracks:
            episodes.append({
                "id": t["track_url"],
                "name": t["name"],
                "number": t["number"],
                "track_url": t["track_url"],
                "csrf": t["csrf"],
                "csrf_fallback": t["csrf_fallback"],
                "artist": t["artist"],
                "producers": t.get("producers"),
                "album": self.title,
                "year": self.year,
                "genre": self.genre,
                "cover": self.cover_url,
            })
        return episodes