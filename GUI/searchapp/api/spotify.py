# 16.05.26

import importlib
from typing import List, Optional

from .base import BaseStreamingAPI, Entries, Season

from VibraVid.services._base.site_loader import get_folder_name
from VibraVid.services.spotify.scrapper import TrackInfo


class SpotifyAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "spotify"
        self._search_fn = None

    def _get_search_fn(self):
        """Lazy load the search function."""
        if self._search_fn is None:
            module = importlib.import_module(f"VibraVid.{get_folder_name()}.{self.site_name}")
            self._search_fn = getattr(module, "search")
        return self._search_fn

    def search(self, query: str) -> List[Entries]:
        """Search for tracks on Spotify (via Jumo)."""
        search_fn = self._get_search_fn()
        database = search_fn(query, get_onlyDatabase=True)

        results = []
        if database and hasattr(database, "media_list"):
            for element in list(database.media_list):
                item_dict = element.__dict__.copy() if hasattr(element, "__dict__") else {}

                media_item = Entries(
                    id=item_dict.get("id") or item_dict.get("slug"),
                    name=item_dict.get("name"),
                    slug=item_dict.get("slug", ""),
                    type=item_dict.get("type", "song"),
                    url=item_dict.get("url"),
                    poster=item_dict.get("image"),
                    year=item_dict.get("year"),
                    raw_data=item_dict,
                )
                results.append(media_item)

        return results

    def get_series_metadata(self, media_item: Entries) -> Optional[List[Season]]:
        """Spotify deals only with single tracks here, no seasons/episodes."""
        return None

    def start_download(self, media_item: Entries, season: Optional[str] = None, episodes: Optional[str] = None, audio_format: Optional[str] = None) -> bool:
        """
        Start downloading a song from Spotify (Jumo).

        audio_format ('flac' | 'mp3') is forwarded to the backend via the
        selections dict so download_song can pick the right Jumo format_id.
        """
        search_fn = self._get_search_fn()

        selections = {}
        # Pass season/episode only if non-empty (kept for API symmetry).
        if season or episodes:
            selections["season"] = season
            selections["episode"] = episodes
        # GUI form override (FLAC/MP3 dropdown). Fall back to whatever
        # attribute the Entries instance was tagged with by the dispatcher.
        af = audio_format or getattr(media_item, "audio_format", None)
        if af:
            selections["audio_format"] = af

        scrape_serie = self.get_cached_scraper(media_item)
        search_fn(direct_item=media_item.raw_data, selections=(selections or None), scrape_serie=scrape_serie)
        return True
