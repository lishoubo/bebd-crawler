from __future__ import annotations

from bebd_crawler.human_timing import HumanTiming


class FixedRandom:
    def randint(self, start: int, end: int) -> int:
        assert (start, end) in {(180, 520), (220, 620)}
        return 320

    def uniform(self, start: float, end: float) -> float:
        return (start + end) / 2


class Scroll:
    def __init__(self) -> None:
        self.down_values: list[int] = []
        self.to_see_values: list[bool] = []

    def down(self, value: int) -> None:
        self.down_values.append(value)

    def to_see(self, *, center: bool) -> None:
        self.to_see_values.append(center)


class Page:
    def __init__(self) -> None:
        self.scroll = Scroll()


class Button:
    def __init__(self) -> None:
        self.scroll = Scroll()
        self.hovered = False

    def hover(self) -> None:
        self.hovered = True


def test_before_next_page_scrolls_hovers_and_waits() -> None:
    sleeps: list[float] = []
    timing = HumanTiming(random_source=FixedRandom(), sleeper=sleeps.append)  # type: ignore[arg-type]
    page = Page()
    button = Button()

    delay = timing.before_next_page(page, button)

    assert page.scroll.down_values == [320]
    assert button.scroll.to_see_values == [True]
    assert button.hovered is True
    assert sleeps == [0.6, 1.3]
    assert delay == 1.3


def test_after_page_loaded_uses_random_delay() -> None:
    sleeps: list[float] = []
    timing = HumanTiming(random_source=FixedRandom(), sleeper=sleeps.append)  # type: ignore[arg-type]
    assert timing.after_page_loaded() == 1.0
    assert sleeps == [1.0]


def test_browse_product_detail_scrolls_and_waits() -> None:
    sleeps: list[float] = []
    timing = HumanTiming(random_source=FixedRandom(), sleeper=sleeps.append)  # type: ignore[arg-type]
    page = Page()

    delay = timing.browse_product_detail(page)

    assert page.scroll.down_values == [320]
    assert delay == 1.3
    assert sleeps == [1.3]
