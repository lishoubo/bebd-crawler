from __future__ import annotations

from dataclasses import dataclass

import pytest

from bebd_crawler.adapters.bebd import (
    BebdSearchCrawler,
    CaptchaRequiredError,
    is_exponent_desc,
    parse_search_packet,
)


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


def test_parse_search_packet_identifies_image_captcha() -> None:
    packet = Packet(
        Value({"current": 94}),
        Value(body={"retCode": 30019, "msg": "弹出图片验证码的框"}),
    )
    with pytest.raises(CaptchaRequiredError, match="图片验证码"):
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


class ClickableElement:
    states = VisibleState()

    def __init__(self) -> None:
        self.clicked = False
        self.inputs: list[tuple[str, bool]] = []

    def click(self) -> None:
        self.clicked = True

    def input(self, value: str, *, clear: bool = False) -> None:
        self.inputs.append((value, clear))


class Listener:
    def __init__(self, packet: Packet) -> None:
        self.packet = packet

    def wait(self, *, timeout: float) -> Packet:
        assert timeout == 30.0
        return self.packet


class JumpPage:
    def __init__(self) -> None:
        self.jumper = ClickableElement()
        self.blur_target = ClickableElement()
        request = {
            "current": 88,
            "orders": [{"asc": False, "column": "exponent"}],
        }
        response = {"retCode": 200, "data": {"data": [], "total": 5533}}
        self.listen = Listener(Packet(Value(request), Value(body=response)))

    def eles(self, locator: str) -> list[ClickableElement]:
        if locator == "css:.ant-pagination-options-quick-jumper input":
            return [self.jumper]
        if locator == "@placeholder=查找化妆品":
            return [self.blur_target]
        return []


class NoWaitTiming:
    def between_actions(self, minimum: float, maximum: float) -> float:
        return (minimum + maximum) / 2

    def after_page_loaded(self) -> float:
        return 0


def test_jump_to_page_clicks_elsewhere_to_trigger_blur() -> None:
    page = JumpPage()
    crawler = BebdSearchCrawler(page, human_timing=NoWaitTiming())  # type: ignore[arg-type]

    _, page_data = crawler._jump_to_page(88)

    assert page.jumper.clicked is True
    assert page.jumper.inputs == [("88", True)]
    assert page.blur_target.clicked is True
    assert page_data.current == 88
