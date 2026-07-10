# 21.05.24

from VibraVid.core.ui.tracker import context_tracker

DEFAULT_REGION = "it"

REGIONS = {
    "it": {
        "label": "Italia",
        "home_url": "https://mediasetinfinity.mediaset.it/",
        "site_base": "https://mediasetinfinity.mediaset.it",
        "origin": "https://mediasetinfinity.mediaset.it",
        "login_url": "https://api-ott-prod-fe.mediaset.net/PROD/play/idm/anonymous/login/v2.0",
        "playback_url": "https://api-ott-prod-fe.mediaset.net/PROD/play/playback/check/v2.0",
        "playback_api": "v2",
        "graphql_url": "https://mediasetplay.api-graph.mediaset.it/",
        "property": "MPLAY",
        "login_key": "mediasetinfinity",
        "feed_public_id": "PR1GhC",
        "geo": "geoIT|geoNo",
        "image_region": "ita",
        "qualities_full": ("HD", "HR", "SD", "SS"),
        "qualities_anon": ("HR", "SD", "SS"),
    },
    "es": {
        "label": "Espana",
        "home_url": "https://www.mediasetinfinity.es/",
        "site_base": "https://www.mediasetinfinity.es",
        "origin": "https://www.mediasetinfinity.es",
        "login_url": "https://services-ott-prod-fe.mediaset.net/esp/idm/v3.0/anonymous/login",
        "playback_url": "https://services-ott-prod-fe.mediaset.net/esp/playback/v3.0/check",
        "playback_api": "v3",
        "graphql_url": "https://ottesp.api-graph.mediaset.it/",
        "property": "MITELE",
        "login_key": "mediasetinfinityes",
        "feed_public_id": "PR1GhC",
        "geo": "geoES|geoNo",
        "image_region": "esp",
        "qualities_full": ("4K", "HD", "HR", "SD", "SS"),
        "qualities_anon": ("HR", "SD", "SS"),
    },
}


def get_region() -> str:
    """Return the active region code ('it' or 'es'), defaulting to it."""
    try:
        raw = (context_tracker.site_options or {}).get("country")
    except Exception:
        raw = None
    code = (raw or DEFAULT_REGION).strip().lower()
    return code if code in REGIONS else DEFAULT_REGION


def region_conf() -> dict:
    """Return the config dict for the active region."""
    return REGIONS[get_region()]