"""
Structured file logger — lightweight, no external dependencies.

Writes newline-delimited JSON to logs/aeronav.log.
Rotates at ~5 MB to avoid filling the Pi SD card.
Used throughout the navigation stack for field diagnostics.

Usage:
    from utils.logger import log
    log.info("Mission loaded", points=42, segments=3)
    log.warning("GPS outlier rejected", dist_m=47.2)
    log.error("UART read failed", exc="TimeoutError")
"""

import json
import os
import time
import traceback
from pathlib import Path


LOG_DIR  = Path(__file__).parent.parent / 'logs'
LOG_FILE = LOG_DIR / 'aeronav.log'
MAX_BYTES = 5 * 1024 * 1024   # 5 MB


class StructuredLogger:
    def __init__(self):
        LOG_DIR.mkdir(exist_ok=True)
        self._path = LOG_FILE

    def _write(self, level: str, msg: str, **kwargs):
        record = {
            't':   round(time.time(), 3),
            'lvl': level,
            'msg': msg,
            **kwargs,
        }
        line = json.dumps(record, default=str) + '\n'
        try:
            # Rotate if too large
            if self._path.exists() and self._path.stat().st_size > MAX_BYTES:
                rotated = LOG_DIR / 'aeronav.1.log'
                self._path.rename(rotated)
            with open(self._path, 'a') as f:
                f.write(line)
        except Exception:
            pass   # Never let logging crash the navigation loop

        # Also print a concise log line to stdout for terminal visibility
        try:
            print(f"[{level}] {time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}")
        except Exception:
            pass

    def debug(self, msg: str, **kw):   self._write('DEBUG',   msg, **kw)
    def info(self,  msg: str, **kw):   self._write('INFO',    msg, **kw)
    def warning(self, msg: str, **kw): self._write('WARNING', msg, **kw)
    def error(self, msg: str, **kw):   self._write('ERROR',   msg, **kw)

    def exception(self, msg: str, exc: Exception, **kw):
        self._write('ERROR', msg, exc=str(exc),
                    tb=traceback.format_exc().strip().splitlines()[-1], **kw)


log = StructuredLogger()
