from pathlib import Path

from bebd_crawler.adapters.product_detail import (
    MID_PATTERN,
    detail_mid_from_url,
    endpoint_path,
    response_requires_captcha,
    unique_responses,
    write_ingredient_files,
    write_json_atomic,
)


def test_unique_responses_deduplicates_canonical_json() -> None:
    first = {"retCode": 200, "data": {"total": 1, "items": [{"mid": "a"}]}}
    same_different_order = {"data": {"items": [{"mid": "a"}], "total": 1}, "retCode": 200}
    different = {"retCode": 200, "data": {"total": 2}}

    assert unique_responses([first, same_different_order, different]) == [first, different]


def test_endpoint_path_does_not_confuse_ingredient_resemble() -> None:
    assert endpoint_path("https://saas2-api.bevol.com/auth/goods/detail/ingredient") == (
        "/auth/goods/detail/ingredient"
    )
    assert endpoint_path("https://saas2-api.bevol.com/auth/goods/detail/ingredientResemble") != (
        "/auth/goods/detail/ingredient"
    )


def test_detail_mid_from_url_requires_product_route() -> None:
    mid = "c140d305e626346df8a8b8c6640d3fb4"
    assert detail_mid_from_url(
        f"https://bebd.bevol.com/main.html#/home/productDetails?mid={mid}"
    ) == mid
    assert detail_mid_from_url(
        "https://bebd.bevol.com/main.html#/home/firstPage/index"
    ) is None
    assert detail_mid_from_url(
        "https://bebd.bevol.com/main.html#/login?redirect=%2Fhome%2FfirstPage%2Findex"
    ) is None


def test_response_requires_captcha_only_matches_known_code() -> None:
    assert response_requires_captcha({"retCode": 30019, "msg": "弹出图片验证码的框"})
    assert not response_requires_captcha({"retCode": 200})
    assert not response_requires_captcha("not-json")


def test_write_json_atomic(tmp_path: Path) -> None:
    path = tmp_path / "欧莱雅" / "mid" / "data.json"
    write_json_atomic(path, {"name": "测试 产品"})
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert "测试 产品" in path.read_text(encoding="utf-8")


def test_mid_format_only_accepts_32_hex_characters() -> None:
    assert MID_PATTERN.fullmatch("c140d305e626346df8a8b8c6640d3fb4")
    assert MID_PATTERN.fullmatch("../outside") is None


def test_write_ingredient_files_removes_stale_numbered_files(tmp_path: Path) -> None:
    (tmp_path / "ingredient-2.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ingredient-note.json").write_text("keep", encoding="utf-8")

    written = write_ingredient_files(tmp_path, [{"retCode": 200, "data": [1]}])

    assert [path.name for path in written] == ["ingredient.json"]
    assert not (tmp_path / "ingredient-2.json").exists()
    assert (tmp_path / "ingredient-note.json").exists()
