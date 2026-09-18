"""品牌/搜索词清单加载。"""
from __future__ import annotations

import json
from pathlib import Path


def load_brands(path: Path) -> list[str]:
    """读取 JSON、简单 YAML 或逐行文本品牌清单。"""
    if not path.exists():
        raise FileNotFoundError(f"品牌清单不存在：{path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        values = payload.get("brands") if isinstance(payload, dict) else payload
        if not isinstance(values, list):
            raise ValueError("JSON 品牌清单必须是数组，或包含 brands 数组")
        return normalize_brands(str(item) for item in values)

    values: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line == "brands:":
            continue
        values.append(line[1:].strip() if line.startswith("-") else line)
    return normalize_brands(values)


def normalize_brands(values: object) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:  # type: ignore[union-attr]
        brand = str(value).strip()
        if brand and brand not in seen:
            seen.add(brand)
            result.append(brand)
    if not result:
        raise ValueError("品牌清单为空")
    return result

