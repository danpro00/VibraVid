# 16.07.26

import time
import json5
import logging
from typing import Optional, Tuple, Callable
from urllib.parse import urlencode, urlparse, parse_qs

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client

from .cloudflare import FlareSolverr


console = Console()
logger = logging.getLogger(__name__)

_MAX_POLL_SECONDS = 600
_POLL_INTERVAL = 1.0
_POLL_MAX_TRANSIENT_ERRORS = 15
_RESOLVE_TRANSIENT_ERRORS = (
    "An error occured trying to process your request.",
    'Message: "Cannot contact any valid server"',
    "An error occurred. Had an issue getting that item, try again.",
)
_RESOLVE_RETRIES = 3
_RESOLVE_RETRY_DELAY = 5.0


def _default_base_url() -> str:
    """Resolve the lucida base url from domains.json, falling back to the public host."""
    return config_manager.domain.get("lucida", "full_url").rstrip("/")


class LucidaError(Exception):
    """Raised for unrecoverable lucida API errors (bad url, server refusal)."""


class LucidaClient:
    def __init__(self, country: str = "auto", metadata: bool = True, private: bool = False) -> None:
        self.country = (country or "auto").strip() or "auto"
        self.metadata = bool(metadata)
        self.private = bool(private)
        self.base_url = _default_base_url()
        self._session = create_client(browser="chrome", http2=True)
        self._cf = FlareSolverr.from_config()

    def _apply_clearance(self, clearance) -> None:
        """Inject FlareSolverr's cf_clearance cookie + matching UA into the session."""
        if not clearance:
            return
        if clearance.user_agent:
            self._session.headers["User-Agent"] = clearance.user_agent
        raw = getattr(clearance, "raw", None) or []
        if raw:
            for c in raw:
                name = c.get("name")
                if not name:
                    continue
                try:
                    self._session.cookies.set(
                        name, c.get("value", ""),
                        domain=c.get("domain", "") or "", path=c.get("path", "/") or "/",
                    )
                except Exception:
                    try:
                        self._session.cookies.set(name, c.get("value", ""))
                    except Exception:
                        pass
        else:
            for name, value in clearance.cookies.items():
                try:
                    self._session.cookies.set(name, value)
                except Exception:
                    pass

    @staticmethod
    def _is_cf_challenge(resp, peek_body: bool) -> bool:
        """Detect a Cloudflare interstitial ("Just a moment...") response."""
        if resp.status_code not in (403, 503):
            return False
        if str(resp.headers.get("cf-mitigated", "")).lower() == "challenge":
            return True
        if not peek_body:
            return False
        try:
            body = resp.text
        except Exception:
            return False
        return ("Just a moment" in body) or ("cf-chl" in body) or ("challenge-platform" in body)

    def _request(self, method: str, url: str, *, stream: bool = False, _cf_retried: bool = False, **kwargs):
        """Session request wrapper that transparently solves Cloudflare challenges."""
        if self._cf and not _cf_retried:
            cached = self._cf.peek(url)
            if cached:
                self._apply_clearance(cached)

        resp = self._session.request(method, url, stream=stream, **kwargs)

        if not _cf_retried and self._cf and self._is_cf_challenge(resp, peek_body=not stream):
            logger.info(f"[lucida] Cloudflare challenge on {url}; solving via FlareSolverr…")
            clearance = self._cf.clearance(url, force=True)
            if clearance and clearance.cookies.get("cf_clearance"):
                self._apply_clearance(clearance)
                try:
                    resp.close()
                except Exception:
                    pass
                return self._request(method, url, stream=stream, _cf_retried=True, **kwargs)
            raise LucidaError(
                "lucida.to is behind a Cloudflare challenge and it could not be solved. "
                "Ensure the FlareSolverr service is running and FLARESOLVERR_URL is set."
            )

        if not _cf_retried and not self._cf and self._is_cf_challenge(resp, peek_body=not stream):
            raise LucidaError(
                "lucida.to returned a Cloudflare challenge. Start the FlareSolverr service "
                "and set FLARESOLVERR_URL to let VibraVid solve it automatically."
            )

        return resp

    @staticmethod
    def normalize_input(raw: str) -> Tuple[str, Optional[str]]:
        """Accept either a bare service url or a full lucida.to/?url=... link."""
        raw = (raw or "").strip()
        try:
            parsed = urlparse(raw)
        except Exception:
            return raw, None

        if parsed.netloc.endswith("lucida.to"):
            qs = parse_qs(parsed.query)
            inner = (qs.get("url") or [None])[0]
            country = (qs.get("country") or [None])[0]
            if inner:
                return inner, country
        return raw, None

    def resolve(self, source_url: str) -> dict:
        """Resolve a service url into lucida PageData (dict)."""
        source_url, country_override = self.normalize_input(source_url)
        country = country_override or self.country

        params = urlencode({"url": source_url, "country": country})
        url = f"{self.base_url}/?{params}"
        logger.info(f"[lucida] resolving page: source={source_url!r} country={country!r}")

        for attempt in range(1, _RESOLVE_RETRIES + 1):
            try:
                resp = self._request("GET", url, timeout=60)
            except LucidaError:
                raise
            except Exception as e:
                logger.exception(f"[lucida] GET resolve request failed: {e}")
                raise LucidaError(f"Network error contacting lucida.to: {e}")

            logger.info(f"[lucida] resolve HTTP {resp.status_code} ({len(resp.text)} bytes)")
            if resp.status_code == 403:
                logger.error("[lucida] 403 Forbidden — Cloudflare challenge blocking the request.")
                raise LucidaError("lucida.to returned 403 (Cloudflare challenge). Retry later or configure a proxy.")
            resp.raise_for_status()

            html = resp.text
            transient_err = next((err for err in _RESOLVE_TRANSIENT_ERRORS if err in html), None)
            if transient_err:
                if attempt < _RESOLVE_RETRIES:
                    logger.warning(
                        f"[lucida] page reports transient error (attempt {attempt}/{_RESOLVE_RETRIES}): "
                        f"{transient_err!r}; retrying in {_RESOLVE_RETRY_DELAY}s"
                    )
                    time.sleep(_RESOLVE_RETRY_DELAY)
                    continue
                logger.error(f"[lucida] page still reports error after {_RESOLVE_RETRIES} attempts: {transient_err}")
                raise LucidaError(
                    f"lucida.to is failing this request server-side ({transient_err!r}) after "
                    f"{_RESOLVE_RETRIES} attempts. This is an upstream lucida.to outage, not a "
                    "VibraVid bug — it usually self-resolves within a few hours; try again later."
                )

            data = self._extract_page_data(html)
            if not data:
                if attempt < _RESOLVE_RETRIES:
                    logger.warning(f"[lucida] could not extract PageData (attempt {attempt}/{_RESOLVE_RETRIES}); retrying in {_RESOLVE_RETRY_DELAY}s")
                    time.sleep(_RESOLVE_RETRY_DELAY)
                    continue
                raise LucidaError(
                    "Could not extract PageData from the lucida page "
                    "(site layout changed or Cloudflare challenge). "
                    + self._page_diagnostic(html, resp.status_code)
                )
            logger.info(f"[lucida] resolved type={((data.get('info') or {}).get('type'))!r} service={data.get('originalService')!r}")
            return data

    @staticmethod
    def _page_diagnostic(html: str, status: int) -> str:
        """Short, inline diagnostic for a page we couldn't parse."""
        low = html.lower()
        cloudflare = ("cloudflare" in low) or ("cf-chl" in low) or ("challenge-platform" in low)
        head = " ".join(html[:200].split())
        return (
            f"[HTTP {status}, {len(html)}B, cloudflare={cloudflare}, "
            f"sveltekit={'__sveltekit' in html}, api/fetch={'api/fetch' in html}] "
            f"head: {head!r}"
        )

    @staticmethod
    def _extract_page_data(html: str) -> Optional[dict]:
        """Pull the SSR-embedded PageData object out of the lucida HTML."""
        start_marker = ',{"type":"data","data":'
        end_marker = ',"uses":{"url":1}'

        start = html.find(start_marker)
        if start == -1:
            logger.error(
                "[lucida] PageData marker not found. markers present: "
                f"__sveltekit={'__sveltekit' in html}, api/fetch={'api/fetch' in html}. "
                f"HTML head: {html[:400]!r}"
            )
            return None

        start += len(start_marker)
        end = html.find(end_marker, start)
        if end == -1:
            logger.error(f"[lucida] could not locate end of PageData object at offset {start}.")
            return None

        blob = html[start:end]
        try:
            return json5.loads(blob)
        except Exception as e:
            logger.error(f"[lucida] PageData JSON5 decode failed: {e}. blob head: {blob[:500]!r}")
            return None

    def request_download(self, track_url: str, csrf: str, csrf_fallback: Optional[str], token_expiry: int) -> Tuple[str, str]:
        """Ask lucida to prepare a track. Returns (server, handoff)."""
        payload = {
            "account": {"id": self.country, "type": "country"},
            "compat": False,
            "downscale": "flac-16",
            "handoff": True,
            "metadata": self.metadata,
            "private": self.private,
            "token": {"expiry": token_expiry, "primary": csrf, "secondary": csrf_fallback},
            "upload": {"enabled": False},
            "url": track_url,
        }

        url = f"{self.base_url}/api/load?url=%2Fapi%2Ffetch%2Fstream%2Fv2"
        logger.info(f"[lucida] request_download url={track_url!r} country={self.country!r} expiry={token_expiry}")

        for attempt in range(1, _RESOLVE_RETRIES + 1):
            try:
                resp = self._request("POST", url, json=payload, timeout=60)
            except LucidaError:
                raise
            except Exception as e:
                logger.exception(f"[lucida] POST /api/load failed: {e}")
                raise LucidaError(f"Network error on lucida /api/load: {e}")

            logger.info(f"[lucida] /api/load HTTP {resp.status_code}")
            # lucida sometimes answers /api/load with a transient 404/5xx (job
            # routing hiccup); retry a few times before giving up.
            if resp.status_code in (404, 500, 502, 503) and attempt < _RESOLVE_RETRIES:
                logger.warning(
                    f"[lucida] /api/load transient HTTP {resp.status_code} "
                    f"(attempt {attempt}/{_RESOLVE_RETRIES}); retrying in {_RESOLVE_RETRY_DELAY}s"
                )
                time.sleep(_RESOLVE_RETRY_DELAY)
                continue
            resp.raise_for_status()

            data = resp.json()
            if "error" in data:
                logger.error(f"[lucida] load error: {data.get('error')}")
                raise LucidaError(f"lucida load error: {data.get('error')}")

            server, handoff = data.get("server"), data.get("handoff")
            if not server or not handoff:
                logger.error(f"[lucida] load returned no server/handoff: {data}")
                raise LucidaError(f"lucida load returned no server/handoff: {data}")
            logger.info(f"[lucida] prepared server={server} handoff={handoff}")
            return server, handoff

        raise LucidaError(f"lucida /api/load kept failing after {_RESOLVE_RETRIES} attempts.")

    def wait_until_ready(self, server: str, handoff: str, on_status: Optional[Callable[[str, str], None]] = None, stop_check: Optional[Callable[[], bool]] = None) -> bool:
        """Poll the processing status until 'completed'. Returns False if aborted."""
        url = f"https://{server}.lucida.to/api/fetch/request/{handoff}"
        deadline = time.time() + _MAX_POLL_SECONDS
        last_key = None
        transient_errors = 0

        while time.time() < deadline:
            if stop_check and stop_check():
                return False

            resp = self._request("GET", url, timeout=30)
            if resp.status_code in (404, 500):
                transient_errors += 1
                if transient_errors > _POLL_MAX_TRANSIENT_ERRORS:
                    raise LucidaError(
                        f"lucida processing failed (HTTP {resp.status_code} after "
                        f"{transient_errors} consecutive attempts)."
                    )
                logger.warning(
                    f"[lucida] transient HTTP {resp.status_code} while polling "
                    f"(#{transient_errors}/{_POLL_MAX_TRANSIENT_ERRORS}); retrying"
                )
                time.sleep(_POLL_INTERVAL)
                continue
            transient_errors = 0
            resp.raise_for_status()

            data = resp.json()
            status = str(data.get("status", ""))
            message = str(data.get("message", ""))

            key = (status, message)
            if key != last_key:
                last_key = key
                logger.info(f"[lucida] status={status!r} message={message!r}")
                if on_status:
                    on_status(status, message)

            if status == "completed":
                return True
            if status == "error":
                logger.error(f"[lucida] processing error: {message or 'unknown'}")
                raise LucidaError(f"lucida processing error: {message or 'unknown'}")

            time.sleep(_POLL_INTERVAL)

        logger.error("[lucida] processing timed out.")
        raise LucidaError("lucida processing timed out.")

    @staticmethod
    def download_url(server: str, handoff: str) -> str:
        """Build the single-use download url for a prepared (server, handoff) pair."""
        return f"https://{server}.lucida.to/api/fetch/request/{handoff}/download"

    def download_headers(self) -> dict:
        """Headers needed to fetch download_url() with an out-of-session downloader
        (e.g. MP4_Downloader), which doesn't reuse this client's curl_cffi session.
        """
        cookie = "; ".join(f"{k}={v}" for k, v in self._session.cookies.items())
        headers = {"Referer": f"{self.base_url}/"}
        if cookie:
            headers["Cookie"] = cookie
        ua = self._session.headers.get("User-Agent")
        if ua:
            headers["User-Agent"] = ua
        return headers