import json
import stat
from pathlib import Path

from bebd_crawler.output import (
    DetailProgress,
    JsonArraySink,
    JsonlSink,
    MidLineSink,
    SearchProgress,
    read_detail_state,
    read_search_state,
    safe_filename,
    write_detail_state,
    write_search_state,
)


def test_jsonl_sink_deduplicates_by_mid_and_appends(tmp_path: Path) -> None:
    path = tmp_path / "欧莱雅.jsonl"
    sink = JsonlSink(path)

    assert sink.append([{"mid": "a", "name": "A"}, {"mid": "b", "name": "B"}]) == 2
    assert sink.append([{"mid": "a", "name": "A2"}, {"mid": "c", "name": "C"}]) == 1

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["mid"] for row in rows] == ["a", "b", "c"]
    assert stat.S_IMODE(path.stat().st_mode) & 0o222


def test_json_array_sink_deduplicates_and_replaces_previous_run(tmp_path: Path) -> None:
    path = tmp_path / "欧莱雅" / "search-result-all.json"
    path.parent.mkdir(parents=True)
    path.write_text('[{"mid":"old"}]\n', encoding="utf-8")
    sink = JsonArraySink(path)

    assert json.loads(path.read_text(encoding="utf-8")) == []
    assert sink.append([{"mid": "a"}, {"mid": "b"}]) == 2
    assert sink.append([{"mid": "a"}, {"mid": "c"}]) == 1
    assert [row["mid"] for row in json.loads(path.read_text(encoding="utf-8"))] == [
        "a",
        "b",
        "c",
    ]
    assert list(path.parent.glob(".search-result-all.json.*")) == []


def test_json_array_sink_resumes_existing_results(tmp_path: Path) -> None:
    path = tmp_path / "search-result-all.json"
    path.write_text(
        json.dumps([{"mid": "a", "_page": 1}, {"mid": "b", "_page": 2}]),
        encoding="utf-8",
    )

    sink = JsonArraySink(path, resume=True)

    assert sink.written == 2
    assert sink.max_page == 2
    assert sink.append([{"mid": "b", "_page": 3}, {"mid": "c", "_page": 3}]) == 1
    assert [row["mid"] for row in json.loads(path.read_text(encoding="utf-8"))] == [
        "a",
        "b",
        "c",
    ]


def test_search_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.md"
    progress = SearchProgress(
        brand="欧莱雅",
        completed_page=83,
        captured=830,
        next_page=84,
        api_total=5533,
        status="已中断，可继续",
    )

    write_search_state(path, progress)

    assert read_search_state(path) == progress
    assert "- 下一次开始页：84" in path.read_text(encoding="utf-8")


def test_mid_line_sink_resumes_and_appends_unique_mids(tmp_path: Path) -> None:
    path = tmp_path / "search-result-mids-all.json"
    first = "c140d305e626346df8a8b8c6640d3fb4"
    second = "281cebbc6c377fe3417f43beccea1e91"
    path.write_text(f"{first}\n", encoding="utf-8")
    sink = MidLineSink(path)

    added = sink.append([{"mid": first}, {"mid": second}])

    assert added == 1
    assert sink.written == 2
    assert path.read_text(encoding="utf-8").splitlines() == [first, second]


def test_detail_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "detail-state.md"
    progress = DetailProgress(
        brand="欧莱雅",
        status="本次已完成 10 个，可继续",
        total_mids=930,
        completed_details=11,
        next_line=12,
        next_mid="0123456789abcdef0123456789abcdef",
        last_completed_mid="fedcba9876543210fedcba9876543210",
    )

    write_detail_state(path, progress)

    assert read_detail_state(path) == progress


def test_safe_filename_removes_path_characters() -> None:
    assert safe_filename(" 欧/莱:雅 ") == "欧_莱_雅"
