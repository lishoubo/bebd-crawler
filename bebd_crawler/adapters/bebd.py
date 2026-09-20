"""通过页面交互触发请求，并拦截 BEBD 商品搜索响应。"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bebd_crawler.human_timing import HumanTiming

logger = logging.getLogger(__name__)

GOODS_SEARCH_API_PATH = "/auth/goods/goodsSearch"


class CaptchaRequiredError(RuntimeError):
    """BEBD 要求用户完成图片验证码。"""


@dataclass(frozen=True)
class SearchPage:
    current: int
    total: int
    limit_page: bool
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class CrawlStats:
    keyword: str
    total: int
    pages: int
    received: int
    written: int
    stop_reason: str


def parse_search_packet(packet: object) -> tuple[dict[str, Any], SearchPage]:
    request = packet.request.postData  # type: ignore[attr-defined]
    response = packet.response.body  # type: ignore[attr-defined]
    if not isinstance(request, dict):
        raise RuntimeError("goodsSearch 请求体不是 JSON 对象")
    if not isinstance(response, dict) or response.get("retCode") != 200:
        code = response.get("retCode") if isinstance(response, dict) else None
        message = response.get("msg") if isinstance(response, dict) else "响应不是 JSON"
        if code == 30019:
            raise CaptchaRequiredError(f"BEBD 要求图片验证码：{message}")
        raise RuntimeError(f"goodsSearch 接口失败：retCode={code} msg={message}")
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("goodsSearch 响应缺少 data")
    raw_rows = data.get("data")
    if not isinstance(raw_rows, list):
        raise RuntimeError("goodsSearch 响应缺少 data.data 数组")
    rows = [row for row in raw_rows if isinstance(row, dict)]
    return request, SearchPage(
        current=int(request.get("current") or 0),
        total=int(data.get("total") or 0),
        limit_page=bool(data.get("limitPage")),
        rows=rows,
    )


def is_exponent_desc(request: dict[str, Any]) -> bool:
    orders = request.get("orders")
    return bool(
        isinstance(orders, list)
        and orders
        and isinstance(orders[0], dict)
        and orders[0].get("column") == "exponent"
        and orders[0].get("asc") is False
    )


class BebdSearchCrawler:
    def __init__(
        self,
        page: object,
        *,
        timeout: float = 30.0,
        human_timing: HumanTiming | None = None,
    ) -> None:
        self.page = page
        self.timeout = timeout
        self.human_timing = human_timing or HumanTiming()

    def wait_until_search_ready(
        self,
        target_url: str,
        *,
        poll_seconds: float = 2.0,
        timeout: float | None = None,
    ) -> str:
        """登录态失效时保持浏览器开启，轮询等待用户手工登录。"""
        started = time.monotonic()
        last_navigation = 0.0
        last_notice = 0.0
        while True:
            search_input = self._first_displayed("@placeholder=查找化妆品")
            current_url = str(getattr(self.page, "url", "") or "")
            if search_input is not None and "#/home/dataCenter/search" in current_url:
                logger.info("BEBD 搜索页面已就绪")
                return current_url

            now = time.monotonic()
            if timeout is not None and now - started >= timeout:
                raise TimeoutError(f"等待 BEBD 登录超时（{timeout:g} 秒）")
            if now - last_notice >= 30 or last_notice == 0:
                logger.warning("当前不是 BEBD 搜索页面，请在浏览器中完成登录；程序正在等待……")
                last_notice = now

            lowered = current_url.lower()
            is_login_page = any(marker in lowered for marker in ("/login", "#/login", "signin"))
            if not is_login_page and now - last_navigation >= 3:
                self.page.get(target_url)  # type: ignore[attr-defined]
                last_navigation = time.monotonic()
            time.sleep(poll_seconds)

    def crawl(
        self,
        keyword: str,
        *,
        limit: int | None,
        on_page: Callable[[list[dict[str, Any]]], int],
        start_page: int = 1,
        already_written: int = 0,
        on_progress: Callable[[SearchPage, int], None] | None = None,
    ) -> CrawlStats:
        if limit is not None and limit < 1:
            raise ValueError("limit 必须大于 0")
        if start_page < 1:
            raise ValueError("start_page 必须大于 0")
        listener = self.page.listen  # type: ignore[attr-defined]
        listener.start(targets=GOODS_SEARCH_API_PATH, method="POST")
        try:
            initial_packet = self._submit_search(keyword)
            request, page_data = parse_search_packet(initial_packet)
            if not is_exponent_desc(request):
                request, page_data = self._select_exponent_desc()
            if start_page > 1:
                request, page_data = self._jump_to_page(start_page)

            total = page_data.total
            pages = 0
            received = 0
            written = already_written
            stop_reason = "unknown"
            while True:
                if not is_exponent_desc(request):
                    raise RuntimeError("分页请求不再是美修指数降序，已停止以避免错误数据")
                remaining = len(page_data.rows) if limit is None else limit - written
                enriched = self._enrich_rows(
                    page_data.rows[:remaining], keyword=keyword, current=page_data.current
                )
                received += len(page_data.rows)
                written += on_page(enriched)
                pages += 1
                if on_progress is not None:
                    on_progress(page_data, written)
                logger.info(
                    "采集 %s: 第 %d 页，接口返回 %d 条，累计写入 %d/%d",
                    keyword,
                    page_data.current,
                    len(page_data.rows),
                    written,
                    total if limit is None else min(limit, total),
                )
                if limit is not None and written >= limit:
                    stop_reason = "requested_limit"
                    break
                if written >= total:
                    stop_reason = "api_total"
                    break
                if not page_data.rows:
                    stop_reason = "empty_page"
                    break

                next_button = self._next_button()
                if next_button is None:
                    stop_reason = "next_page_unavailable"
                    logger.warning(
                        "采集 %s: 下一页不可用；limitPage=%s，累计写入 %d，接口总数 %d",
                        keyword,
                        page_data.limit_page,
                        written,
                        total,
                    )
                    break
                previous_page = page_data.current
                delay = self.human_timing.before_next_page(self.page, next_button)
                logger.debug("翻页前人类化等待 %.2f 秒", delay)
                next_button.click()
                packet = listener.wait(timeout=self.timeout)
                if not packet:
                    raise RuntimeError("点击下一页后未捕获 goodsSearch 请求")
                request, page_data = parse_search_packet(packet)
                if page_data.current <= previous_page:
                    raise RuntimeError(
                        f"分页没有前进：上一页={previous_page} 当前页={page_data.current}"
                    )
                delay = self.human_timing.after_page_loaded()
                logger.debug("翻页后人类化等待 %.2f 秒", delay)

            return CrawlStats(
                keyword=keyword,
                total=total,
                pages=pages,
                received=received,
                written=written,
                stop_reason=stop_reason,
            )
        finally:
            listener.stop()

    def _submit_search(self, keyword: str) -> object:
        search_input = self._wait_for_displayed("@placeholder=查找化妆品", timeout=10)
        search_button = (
            self._first_displayed_from(search_input.parent(), "css:button.search-btn")
            if search_input is not None
            else None
        )
        if not search_input or search_button is None:
            raise RuntimeError("没有找到商品搜索输入框或搜索按钮，页面结构可能已变化")
        search_input.click()
        search_input.input(keyword, clear=True)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if str(search_input.attr("value") or "").strip() == keyword:
                break
            time.sleep(0.1)
        else:
            actual = str(search_input.attr("value") or "")
            raise RuntimeError(f"搜索词没有写入可见输入框：期望={keyword!r} 实际={actual!r}")
        time.sleep(0.5)
        search_button.click()
        packet = self.page.listen.wait(timeout=self.timeout)  # type: ignore[attr-defined]
        if not packet:
            raise RuntimeError("搜索后未捕获 goodsSearch 请求")
        self._brief_wait()
        return packet

    def _select_exponent_desc(self) -> tuple[dict[str, Any], SearchPage]:
        sort_item = self._wait_for_sort_item()
        for _ in range(2):
            sort_item.click()
            packet = self.page.listen.wait(timeout=self.timeout)  # type: ignore[attr-defined]
            if not packet:
                raise RuntimeError("点击美修指数后未捕获 goodsSearch 请求")
            request, page_data = parse_search_packet(packet)
            if is_exponent_desc(request):
                self._brief_wait()
                return request, page_data
        raise RuntimeError("两次点击后仍未切换到美修指数降序")

    def _jump_to_page(self, target_page: int) -> tuple[dict[str, Any], SearchPage]:
        jumper = self._wait_for_displayed(
            "css:.ant-pagination-options-quick-jumper input",
            timeout=10,
        )
        if jumper is None:
            raise RuntimeError("没有找到分页跳转输入框，无法从断点继续")
        self.human_timing.between_actions(0.8, 1.8)
        jumper.click()
        jumper.input(str(target_page), clear=True)
        self.human_timing.between_actions(0.35, 0.8)
        blur_target = self._wait_for_displayed("@placeholder=查找化妆品", timeout=5)
        if blur_target is None:
            raise RuntimeError("输入跳转页码后没有找到可点击的页面区域")
        blur_target.click()
        logger.debug("已点击顶部搜索框，使分页跳转输入框失去焦点")
        packet = self.page.listen.wait(timeout=self.timeout)  # type: ignore[attr-defined]
        if not packet:
            raise RuntimeError(f"跳转到第 {target_page} 页后未捕获 goodsSearch 请求")
        request, page_data = parse_search_packet(packet)
        if not is_exponent_desc(request):
            raise RuntimeError("跳页请求不再是美修指数降序")
        if page_data.current != target_page:
            raise RuntimeError(f"跳页失败：目标={target_page} 实际={page_data.current}")
        self.human_timing.after_page_loaded()
        logger.info("已从分页跳转控件定位到第 %d 页", target_page)
        return request, page_data

    def _wait_for_sort_item(self) -> object:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            for item in self.page.eles("css:.orders-item"):  # type: ignore[attr-defined]
                if item.states.is_displayed and item.text.strip().startswith("美修指数"):
                    return item
            time.sleep(0.25)
        raise RuntimeError("搜索结果中没有找到‘美修指数’排序项")

    def _next_button(self) -> object | None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            button = self._first_displayed("css:.ant-pagination-next")
            if button is not None:
                classes = str(button.attr("class") or "")
                return None if "ant-pagination-disabled" in classes else button
            time.sleep(0.2)
        return None

    def _first_displayed(self, locator: str) -> object | None:
        for item in self.page.eles(locator):  # type: ignore[attr-defined]
            if item.states.is_displayed:
                return item
        return None

    def _wait_for_displayed(self, locator: str, *, timeout: float) -> object | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self._first_displayed(locator)
            if item is not None:
                return item
            time.sleep(0.2)
        return None

    @staticmethod
    def _first_displayed_from(parent: object, locator: str) -> object | None:
        for item in parent.eles(locator):  # type: ignore[attr-defined]
            if item.states.is_displayed:
                return item
        return None

    @staticmethod
    def _enrich_rows(
        rows: list[dict[str, Any]], *, keyword: str, current: int
    ) -> list[dict[str, Any]]:
        captured_at = datetime.now(UTC).isoformat()
        return [
            {
                **row,
                "_searchKeyword": keyword,
                "_page": current,
                "_capturedAt": captured_at,
            }
            for row in rows
        ]

    @staticmethod
    def _brief_wait() -> None:
        time.sleep(0.5)
