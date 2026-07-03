# 21.05.24

import re
from datetime import datetime

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, check_region_availability
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search
from VibraVid.core.ui.tracker import context_tracker

from .downloader import download_series, download_film
from .client import get_client, get_metadata_by_guid


indice = 3
_useFor = "Film_Serie"
_region = ["IT"]
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()

_SERIES_ID_RE = re.compile(r'(SE\d+)')
_FILM_ID_RE = re.compile(r'(F\d{6,})')


def register_cli_args(parser) -> list:
    """Register CLI options."""
    group = parser.add_argument_group('Mediaset Infinity options (--site 3)')
    group.add_argument('--url', dest='url', default=None, metavar='URL', help='Mediaset Infinity title URL (movie or series).')
    return ['url']


def _resolve_url_to_item(url: str):
    """Resolve a Mediaset Infinity URL to an item dictionary containing metadata."""
    series_match = _SERIES_ID_RE.search(url)
    if series_match:
        serie_id = series_match.group(1)
        entry = get_metadata_by_guid(serie_id, 'mediaset-prod-all-series-v2')
        name = entry.get('title') if entry else serie_id
        year = str(entry.get('year')) if entry and entry.get('year') else '9999'
        console.print(f"[cyan]Detected series from URL: [green]{name}")
        return {'id': serie_id, 'name': name, 'type': 'tv', 'url': url, 'year': year}

    film_match = _FILM_ID_RE.search(url)
    if film_match:
        film_id = film_match.group(1)
        entry = get_metadata_by_guid(film_id, 'mediaset-prod-all-programs-v2')
        if not entry:
            console.print(f"[red]Could not resolve film metadata for id '{film_id}'")
            return None

        name = entry.get('title', film_id)
        year = str(entry.get('year')) if entry.get('year') else '9999'
        console.print(f"[cyan]Detected film from URL: [green]{name}")
        return {'id': film_id, 'name': name, 'type': 'film', 'url': url, 'year': year}

    console.print("[red]Could not determine content type (film/series) from URL")
    return None


def title_search(query: str) -> int:
    """
    Search for titles based on a search query.
      
    Parameters:
        - query (str): The query to search for.

    Returns:
        int: The number of titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    if not check_region_availability(_region, site_constants.SITE_NAME):
        return 0

    class_mediaset_api = get_client()
    search_url = 'https://mediasetplay.api-graph.mediaset.it/'
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    params = {
        'extensions': f'{{"persistedQuery":{{"version":1,"sha256Hash":"{class_mediaset_api.getHash256()}"}}}}',
        'variables': f'{{"first":10,"property":"search","query":"{query}","uxReference":"filteredSearch"}}',
    }
    
    try:
        with create_client(headers=class_mediaset_api.generate_request_headers()) as client:
            response = client.get(search_url, params=params)
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Parse response
    resp_json = response.json()
    items = resp_json.get("data", {}).get("getSearchPage", {}).get("areaContainersConnection", {}).get("areaContainers", [])[0].get("areas", [])[0].get("sections", [])[0].get("collections", [])[0].get("itemsConnection", {}).get("items", [])

    # Process items
    for item in items:
        try:
            is_series = (item.get("__typename") == "SeriesItem" or item.get("cardLink", {}).get("referenceType") == "series"or bool(item.get("seasons")))
            item_type = "tv" if is_series else "film"
        except Exception:
            break

        # Get date
        date = item.get("year") or ''
        if not date:
            updated = item.get("updated") or item.get("r") or ''
            if updated:
                try:
                    date = datetime.fromisoformat(str(updated).replace("Z", "+00:00")).year
                except Exception:
                    date = ''
        
        # Get poster image
        images = item.get("cardImages", [])
        vertical_image = None

        for img in images:
            if img.get("sourceType") == "image_vertical" or img.get("type") == "image_vertical":
                vertical_image = img
                break
    
        image_base_url = "https://img-prod-api2.mediasetplay.mediaset.it/api/images"
        image_url = f"{image_base_url}/{vertical_image.get('engine', 'mse')}/v5/ita/{vertical_image.get('id', '')}/image_vertical/300/450"
        if vertical_image.get("r", ""):
            image_url += f"?r={vertical_image.get('r', '')}"
        
        entries_manager.add(Entries(
            id=item.get("guid", ""),
            name=item.get("cardTitle", "No Title"),
            type=item_type,
            image=image_url,
            year=date if date not in ("", None) else "9999",
            url=item.get("cardLink", {}).get("value", "")
        ))

    return len(entries_manager)

def process_search_result(select_title, selections=None, scrape_serie=None):
    """Wrapper for the generalized process_search_result function."""
    return base_process_search_result(
        select_title=select_title,
        download_film_func=download_film,
        download_series_func=download_series,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie
    )

def search(string_to_search: str = None, get_onlyDatabase: bool = False, direct_item: dict = None, selections: dict = None, scrape_serie=None):
    """Wrapper for the generalized search function."""
    if direct_item is None and not get_onlyDatabase:
        url = (context_tracker.site_options or {}).get('url')
        if url:
            direct_item = _resolve_url_to_item(url)
            if not direct_item:
                return False

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
        scrape_serie=scrape_serie
    )