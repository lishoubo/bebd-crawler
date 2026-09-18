"""JSONL 结果和运行摘要输出。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def create_run_dir(output_root: Path) -> Path:
    run_id = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    run_dir = output_root / run_id
    suffix = 1
    while run_dir.exists():
        run_dir = output_root / f"{run_id}-{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True)
    return run_dir


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", value.strip()).strip("._")
    return cleaned or "unnamed"


class JsonlSink:
    """每页追加并刷盘，按商品 mid 去重。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()
        self.written = 0

    def append(self, rows: list[dict[str, Any]]) -> int:
        added = 0
        with self.path.open("a", encoding="utf-8") as stream:
            for row in rows:
                key = str(row.get("mid") or "")
                if not key:
                    key = json.dumps(row, ensure_ascii=False, sort_keys=True)
                if key in self._seen:
                    continue
                self._seen.add(key)
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                stream.write("\n")
                self.written += 1
                added += 1
            stream.flush()
            os.fsync(stream.fileno())
        return added


@dataclass
class BrandSummary:
    keyword: str
    status: str
    requested_limit: int
    api_total: int = 0
    pages: int = 0
    received: int = 0
    written: int = 0
    output_file: str = ""
    error: str = ""


def write_summary(
    run_dir: Path,
    summaries: list[BrandSummary],
    *,
    started_at: datetime,
    status: str,
) -> Path:
    payload = {
        "status": status,
        "started_at": started_at.astimezone(UTC).isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "brands": [asdict(item) for item in summaries],
    }
    path = run_dir / "summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path

