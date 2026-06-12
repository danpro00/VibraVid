# 16.04.24

import gzip
import logging
from pathlib import Path


logger = logging.getLogger(__name__)


def _merge_fmt_size(nb: int) -> str:
    if nb >= 1_073_741_824:
        return f"{nb / 1_073_741_824:.2f}GB"
    if nb >= 1_048_576:
        return f"{nb / 1_048_576:.1f}MB"
    if nb >= 1_024:
        return f"{nb / 1024:.0f}KB"
    return f"{nb}B"


def _segment_number(path: Path) -> int:
    try:
        stem = path.stem
        if stem.startswith("seg_"):
            return int(stem[4:])
    except (ValueError, IndexError):
        pass
    return 999_999_999


def binary_merge_segments(paths: list[Path], output_path: Path, merge_logger: logging.Logger | None = None) -> None:
    """Merge downloaded segments using direct raw binary concatenation."""
    logger.info("Binary merge v2 ...")
    log = merge_logger or logger
    valid = [(p, _segment_number(p)) for p in paths if p.exists() and p.stat().st_size > 0]
    valid.sort(key=lambda item: item[1])
    if not valid:
        log.error("[binary_merge] No valid segments found")
        return

    log.info(f"[binary_merge] raw concat {len(valid)} segments -> {output_path.name}")
    total_written = 0
    with open(output_path, "wb") as out_f:
        for seg_path, _ in valid:
            chunk = seg_path.read_bytes()

            # Check if this chunk is gzip-compressed (magic bytes: 1f 8b)
            if len(chunk) >= 2 and chunk[0:2] == b'\x1f\x8b':
                try:
                    log.info(f"[binary_merge] detected gzip-compressed segment: {seg_path.name}, decompressing ...")
                    chunk = gzip.decompress(chunk)
                except Exception as e:
                    log.warning(f"[binary_merge] failed to decompress {seg_path.name}: {e}, using raw data")

            out_f.write(chunk)
            total_written += len(chunk)

    if output_path.exists() and output_path.stat().st_size > 0:
        log.info(f"[binary_merge] raw concat OK: {output_path.name} ({_merge_fmt_size(total_written)})")
    else:
        log.error(f"[binary_merge] output is empty or missing: {output_path}")