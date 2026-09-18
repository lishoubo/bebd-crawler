from pathlib import Path

import pytest

from bebd_crawler.brands import load_brands, normalize_brands


def test_normalize_brands_strips_and_deduplicates() -> None:
    assert normalize_brands([" 欧莱雅 ", "", "欧莱雅", "雅诗兰黛"]) == [
        "欧莱雅",
        "雅诗兰黛",
    ]


def test_load_simple_yaml(tmp_path: Path) -> None:
    path = tmp_path / "brands.yaml"
    path.write_text("brands:\n  - 欧莱雅\n  - 雅诗兰黛\n", encoding="utf-8")
    assert load_brands(path) == ["欧莱雅", "雅诗兰黛"]


def test_empty_brand_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="品牌清单为空"):
        normalize_brands([])

