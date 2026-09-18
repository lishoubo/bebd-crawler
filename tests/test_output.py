import json
import stat
from pathlib import Path

from bebd_crawler.output import JsonlSink, safe_filename


def test_jsonl_sink_deduplicates_by_mid_and_appends(tmp_path: Path) -> None:
    path = tmp_path / "欧莱雅.jsonl"
    sink = JsonlSink(path)

    assert sink.append([{"mid": "a", "name": "A"}, {"mid": "b", "name": "B"}]) == 2
    assert sink.append([{"mid": "a", "name": "A2"}, {"mid": "c", "name": "C"}]) == 1

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["mid"] for row in rows] == ["a", "b", "c"]
    assert stat.S_IMODE(path.stat().st_mode) & 0o222


def test_safe_filename_removes_path_characters() -> None:
    assert safe_filename(" 欧/莱:雅 ") == "欧_莱_雅"

