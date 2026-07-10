# 12.06.26

import os
import logging
import threading
from typing import Optional

from VibraVid.utils.http_client import create_client
from VibraVid.utils.config import config_manager
from VibraVid.utils.vault_upload import client

logger = logging.getLogger(__name__)

STORE_URL = config_manager.config.get_dict("HOOKS", "db_info", default={}).get("url", "")
STORE_TOKEN = config_manager.config.get_dict("HOOKS", "db_info", default={}).get("token", "")


class ExternalUploadVault:
    def __init__(self):
        self.base_url = STORE_URL.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if STORE_TOKEN:
            headers["Authorization"] = f"Bearer {STORE_TOKEN}"
        
        self.api = create_client(headers=headers, http2=True)
        self.storage = create_client(timeout=120)
        self._lock = threading.Lock()

    def close(self):
        for s in (getattr(self, "api", None), getattr(self, "storage", None)):
            try:
                if s:
                    s.close()
            except Exception:
                pass

    def search(self, title: str, media_type: Optional[str] = None, season: Optional[int] = None, episode: Optional[int] = None) -> Optional[dict]:
        if not self.base_url or not title:
            return None
        
        params: dict = {"title": title.strip()}
        if media_type:
            params["type"] = str(media_type).strip().lower()
        if season is not None:
            params["season"] = str(int(season))
        if episode is not None:
            params["episode"] = str(int(episode))
        try:
            with self._lock:
                r = self.api.get(f"{self.base_url}/search", params=params)
            r.raise_for_status()
            data = r.json()
            return data if data.get("found") else None
        except Exception as e:
            logger.debug(f"upload store search error: {e}")
            return None

    def upload(self, file_path: str, title: Optional[str] = None, media_type: Optional[str] = None, season: Optional[int] = None, episode: Optional[int] = None, category: Optional[str] = None, expiry_days: Optional[int] = None, on_progress=None) -> Optional[str]:
        if not self.base_url or not os.path.isfile(file_path):
            return None

        filename = os.path.basename(file_path)
        size = os.path.getsize(file_path)
        try:
            create_payload = {"filename": filename, "size": size, "mtime": int(os.path.getmtime(file_path))}
            if title:
                create_payload["title"] = title
            if expiry_days is not None:
                create_payload["expiryDays"] = expiry_days
            
            r = self.api.post(f"{self.base_url}/upload/create", json=create_payload)
            r.raise_for_status()
            c = r.json()
            sid, xh, root_h, pool = c["sid"], c["xh"], c["rootH"], c["pool"]

            ul_key = client.rand_a32(6)
            token, macs = client.upload_file(self.storage, file_path, pool, ul_key, on_progress=on_progress)
            filekey = client.compute_file_key(ul_key, macs)

            finalize = {
                "sid": sid, "xh": xh, "rootH": root_h,
                "token": client.b64_encode(token), "filekey": filekey,
                "filename": filename, "size": size,
            }
            for k, v in (("title", title), ("mediaType", media_type), ("category", category)):
                if v:
                    finalize[k] = v
            if season is not None:
                finalize["season"] = season
            if episode is not None:
                finalize["episode"] = episode
            if expiry_days is not None:
                finalize["expiryDays"] = expiry_days
            
            r = self.api.post(f"{self.base_url}/upload/finalize", json=finalize)
            r.raise_for_status()
            link = r.json().get("link")
            logger.info(f"upload store: uploaded {filename} -> {link}")
            return link
        except Exception as e:
            logger.error(f"upload store upload error: {e}", exc_info=True)
            return None

    def download(self, xh: str, dest_path: str, password: Optional[str] = None, on_progress=None) -> Optional[str]:
        if not self.base_url or not xh:
            return None
        
        try:
            with self._lock:
                r = self.api.get(f"{self.base_url}/transfer/{xh}", params={"pw": password} if password else None)
            r.raise_for_status()
            files = r.json().get("files") or []
            if not files:
                return None
            node = files[0]

            dl = {"xh": xh, "handle": node["handle"]}
            if password:
                dl["password"] = password
            r = self.api.post(f"{self.base_url}/download", json=dl)
            r.raise_for_status()
            info = r.json()
            url = info.get("url")
            if not url:
                return None

            client.download_file(self.storage, url, dest_path, client.b64_to_a32(node["key"]), total=info.get("size") or node.get("size"), on_progress=on_progress)
            logger.info(f"upload store: downloaded -> {dest_path}")
            return dest_path
        except Exception as e:
            logger.error(f"upload store download error: {e}", exc_info=True)
            return None

    def fetch_if_present(self, dest_path: str, title: str, media_type: Optional[str] = None, season: Optional[int] = None, episode: Optional[int] = None, on_progress=None) -> Optional[str]:
        hit = self.search(title, media_type, season, episode)
        if not hit:
            return None
        return self.download(hit["xh"], dest_path, on_progress=on_progress)


is_upload_vault_valid = bool(STORE_URL)
upload_vault = ExternalUploadVault() if is_upload_vault_valid else None