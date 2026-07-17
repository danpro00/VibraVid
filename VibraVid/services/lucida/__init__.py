# 16.07.26

import logging
from urllib.parse import urlparse

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download
from VibraVid.core.ui.tracker import context_tracker

from .client import LucidaError
from .scrapper import AlbumScraper, _first_artist, _cover_from, _year_from_date
from .downloader import download_song, download_track_from_album, _build_client


indice = 18
_useFor = "Song"
console = Console()
msg = Prompt()
logger = logging.getLogger(__name__)
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def register_cli_args(parser) -> list:
    """Register CLI options for lucida."""
    group = parser.add_argument_group('Lucida options (--site 19)')
    group.add_argument('--country', dest='country', default=None, metavar='CODE', help='Account country to source from (e.g. "auto", "us", "de"). Default: auto.')
    group.add_argument('--lucida-private', dest='private', action='store_true', help='Hide the track from lucida recent downloads.')
    group.add_argument('--lucida-no-metadata', dest='no_metadata', action='store_true', help='Disable server-side metadata embedding by lucida.')
    return ['country', 'private', 'no_metadata']


def _looks_like_url(value: str) -> bool:
    try:
        p = urlparse(str(value).strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def _search_via_amazon(query: str) -> int:
    """Text search fallback: search Amazon Music's public catalog directly"""
    from VibraVid.provider.amazon import amazon_music

    try:
        results = amazon_music.search_songs(query, limit=25)
    except Exception as e:
        logger.exception(f"[lucida] Amazon Music search failed for {query!r}")
        console.print(f"[red]Search failed: {e}")
        return 0

    logger.info(f"[lucida] text search (via Amazon Music) query={query!r} -> {len(results)} track(s)")

    for r in results:
        artist = (r.get("artist") or {}).get("name", "")
        title = r.get("title", "")
        entry = Entries(
            id=r.get("id"),
            name=f"{artist} - {title}" if artist else title,
            type="song",
            year="",
            image=r.get("image", ""),
            url=r["url"],
        )
        entry.title = title
        entry.artist = artist
        entry.album = (r.get("album") or {}).get("name", "")
        entries_manager.add(entry)

    return len(entries_manager)


def title_search(query: str) -> int:
    """Resolve a service/lucida url into a single downloadable entry."""
    entries_manager.clear()
    table_show_manager.clear()

    if not _looks_like_url(query):
        return _search_via_amazon(query)

    client = _build_client()
    try:
        data = client.resolve(query)
    except LucidaError as e:
        logger.error(f"[lucida] title_search resolve failed: {e}")
        console.print(f"[red]{e}")
        return 0
    except Exception as e:
        logger.exception(f"[lucida] title_search unexpected error for {query!r}")
        console.print(f"[red]Could not resolve the url: {e}")
        return 0

    info = data.get("info") or {}
    info_type = str(info.get("type"))
    service = str(data.get("originalService", ""))

    if info_type == "track":
        album = info.get("album") if isinstance(info.get("album"), dict) else None
        artist = _first_artist((album or {}).get("artists") or info.get("artists"))
        title = info.get("title", "")
        entry = Entries(
            name=f"{artist} - {title}" if artist else title,
            type="song",
            year=_year_from_date((album or {}).get("releaseDate") or info.get("releaseDate")),
            image=_cover_from(album or info, service),
            url=query,
        )
        entry.title = title
        entry.artist = artist
        entry.album = (album or {}).get("title") or title
        entry.service = service
        entries_manager.add(entry)

    elif info_type == "album":
        artist = _first_artist(info.get("artists"))
        title = info.get("title", "")
        entry = Entries(
            name=f"{artist} - {title}" if artist else title,
            type="album",
            year=_year_from_date(info.get("releaseDate")),
            image=_cover_from(info, service),
            url=query,
        )
        entry.artist = artist
        entry.service = service
        count = info.get("trackCount") or len(info.get("tracks") or [])
        entry.tracks = str(count) if count else "—"
        entries_manager.add(entry)

    else:
        console.print(f"[yellow]Unsupported lucida item type: {info_type}")
        return 0

    return len(entries_manager)


def _build_album_scraper(select_title: Entries) -> AlbumScraper | None:
    """Instantiate and fetch an AlbumScraper for a selected album entry."""
    scraper = AlbumScraper(str(select_title.url).strip(), client=_build_client())
    try:
        scraper.fetch()
    except Exception as e:
        console.print(f"[red]Failed to fetch album '{select_title.name}': {e}")
        logger.error(f"AlbumScraper.fetch() error: {e}")
        context_tracker.report_download_error(f"Failed to fetch album '{select_title.name}': {e}")
        return None
    return scraper


def download_series_album(select_title: Entries, season_selection=None, episode_selection=None, scrape_serie=None):
    """Drive the shared season/episode pipeline for a lucida album."""
    start_name = select_title.name if select_title else "Unknown"
    console.print(f"\n[yellow]Download album: [red]{site_constants.SITE_NAME} -> [cyan]{start_name}\n")

    if scrape_serie is None:
        scrape_serie = _build_album_scraper(select_title)
        if scrape_serie is None:
            return (None, False, "Could not load album metadata")

    seasons_count = len(scrape_serie.seasons_manager)

    def _download_episode_callback(season_number: int, download_all: bool, episode_selection=None):
        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=lambda ep, sn, ei: download_track_from_album(ep, sn, ei, scrape_serie),
            download_all=download_all,
            episode_selection=episode_selection,
        )

    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=_download_episode_callback,
    )

    entries_manager.clear()
    table_show_manager.clear()


def process_search_result(select_title, selections=None, scrape_serie=None):
    """Route songs to download_song, albums to the series pipeline."""
    if select_title is None:
        console.print("[yellow]No title selected.")
        return False

    media_type = str(getattr(select_title, "type", "")).lower()

    if media_type == "album":
        if scrape_serie is None:
            scrape_serie = _build_album_scraper(select_title)
            if scrape_serie is None:
                return False

        return base_process_search_result(
            select_title=select_title,
            download_series_func=download_series_album,
            media_search_manager=entries_manager,
            table_show_manager=table_show_manager,
            selections=selections,
            scrape_serie=scrape_serie,
        )

    return base_process_search_result(
        select_title=select_title,
        download_film_func=download_song,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie,
    )


def search(string_to_search: str = None, get_onlyDatabase: bool = False, direct_item: dict = None, selections: dict = None, scrape_serie=None):
    """Wrapper for the generalized search function."""
    return base_search(
        title_search_func=title_search,
        process_result_func=process_search_result,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        site_name=site_constants.SITE_NAME,
        string_to_search=string_to_search,
        get_onlyDatabase=get_onlyDatabase,
        direct_item=direct_item,
        selections=selections,
        scrape_serie=scrape_serie,
    )