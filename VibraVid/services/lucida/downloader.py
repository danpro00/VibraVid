# 16.07.26

import os
import logging
from typing import Optional, Tuple

from rich.console import Console

from VibraVid.utils import start_message
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import map_song_path
from VibraVid.core.ui.tracker import context_tracker, download_tracker
from VibraVid.core.muxing.helper.audio import process_song
from VibraVid.core.downloader import MP4_Downloader

from .client import LucidaClient, LucidaError
from .scrapper import TrackInfo, AlbumScraper


console = Console()
logger = logging.getLogger(__name__)
_AUDIO_EXTENSIONS = ("flac", "m4a", "mp3", "opus", "ogg", "aac", "wav")


def _stop_requested() -> bool:
    """True when the active tracked download was asked to stop."""
    did = context_tracker.download_id
    return bool(did and download_tracker.is_stopped(did))


def _existing_audio_file(dest_base: str) -> Optional[str]:
    """Return an already-downloaded track for extension-less *dest_base*, if any."""
    directory = os.path.dirname(dest_base)
    if not os.path.isdir(directory):
        return None
    base = os.path.basename(dest_base)
    for ext in _AUDIO_EXTENSIONS:
        candidate = os.path.join(directory, f"{base}.{ext}")
        if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
            return candidate
    return None


def _stream_to_disk(client: LucidaClient, server: str, handoff: str, dest_base: str, label: str) -> Tuple[Optional[str], bool, Optional[str]]:
    """Fetch the prepared track via MP4_Downloader"""
    url = LucidaClient.download_url(server, handoff)
    out_path = f"{dest_base}.flac"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    result_path, stopped, error = MP4_Downloader(
        url=url,
        path=out_path,
        headers_=client.download_headers(),
        label=label,
        check_content_type=False,
        sanitize_path=False,
    )
    if stopped:
        return (None, True, None)
    if not result_path or error:
        return (None, False, error or "lucida download failed.")
    return (result_path, False, None)


def _prepare_and_download(client: LucidaClient, track_url: str, csrf: str, csrf_fallback: Optional[str], token_expiry: int, dest_base: str, label: str) -> Tuple[Optional[str], bool, Optional[str]]:
    """Run the full request -> poll -> stream cycle for one track."""
    logger.info(f"[lucida] preparing download: label={label!r} url={track_url!r}")
    try:
        server, handoff = client.request_download(track_url, csrf, csrf_fallback, token_expiry)
    except LucidaError as e:
        logger.error(f"[lucida] request_download failed: {e}")
        return (None, False, str(e))

    def _on_status(status: str, message: str) -> None:
        console.print(f"[dim]lucida: {status} — {message.replace('{item}', label)}")

    try:
        ready = client.wait_until_ready(server, handoff, on_status=_on_status, stop_check=_stop_requested)
    except LucidaError as e:
        logger.error(f"[lucida] processing failed: {e}")
        return (None, False, str(e))

    if not ready:
        logger.info("[lucida] download aborted (stop requested during processing).")
        return (None, True, None)

    return _stream_to_disk(client, server, handoff, dest_base, label)


def download_song(select_title: Entries) -> Optional[str]:
    """Download a single lucida track, returning the processed path or None."""
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{select_title.name}\n")

    source_url = select_title.url
    client = _build_client()
    metadata = {
        "title": getattr(select_title, "title", "") or getattr(select_title, "name", ""),
        "artist": getattr(select_title, "artist", ""),
        "album": getattr(select_title, "album", ""),
        "year": getattr(select_title, "year", ""),
        "cover": getattr(select_title, "image", ""),
    }

    track = TrackInfo(source_url, client=client, metadata=metadata)
    try:
        track.fetch()
    except LucidaError as e:
        logger.error(f"[lucida] track resolve failed for {source_url!r}: {e}")
        console.print(f"[red]{e}")
        context_tracker.report_download_error(e)
        return None
    except Exception as e:
        logger.exception(f"[lucida] unexpected error resolving {source_url!r}")
        console.print(f"[red]Unexpected error: {e}")
        context_tracker.report_download_error(f"Unexpected error: {e}")
        return None

    if not track.downloadable:
        logger.warning(f"[lucida] track not downloadable yet: {track.title}")
        console.print(f"[yellow]Track not available for download yet: {track.title}")
        context_tracker.report_download_error(f"Track not available for download yet: {track.title}")
        return None

    path_components, filename = map_song_path(
        artist=track.artist, album=track.album, title=track.title,
        year=track.year, track_number=track.track_num,
    )
    dest_base = os.path.join(site_constants.MUSIC_FOLDER, *path_components, filename)

    existing = _existing_audio_file(dest_base)
    if existing:
        console.print(f"[dim]Already downloaded, skipping: {os.path.basename(existing)}")
        context_tracker.report_download_success()
        return existing

    out_path, stopped, error = _prepare_and_download(
        client, track.track_url, track.csrf, track.csrf_fallback, track.token_expiry, dest_base, "Audio",
    )
    if stopped or not out_path:
        if error:
            logger.error(f"Download error: {error}")
            console.print(f"[red]{error}")
            context_tracker.report_download_error(error)
        return None

    context_tracker.report_download_success()
    return process_song(
        file_path=out_path, title=track.title, artist=track.artist, album=track.album,
        year=track.year, track_number=track.track_num, genre=track.genre, cover_url=track.cover_url,
    )


