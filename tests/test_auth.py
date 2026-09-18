from __future__ import annotations

import json
import logging
import stat
from pathlib import Path

import pytest

from bebd_crawler.auth import (
    check_session_url,
    export_all_cookies,
    inject_cookies,
    load_cookie_file,
    refresh_cookie_file,
    save_cookie_file,
)


class FakePage:
    def __init__(self, cookies: list[dict] | None = None) -> None:
        self.cookies = cookies or []
        self.calls: list[tuple[str, dict]] = []

    def run_cdp(self, method: str, **params: object) -> dict:
        self.calls.append((method, params))
        if method == "Network.getAllCookies":
            return {"cookies": self.cookies}
        if method == "Network.setCookie":
            return {"success": True}
        raise AssertionError(method)


def sample_cookie(secret: str = "top-secret-value") -> dict:
    return {
        "name": "session",
        "value": secret,
        "domain": ".bevol.com",
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "sameSite": "None",
        "expires": 2_000_000_000,
        "partitionKey": {
            "topLevelSite": "https://bevol.com",
            "hasCrossSiteAncestor": False,
        },
    }


def test_cookie_round_trip_preserves_attributes_and_permissions(tmp_path: Path) -> None:
    path = tmp_path / "auth" / "cookies.json"
    cookie = sample_cookie()

    save_cookie_file(path, [cookie], source_url="https://bebd.bevol.com/main.html#/home")

    assert load_cookie_file(path) == [cookie]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1


def test_export_and_inject_preserve_full_cookie_attributes() -> None:
    cookie = sample_cookie()
    source = FakePage([cookie])
    exported = export_all_cookies(source)

    target = FakePage()
    assert inject_cookies(target, exported) == 1
    assert target.calls == [("Network.setCookie", cookie)]


def test_cookie_values_are_not_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    secret = "never-log-this-secret"
    caplog.set_level(logging.INFO)

    save_cookie_file(tmp_path / "cookies.json", [sample_cookie(secret)], source_url="https://x")
    export_all_cookies(FakePage([sample_cookie(secret)]))

    assert secret not in caplog.text


def test_refresh_cookie_file_replaces_existing_cookie(tmp_path: Path) -> None:
    path = tmp_path / "cookies.json"
    save_cookie_file(path, [sample_cookie("old-value")], source_url="https://old")

    count = refresh_cookie_file(
        FakePage([sample_cookie("new-value")]), path, source_url="https://bebd.bevol.com/main.html"
    )

    assert count == 1
    assert load_cookie_file(path)[0]["value"] == "new-value"


@pytest.mark.parametrize(
    ("current_url", "expected"),
    [
        ("https://bebd.bevol.com/main.html#/home/dataCenter/search", True),
        ("https://bebd.bevol.com/login", False),
        ("https://account.example.com/sso", False),
        ("", False),
    ],
)
def test_check_session_url(current_url: str, expected: bool) -> None:
    result = check_session_url(
        current_url, "https://bebd.bevol.com/main.html#/home/dataCenter/search"
    )
    assert result.ok is expected
