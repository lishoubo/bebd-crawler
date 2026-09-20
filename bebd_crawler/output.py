"""JSONL 结果和运行摘要输出。"""
from __future__ import annotations

import json
import os
import re
import tempfile
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


class JsonArraySink:
    """按商品 mid 去重，并在每页后原子更新一个 JSON 数组文件。"""

    def __init__(self, path: Path, *, resume: bool = False) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()
        self._rows: list[dict[str, Any]] = []
        self.written = 0
        if resume and self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
                raise ValueError(f"已有搜索结果不是 JSON 对象数组：{self.path}")
            for row in payload:
                key = self._key(row)
                if key not in self._seen:
                    self._seen.add(key)
                    self._rows.append(row)
            self.written = len(self._rows)
        else:
            self._flush()

    @property
    def max_page(self) -> int:
        pages = [int(row.get("_page") or 0) for row in self._rows]
        return max(pages, default=0)

    def append(self, rows: list[dict[str, Any]]) -> int:
        added = 0
        for row in rows:
            key = self._key(row)
            if key in self._seen:
                continue
            self._seen.add(key)
            self._rows.append(row)
            self.written += 1
            added += 1
        if added:
            self._flush()
        return added

    @staticmethod
    def _key(row: dict[str, Any]) -> str:
        key = str(row.get("mid") or "")
        return key or json.dumps(row, ensure_ascii=False, sort_keys=True)

    def _flush(self) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self._rows, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise


class MidLineSink:
    """将唯一商品 MID 以纯文本形式逐行追加。"""

    def __init__(self, path: Path, *, resume: bool = True) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()
        if resume and self.path.exists():
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                mid = line.strip()
                if not re.fullmatch(r"[0-9a-fA-F]{32}", mid):
                    raise ValueError(f"MID 文件第 {line_number} 行格式不正确：{self.path}")
                self._seen.add(mid)
        elif not resume:
            self.path.write_text("", encoding="utf-8")
        self.written = len(self._seen)

    def append(self, rows: list[dict[str, Any]]) -> int:
        new_mids: list[str] = []
        for row in rows:
            mid = str(row.get("mid") or "").strip()
            if not re.fullmatch(r"[0-9a-fA-F]{32}", mid):
                raise ValueError("搜索结果缺少合法的 32 位 MID")
            if mid not in self._seen:
                self._seen.add(mid)
                new_mids.append(mid)
        if not new_mids:
            return 0
        with self.path.open("a", encoding="utf-8") as stream:
            for mid in new_mids:
                stream.write(f"{mid}\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.written += len(new_mids)
        return len(new_mids)


@dataclass(frozen=True)
class SearchProgress:
    brand: str
    completed_page: int
    captured: int
    next_page: int
    api_total: int
    status: str


def write_search_state(path: Path, progress: SearchProgress) -> None:
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    content = (
        f"# {progress.brand} 搜索采集进度\n\n"
        f"- 状态：{progress.status}\n"
        f"- 排序：美修指数从高到低\n"
        f"- 已完成页：{progress.completed_page}\n"
        f"- 已保存商品数：{progress.captured}\n"
        f"- 下一次开始页：{progress.next_page}\n"
        f"- 接口报告总数：{progress.api_total}\n"
        f"- 更新时间：{updated_at}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def read_search_state(path: Path) -> SearchProgress | None:
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")

    def value(label: str) -> str:
        match = re.search(rf"^- {re.escape(label)}：(.+)$", content, re.MULTILINE)
        if match is None:
            raise ValueError(f"进度文件缺少字段：{label}")
        return match.group(1).strip()

    title = re.search(r"^# (.+) 搜索采集进度$", content, re.MULTILINE)
    if title is None:
        raise ValueError("进度文件标题格式不正确")
    return SearchProgress(
        brand=title.group(1).strip(),
        completed_page=int(value("已完成页")),
        captured=int(value("已保存商品数")),
        next_page=int(value("下一次开始页")),
        api_total=int(value("接口报告总数")),
        status=value("状态"),
    )


@dataclass(frozen=True)
class DetailProgress:
    brand: str
    status: str
    total_mids: int
    completed_details: int
    next_line: int
    next_mid: str
    last_completed_mid: str


def write_detail_state(path: Path, progress: DetailProgress) -> None:
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    content = (
        f"# {progress.brand} 产品详情采集进度\n\n"
        f"- 状态：{progress.status}\n"
        f"- MID总数：{progress.total_mids}\n"
        f"- 已完成详情数：{progress.completed_details}\n"
        f"- 下一次开始行：{progress.next_line}\n"
        f"- 下一MID：{progress.next_mid or '-'}\n"
        f"- 最近完成MID：{progress.last_completed_mid or '-'}\n"
        f"- 更新时间：{updated_at}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def read_detail_state(path: Path) -> DetailProgress | None:
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")

    def value(label: str) -> str:
        match = re.search(rf"^- {re.escape(label)}：(.+)$", content, re.MULTILINE)
        if match is None:
            raise ValueError(f"详情进度文件缺少字段：{label}")
        return match.group(1).strip()

    title = re.search(r"^# (.+) 产品详情采集进度$", content, re.MULTILINE)
    if title is None:
        raise ValueError("详情进度文件标题格式不正确")
    next_mid = value("下一MID")
    last_mid = value("最近完成MID")
    return DetailProgress(
        brand=title.group(1).strip(),
        status=value("状态"),
        total_mids=int(value("MID总数")),
        completed_details=int(value("已完成详情数")),
        next_line=int(value("下一次开始行")),
        next_mid="" if next_mid == "-" else next_mid,
        last_completed_mid="" if last_mid == "-" else last_mid,
    )


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