def download_track_from_album(episode_dict: dict, season_number: int, episode_index: int, scrape_serie: AlbumScraper) -> Tuple[Optional[str], bool, Optional[str]]:
    """Download a track from a lucida album. Returns (path, stopped, error)."""
    is_dict = isinstance(episode_dict, dict)
    track_name = (episode_dict.get("name") if is_dict else getattr(episode_dict, "name", None)) or "Unknown Track"
    track_url = episode_dict.get("track_url") if is_dict else getattr(episode_dict, "track_url", None)
    csrf = episode_dict.get("csrf") if is_dict else getattr(episode_dict, "csrf", None)
    csrf_fallback = episode_dict.get("csrf_fallback") if is_dict else getattr(episode_dict, "csrf_fallback", None)
    track_num = episode_dict.get("number") if is_dict else getattr(episode_dict, "number", None)

    logger.info(f"[lucida] album track {track_num}: {track_name!r}")
    if not track_url or not csrf:
        logger.error(f"[lucida] missing token for album track: {track_name}")
        context_tracker.report_download_error(f"Missing lucida token for: {track_name}")
        return (None, False, f"Missing lucida token for: {track_name}")

    # Qobuz: producers=null marks a not-yet-downloadable track.
    if scrape_serie.service.lower() == "qobuz" and (episode_dict.get("producers") if is_dict else None) is None:
        logger.warning(f"[lucida] Qobuz track not downloadable yet: {track_name}")
        context_tracker.report_download_error(f"Track not available yet: {track_name}")
        return (None, False, f"Track not available yet: {track_name}")

    artist = (episode_dict.get("artist") if is_dict else "") or scrape_serie.artist
    album = scrape_serie.title
    year = (episode_dict.get("year") if is_dict else "") or scrape_serie.year
    genre = (episode_dict.get("genre") if is_dict else "") or scrape_serie.genre
    cover = (episode_dict.get("cover") if is_dict else "") or scrape_serie.cover_url

    path_components, filename = map_song_path(
        artist=artist, album=album, title=track_name, year=year, track_number=track_num,
    )
    dest_base = os.path.join(site_constants.MUSIC_FOLDER, *path_components, filename)

    existing = _existing_audio_file(dest_base)
    if existing:
        console.print(f"[dim]Already downloaded, skipping: {os.path.basename(existing)}")
        context_tracker.report_download_success()
        return (existing, False, None)

    out_path, stopped, error = _prepare_and_download(
        scrape_serie.client, track_url, csrf, csrf_fallback, scrape_serie.token_expiry,
        dest_base, f"Track {track_num or episode_index}",
    )
    if stopped:
        return (None, True, None)
    if not out_path or error:
        context_tracker.report_download_error(error or f"Download failed for '{track_name}'")
        return (None, False, error or f"Download failed for '{track_name}'")

    context_tracker.report_download_success()
    result_path = process_song(
        file_path=out_path, title=track_name, artist=artist, album=album,
        year=year, track_number=track_num, genre=genre, cover_url=cover,
    )
    return (result_path, False, None)


def _build_client() -> LucidaClient:
    """Build a LucidaClient from the active CLI/site options."""
    opts = context_tracker.site_options or {}
    return LucidaClient(
        country=opts.get("country") or "auto",
        metadata=not bool(opts.get("no_metadata")),
        private=bool(opts.get("private")),
    )