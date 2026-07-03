# 12.06.26

import os
import struct
import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Optional, Callable

from Cryptodome.Cipher import AES
from Cryptodome.Util import Counter

logger = logging.getLogger(__name__)


def a32_to_bytes(a: List[int]) -> bytes:
    return struct.pack(f">{len(a)}I", *[x & 0xFFFFFFFF for x in a])

def bytes_to_a32(b: bytes) -> List[int]:
    if len(b) % 4:
        b += b"\0" * (4 - len(b) % 4)
    return list(struct.unpack(f">{len(b) // 4}I", b))

def b64_encode(b: bytes) -> str:
    return base64.b64encode(b).decode().replace("+", "-").replace("/", "_").rstrip("=")

def b64_decode(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)

def b64_to_a32(s: str) -> List[int]:
    return bytes_to_a32(b64_decode(s))

def rand_a32(n: int) -> List[int]:
    return bytes_to_a32(os.urandom(n * 4))

def attr_key(k: List[int]) -> List[int]:
    """De-obfuscated AES key from a full 8-word file key."""
    return [k[0] ^ k[4], k[1] ^ k[5], k[2] ^ k[6], k[3] ^ k[7]]

def _nonce8(ul_key: List[int]) -> bytes:
    return a32_to_bytes([ul_key[4], ul_key[5]])

def get_chunks(size: int) -> List[Tuple[int, int]]:
    chunks: List[Tuple[int, int]] = []
    pos = 0
    for s in (0x20000, 0x40000, 0x60000, 0x80000, 0xA0000, 0xC0000, 0xE0000, 0x100000):
        if pos >= size:
            return chunks
        ln = min(s, size - pos)
        chunks.append((pos, ln))
        pos += ln
    while pos < size:
        ln = min(0x100000, size - pos)
        chunks.append((pos, ln))
        pos += ln
    return chunks

def ctr_encrypt(ul_key: List[int], pos: int, plaintext: bytes) -> bytes:
    ctr = Counter.new(64, prefix=_nonce8(ul_key), initial_value=pos // 16)
    return AES.new(a32_to_bytes(ul_key[:4]), AES.MODE_CTR, counter=ctr).encrypt(plaintext)

def chunk_mac(ul_key: List[int], plaintext: bytes) -> bytes:
    pad_len = (len(plaintext) + 15) // 16 * 16 or 16
    padded = plaintext + b"\0" * (pad_len - len(plaintext))
    iv = _nonce8(ul_key) + _nonce8(ul_key)
    return AES.new(a32_to_bytes(ul_key[:4]), AES.MODE_CBC, iv).encrypt(padded)[-16:]

def compute_file_key(ul_key: List[int], macs: List[bytes]) -> List[int]:
    key16 = a32_to_bytes(ul_key[:4])
    acc = [0, 0, 0, 0]
    for mac in macs:
        m = bytes_to_a32(mac)
        acc = [acc[0] ^ m[0], acc[1] ^ m[1], acc[2] ^ m[2], acc[3] ^ m[3]]
        acc = bytes_to_a32(AES.new(key16, AES.MODE_ECB).encrypt(a32_to_bytes(acc)))
    mm0, mm1 = acc[0] ^ acc[1], acc[2] ^ acc[3]
    return [
        ul_key[0] ^ ul_key[4], ul_key[1] ^ ul_key[5],
        ul_key[2] ^ mm0, ul_key[3] ^ mm1,
        ul_key[4], ul_key[5], mm0, mm1,
    ]

def pick_pool_entry(pool: list, size: int) -> list:
    for e in pool:
        limit = e[2] if len(e) > 2 else None
        if limit is None or size <= limit:
            return e
    return pool[-1]

def upload_file(session, file_path: str, pool: list, ul_key: List[int], concurrency: int = 4, on_progress: Optional[Callable[..., None]] = None) -> Tuple[bytes, List[bytes]]:
    """Encrypt + upload *file_path* directly to the storage nodes"""
    size = os.path.getsize(file_path)
    entry = pick_pool_entry(pool, size)
    base = f"https://{entry[0]}/{entry[1]}"

    if size == 0:
        r = session.post(f"{base}/0", data=b"")
        return r.content, []

    chunks = get_chunks(size)
    macs: List[Optional[bytes]] = [None] * len(chunks)
    token: Optional[bytes] = None
    uploaded = {"n": 0}
    _fh = {}

    def _read(pos: int, ln: int) -> bytes:
        import threading
        tid = threading.get_ident()
        fh = _fh.get(tid)
        if fh is None:
            fh = open(file_path, "rb")
            _fh[tid] = fh
        fh.seek(pos)
        return fh.read(ln)

    def work(idx: int):
        nonlocal token
        pos, ln = chunks[idx]
        plain = _read(pos, ln)
        macs[idx] = chunk_mac(ul_key, plain)
        enc = ctr_encrypt(ul_key, pos, plain)
        r = session.post(f"{base}/{pos}", data=enc)
        if r.status_code != 200:
            raise RuntimeError(f"storage upload HTTP {r.status_code} at offset {pos}")
        resp = r.content
        if 0 < len(resp) < 16:
            raise RuntimeError(f"storage upload error: {resp.decode(errors='replace')}")
        if len(resp) >= 16:
            token = resp
        uploaded["n"] += ln
        if on_progress:
            on_progress(uploaded["n"], size)

    try:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(chunks))) as ex:
            list(ex.map(work, range(len(chunks))))
    finally:
        for fh in _fh.values():
            try:
                fh.close()
            except Exception:
                pass

    if not token:
        raise RuntimeError("no completion token received")
    return token, [m for m in macs if m is not None]

def download_file(session, url: str, dest_path: str, node_key_a32: List[int], total: Optional[int] = None, on_progress: Optional[Callable[..., None]] = None, chunk_size: int = 1 << 20) -> str:
    """Download from a storage URL and AES-CTR decrypt to *dest_path*."""
    aes_key = a32_to_bytes(attr_key(node_key_a32))
    nonce8 = a32_to_bytes([node_key_a32[4], node_key_a32[5]])
    ctr = Counter.new(64, prefix=nonce8, initial_value=0)
    cipher = AES.new(aes_key, AES.MODE_CTR, counter=ctr)

    os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
    r = session.get(url, stream=True)
    try:
        r.raise_for_status()
        if total is None:
            try:
                total = int(r.headers.get("Content-Length") or 0) or None
            except (TypeError, ValueError):
                total = None
        written = 0
        with open(dest_path, "wb") as out:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                out.write(cipher.decrypt(chunk))
                written += len(chunk)
                if on_progress:
                    on_progress(written, total)
        return dest_path
    finally:
        try:
            r.close()
        except Exception:
            pass