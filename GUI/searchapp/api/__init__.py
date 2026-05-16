# 06.06.25

import os
import importlib
from typing import Dict, List

from .base import BaseStreamingAPI, Entries, Season, Episode


_API_REGISTRY: Dict[str, type] = {}
_LOAD_ERRORS: List[str] = []
_INITIALIZED = False
_PREFERRED_ORDER = [
    'streamingcommunity', 
    'guardaserie', 
    'animeunity',
    'animeworld', 
    'crunchyroll', 
    'primevideo', 
    'mediasetinfinity', 
    'raiplay',
    'discoveryplus',
    'discovery',
    'dmax', 
    'nove', 
    'realtime',
    'homegardentv', 
    'foodnetwork', 
    'tubitv',
    'cinezo',
    'mostraguarda'
]


def _make_dynamic_api(svc_name: str) -> type:
    """Build a generic lazy-loading BaseStreamingAPI subclass for a service
    that lives in VibraVid/services/ but has no static stub in this package.
    Lets uploaded plugins appear in the GUI without writing files to /app/GUI."""
    from VibraVid.services._base.site_loader import get_folder_name

    class _DynamicAPI(BaseStreamingAPI):
        def __init__(self):
            super().__init__()
            self.site_name = svc_name
            self._search_fn = None
            self._GetSerieInfo = None

        def _get_search_fn(self):
            if self._search_fn is None:
                module = importlib.import_module(f"VibraVid.{get_folder_name()}.{self.site_name}")
                self._search_fn = getattr(module, "search")
            return self._search_fn

        def _get_serie_info_class(self):
            if self._GetSerieInfo is None:
                try:
                    module = importlib.import_module(f"VibraVid.{get_folder_name()}.{self.site_name}.scrapper")
                    self._GetSerieInfo = getattr(module, "GetSerieInfo", None)
                except ImportError:
                    self._GetSerieInfo = None
            return self._GetSerieInfo

        def search(self, query: str) -> List[Entries]:
            search_fn = self._get_search_fn()
            database = search_fn(query, get_onlyDatabase=True)
            results = []
            if database and hasattr(database, "media_list"):
                for element in list(database.media_list):
                    item_dict = element.__dict__.copy() if hasattr(element, "__dict__") else {}
                    results.append(Entries(
                        id=item_dict.get("id") or item_dict.get("slug"),
                        name=item_dict.get("name"),
                        slug=item_dict.get("slug", ""),
                        path_id=item_dict.get("path_id"),
                        type=item_dict.get("type"),
                        url=item_dict.get("url"),
                        poster=item_dict.get("image"),
                        year=item_dict.get("year"),
                        tmdb_id=item_dict.get("tmdb_id"),
                        provider_language=item_dict.get("provider_language"),
                        raw_data=item_dict,
                    ))
            return results

        def get_series_metadata(self, media_item: Entries):
            if media_item.is_movie:
                return None
            cls = self._get_serie_info_class()
            if cls is None:
                return None
            scrape_serie = self.get_cached_scraper(media_item)
            if not scrape_serie:
                try:
                    scrape_serie = cls(media_item.url)
                except TypeError:
                    try:
                        scrape_serie = cls(url=media_item.url)
                    except Exception:
                        return None
                self.set_cached_scraper(media_item, scrape_serie)
            try:
                if not scrape_serie.getNumberSeason():
                    return None
            except Exception:
                return None
            out = []
            for s in scrape_serie.seasons_manager.seasons:
                eps_raw = scrape_serie.getEpisodeSeasons(s.number) or []
                eps = []
                for idx, ep in enumerate(eps_raw, 1):
                    ep_num = (ep.get("number") if isinstance(ep, dict) else getattr(ep, "number", idx)) or idx
                    ep_name = ep.get("name") if isinstance(ep, dict) else getattr(ep, "name", None)
                    ep_id = ep.get("id") if isinstance(ep, dict) else getattr(ep, "id", idx)
                    eps.append(Episode(number=ep_num, name=ep_name or f"Episodio {idx}", id=ep_id))
                out.append(Season(number=s.number, episodes=eps, name=getattr(s, "name", None)))
            return out or None

        def start_download(self, media_item: Entries, season=None, episodes=None, audio_format=None) -> bool:
            search_fn = self._get_search_fn()
            selections: dict = {}
            if season or episodes:
                selections["season"] = season
                selections["episode"] = episodes
            # Music services (e.g. Spotify) read audio_format from selections.
            af = audio_format or getattr(media_item, "audio_format", None)
            if af:
                selections["audio_format"] = af
            scrape_serie = self.get_cached_scraper(media_item)
            search_fn(direct_item=media_item.raw_data, selections=(selections or None), scrape_serie=scrape_serie)
            return True

    _DynamicAPI.__name__ = f"{svc_name.capitalize()}API"
    return _DynamicAPI


def _discover_uploaded_services() -> List[str]:
    """List service names in VibraVid/services/ that don't have a static GUI stub.
    These get a dynamic in-memory stub so they still appear in the dropdown."""
    try:
        import sys as _sys
        try:
            services_root = _sys.modules['VibraVid.services'].__path__[0]
        except Exception:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            services_root = os.path.join(project_root, 'VibraVid', 'services')
        if not os.path.isdir(services_root):
            return []
        found = []
        for entry in os.listdir(services_root):
            full = os.path.join(services_root, entry)
            if (entry.startswith('_') or entry.startswith('.')
                or not os.path.isdir(full)
                or not os.path.isfile(os.path.join(full, '__init__.py'))):
                continue
            found.append(entry.lower())
        return found
    except Exception:
        return []


