# 16.07.26

import os
import time
import logging
import threading
from typing import Dict, Optional
from urllib.parse import urlparse

from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.http_client import create_client


logger = logging.getLogger(__name__)
_CACHE_SERVICE = "lucida"
_CACHE_NAME = "cf_session"


def _flaresolverr_url() -> Optional[str]:
    """Resolve the FlareSolverr endpoint from env (compose), config, or the default local sidecar address"""
    url = os.environ.get("FLARESOLVERR_URL")
    if not url:
        try:
            url = config_manager.config.get("REQUESTS", "flaresolverr_url", str, default="http://localhost:8191")
        except Exception:
            url = "http://localhost:8191"
    url = (url or "").strip().rstrip("/")
    return url or None


def _host(url: str) -> str:
    try:
        return urlparse(url).hostname or url
    except Exception:
        return url


class CFClearance:
    def __init__(self, cookies: Dict[str, str], user_agent: str, expiry: float, raw=None) -> None:
        self.cookies = cookies            # name -> value (quick lookups)
        self.raw = raw or []              # full cookie dicts (name/value/domain/path)
        self.user_agent = user_agent
        self.expiry = expiry

    def valid(self) -> bool:
        return bool(self.cookies.get("cf_clearance")) and disk_cache.is_fresh({"expiry": self.expiry}, buffer_seconds=300)

    def to_dict(self) -> dict:
        return {"cookies": self.cookies, "raw": self.raw, "user_agent": self.user_agent, "expiry": self.expiry}

    @classmethod
    def from_dict(cls, d: dict) -> "CFClearance":
        return cls(d.get("cookies") or {}, d.get("user_agent") or "", float(d.get("expiry") or 0), raw=d.get("raw"))


def _load_disk_cache() -> Dict[str, CFClearance]:
    raw = disk_cache.load(_CACHE_SERVICE, _CACHE_NAME)
    if not raw:
        return {}
    try:
        return {host: CFClearance.from_dict(d) for host, d in raw.items()}
    except Exception:
        return {}


def _save_disk_cache(cache: Dict[str, CFClearance]) -> None:
    disk_cache.save(_CACHE_SERVICE, _CACHE_NAME, {host: c.to_dict() for host, c in cache.items()})


class FlareSolverr:
    _cache: Dict[str, CFClearance] = _load_disk_cache()
    _lock = threading.Lock()

    def __init__(self, endpoint: str, max_timeout_ms: int = 60000) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.max_timeout_ms = max_timeout_ms

    @classmethod
    def from_config(cls) -> Optional["FlareSolverr"]:
        """Build a client if a FlareSolverr endpoint is configured, else None."""
        url = _flaresolverr_url()
        if not url:
            return None
        return cls(url)

    def peek(self, url: str) -> Optional[CFClearance]:
        """Return a cached, still-valid clearance for the url's host, if any."""
        host = _host(url)
        with self._lock:
            cached = self._cache.get(host)
        return cached if (cached and cached.valid()) else None

    def clearance(self, url: str, force: bool = False) -> Optional[CFClearance]:
        """Return a valid clearance for the url's host, solving if needed."""
        if not force:
            cached = self.peek(url)
            if cached:
                return cached

        solved = self._solve(url)
        if solved and solved.cookies.get("cf_clearance"):
            with self._lock:
                self._cache[_host(url)] = solved
                _save_disk_cache(self._cache)
            return solved
        return solved  # may be None or a partial (no cf_clearance) result

    def _solve(self, url: str) -> Optional[CFClearance]:
        payload = {"cmd": "request.get", "url": url, "maxTimeout": self.max_timeout_ms}
        logger.info(f"[cf] solving Cloudflare challenge for {url} via {self.endpoint}")
        client = None
        try:
            # FlareSolverr is our own trusted service: no impersonation needed.
            client = create_client(browser=None, timeout=(self.max_timeout_ms / 1000) + 15)
            resp = client.post(f"{self.endpoint}/v1", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"[cf] FlareSolverr request failed: {e}")
            return None
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

        if str(data.get("status")) != "ok":
            logger.error(f"[cf] FlareSolverr did not solve: {data.get('message')!r}")
            return None

        solution = data.get("solution") or {}
        ua = solution.get("userAgent") or ""
        raw_cookies = solution.get("cookies") or []
        cookies = {c.get("name"): c.get("value") for c in raw_cookies if c.get("name")}

        expiry = 0.0
        for c in raw_cookies:
            if c.get("name") == "cf_clearance":
                try:
                    expiry = float(c.get("expires") or 0)
                except (TypeError, ValueError):
                    expiry = 0.0
        if not expiry:
            expiry = time.time() + 1800  # conservative fallback (30 min)

        if "cf_clearance" not in cookies:
            logger.warning("[cf] FlareSolverr solved the page but returned no cf_clearance cookie.")
        else:
            logger.info(f"[cf] obtained cf_clearance for {_host(url)} (ua={ua[:40]!r}…)")

        return CFClearance(cookies, ua, expiry, raw=raw_cookies)