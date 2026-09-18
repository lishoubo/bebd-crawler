from __future__ import annotations

from dataclasses import dataclass

import pytest

from bebd_crawler.adapters.bebd import BebdSearchCrawler, is_exponent_desc, parse_search_packet


@dataclass
class Value:
    postData: object | None = None
    body: object | None = None


@dataclass
class Packet:
    request: Value
    response: Value


def test_parse_search_packet_and_sort() -> None:
    request = {
        "keyword": "欧莱雅",
        "current": 1,
        "orders": [{"asc": False, "column": "exponent"}],
    }
    response = {
        "retCode": 200,
        "data": {"data": [{"mid": "a", "exponent": 88.93}], "total": 5533, "limitPage": True},
    }

    parsed_request, page = parse_search_packet(Packet(Value(request), Value(body=response)))

    assert is_exponent_desc(parsed_request)
    assert page.current == 1
    assert page.total == 5533
    assert page.limit_page is True
    assert page.rows[0]["mid"] == "a"


def test_parse_search_packet_rejects_api_error() -> None:
    packet = Packet(Value({"current": 1}), Value(body={"retCode": 401, "msg": "expired"}))
    with pytest.raises(RuntimeError, match="retCode=401"):
        parse_search_packet(packet)


class VisibleState:
    is_displayed = True


class VisibleInput:
    states = VisibleState()


class LoginThenSearchPage:
    def __init__(self) -> None:
        self.polls = 0

    @property
    def url(self) -> str:
        if self.polls < 3:
            return "https://bebd.bevol.com/login"
        return "https://bebd.bevol.com/main.html#/home/dataCenter/search"

    def eles(self, locator: str) -> list[VisibleInput]:
        assert locator == "@placeholder=查找化妆品"
        self.polls += 1
        return [VisibleInput()] if self.polls >= 3 else []


def test_wait_until_search_ready_polls_login_page() -> None:
    page = LoginThenSearchPage()
    crawler = BebdSearchCrawler(page)

    url = crawler.wait_until_search_ready("https://bebd.bevol.com/target", poll_seconds=0)

    assert url.endswith("#/home/dataCenter/search")
    assert page.polls == 3