def _initialize_registry():
    global _INITIALIZED
    if _INITIALIZED:
        return

    package_dir = os.path.dirname(__file__)
    api_files = [
        f[:-3] for f in os.listdir(package_dir)
        if f.endswith('.py') and f not in ('base.py', '__init__.py')
    ]

    # Discover services that have no static stub — they'll get a dynamic one.
    dynamic_only = [s for s in _discover_uploaded_services() if s not in api_files]

    # Use preferred order first, then any remaining files
    sorted_files = [f for f in _PREFERRED_ORDER if f in api_files]
    sorted_files.extend([f for f in api_files if f not in _PREFERRED_ORDER])
    # Append dynamic-only services at the end (also respecting preferred order if listed)
    sorted_files.extend([f for f in _PREFERRED_ORDER if f in dynamic_only and f not in sorted_files])
    sorted_files.extend([f for f in dynamic_only if f not in sorted_files])

    # Build into a local dict first; only commit to _API_REGISTRY if we end up
    # with at least as many services as we already had. Without this, a bad
    # reload (e.g. one module raising during import) can silently shrink the
    # registry and the dropdown loses services that were working before.
    previous_count = len(_API_REGISTRY)
    new_registry: Dict[str, type] = {}
    load_errors: List[str] = []
    _LOAD_ERRORS.clear()

    discovered_services = set(_discover_uploaded_services())

    for idx, module_name in enumerate(sorted_files):
        loaded = False
        try:
            if module_name in api_files:
                module = importlib.import_module(f'.{module_name}', package=__package__)
                api_cls = None
                for name, obj in module.__dict__.items():
                    if (isinstance(obj, type) and
                        issubclass(obj, BaseStreamingAPI) and
                        obj is not BaseStreamingAPI):
                        api_cls = obj
                        break
                if api_cls is None:
                    raise RuntimeError("no BaseStreamingAPI subclass found in module")
                api_cls._indice = idx
                new_registry[module_name] = api_cls
                loaded = True
        except Exception as e:
            err = f"{module_name}: static stub failed ({type(e).__name__}: {e})"
            load_errors.append(err)
            print(f"[Warning] Static GUI API '{module_name}' failed to import: {e}")
            print(f"[Info]    Falling back to dynamic stub for '{module_name}'.")

        # If the static stub failed OR no static stub exists, fall back to a
        # dynamic in-memory stub as long as the service actually exists in
        # VibraVid/services/. This guarantees the dropdown always shows every
        # installed service, even if a static stub has a stale top-level import.
        if not loaded and module_name in discovered_services:
            try:
                dyn_cls = _make_dynamic_api(module_name)
                dyn_cls._indice = idx
                new_registry[module_name] = dyn_cls
            except Exception as e:
                err = f"{module_name}: dynamic stub failed ({type(e).__name__}: {e})"
                load_errors.append(err)
                print(f"[Warning] Could not build dynamic API '{module_name}': {e}")

    # Commit: if new load found at least as many as before, replace; otherwise
    # only ADD newly-discovered services so we never lose ones that were already
    # working.
    if len(new_registry) >= previous_count:
        _API_REGISTRY.clear()
        _API_REGISTRY.update(new_registry)
    else:
        for k, v in new_registry.items():
            _API_REGISTRY[k] = v
        print(f"[Warning] Reload produced fewer APIs ({len(new_registry)}) than before ({previous_count}); kept old entries to avoid losing services.")

    _LOAD_ERRORS.extend(load_errors)

    if not _API_REGISTRY:
        print("[CRITICAL] No streaming APIs could be loaded! Check that all dependencies are installed (pip install -r requirements.txt).")
        if load_errors:
            print("[CRITICAL] Load errors:")
            for err in load_errors:
                print(f"  - {err}")
    else:
        print(f"[Info] Loaded {len(_API_REGISTRY)} streaming APIs: {', '.join(_API_REGISTRY.keys())}")
        if load_errors:
            print(f"[Warning] {len(load_errors)} API(s) failed to load:")
            for err in load_errors:
                print(f"  - {err}")

    _INITIALIZED = True


def get_load_errors() -> List[str]:
    """Return the list of import errors from the last _initialize_registry() call."""
    return list(_LOAD_ERRORS)

_initialize_registry()


def get_available_sites() -> List[str]:
    """
    Get list of all available streaming sites.
    
    Returns:
        List of site identifiers
    """
    return list(_API_REGISTRY.keys())


def get_api(site: str) -> BaseStreamingAPI:
    """
    Get API instance for specified site.
    
    Args:
        site: Site identifier (e.g., 'streamingcommunity', 'animeunity', 'mostraguarda')
        
    Returns:
        API instance
        
    Raises:
        ValueError: If site is not supported
    """
    site_lower = site.lower().strip()
    
    if site_lower not in _API_REGISTRY:
        available = ', '.join(_API_REGISTRY.keys())
        raise ValueError(f"Site '{site}' not supported. Available sites: {available}")
    
    api_class = _API_REGISTRY[site_lower]
    return api_class()


def is_site_available(site: str) -> bool:
    """
    Check if a site is available.
    
    Args:
        site: Site identifier
        
    Returns:
        True if site is available
    """
    return site.lower().strip() in _API_REGISTRY


__all__ = [
    'get_available_sites',
    'get_api',
    'is_site_available',
    'get_load_errors',
]
