"""本地运行配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGET_URL = "https://bebd.bevol.com/main.html#/home/dataCenter/search"
DEFAULT_COOKIE_PATH = PROJECT_ROOT / ".local" / "auth" / "bebd-cookies.json"


@dataclass(frozen=True)
class Settings:
    target_url: str
    cookie_path: Path

    @classmethod
    def from_env(cls) -> Settings:
        target_url = os.getenv("BEBD_TARGET_URL", DEFAULT_TARGET_URL).strip()
        cookie_value = os.getenv("BEBD_COOKIE_PATH", "").strip()
        cookie_path = Path(cookie_value).expanduser() if cookie_value else DEFAULT_COOKIE_PATH
        return cls(target_url=target_url, cookie_path=cookie_path)

