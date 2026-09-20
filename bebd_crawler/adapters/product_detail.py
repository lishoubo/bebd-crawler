"""通过打开产品详情页，拦截详情与成分接口响应。"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from bebd_crawler.human_timing import HumanTiming
from bebd_crawler.output import safe_filename

logger = logging.getLogger(__name__)

DATA_ENDPOINT = "/auth/goods/detail/data"
INGREDIENT_ENDPOINT = "/auth/goods/detail/ingredient"
DETAIL_URL_TEMPLATE = "https://bebd.bevol.com/main.html#/home/productDetails?mid={mid}"
MID_PATTERN = re.compile(r"[0-9a-fA-F]{32}")


@dataclass(frozen=True)
class ProductDetailResult:
    mid: str
    brand_name: str
    product_name: str
    output_dir: Path
    ingredient_responses: int
    ingredient_files: int


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def unique_responses(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        key = canonical_json(value)
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


def endpoint_path(url: str) -> str:
    return urlparse(url).path


def detail_mid_from_url(url: str) -> str | None:
    fragment = urlparse(url).fragment
    route, _, query = fragment.partition("?")
    if route.rstrip("/") != "/home/productDetails":
        return None
    values = parse_qs(query).get("mid")
    return values[0] if values else None


def response_requires_captcha(response: object) -> bool:
    return isinstance(response, dict) and response.get("retCode") == 30019


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def write_ingredient_files(
    result_dir: Path,
    responses: list[dict[str, Any]],
) -> list[Path]:
    written: list[Path] = []
    for index, response in enumerate(responses, start=1):
        filename = "ingredient.json" if index == 1 else f"ingredient-{index}.json"
        path = result_dir / filename
        write_json_atomic(path, response)
        written.append(path)

    expected = {path.name for path in written}
    for path in result_dir.glob("ingredient-*.json"):
        if re.fullmatch(r"ingredient-\d+\.json", path.name) and path.name not in expected:
            path.unlink()
    return written


class ProductDetailCrawler:
    def __init__(
        self,
        page: object,
        *,
        idle_timeout: float = 4.0,
        human_timing: HumanTiming | None = None,
    ) -> None:
        self.page = page
        self.idle_timeout = idle_timeout
        self.human_timing = human_timing or HumanTiming()

    def crawl(
        self,
        mid: str,
        *,
        output_root: Path,
        brand_directory: str | None = None,
        wait_timeout: float | None = None,
        on_captcha: Callable[[str], None] | None = None,
        on_captcha_resolved: Callable[[str], None] | None = None,
    ) -> ProductDetailResult:
        normalized_mid = mid.strip()
        if not MID_PATTERN.fullmatch(normalized_mid):
            raise ValueError("mid 必须是 32 位十六进制字符串")

        listener = self.page.listen  # type: ignore[attr-defined]
        listener.start(targets=[DATA_ENDPOINT, INGREDIENT_ENDPOINT], method="POST")
        try:
            target_url = DETAIL_URL_TEMPLATE.format(mid=normalized_mid)
            self.page.get(target_url)  # type: ignore[attr-defined]
            self._wait_until_detail_ready(normalized_mid, timeout=wait_timeout)
            try:
                self.page.wait.doc_loaded(timeout=20)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.debug("等待详情页加载完成超时，将继续监听接口：%s", exc)
            time.sleep(1)
            self.human_timing.browse_product_detail(self.page)

            data_responses: list[dict[str, Any]] = []
            ingredient_responses: list[dict[str, Any]] = []
            last_activity = time.monotonic()
            captcha_waiting = False
            while True:
                try:
                    packet = listener.wait(timeout=1)
                except UnboundLocalError as exc:
                    if not captcha_waiting:
                        raise
                    logger.warning(
                        "验证码等待期间监听器出现临时错误，继续保持浏览器等待：%s",
                        exc,
                    )
                    time.sleep(1)
                    continue
                if not packet:
                    if captcha_waiting:
                        continue
                    if time.monotonic() - last_activity >= self.idle_timeout:
                        if data_responses and ingredient_responses:
                            break
                        delay = self.human_timing.between_actions(3.0, 6.0)
                        logger.warning(
                            "目标详情页未收齐接口响应，等待 %.2f 秒后刷新重试："
                            "mid=%s data=%d ingredient=%d",
                            delay,
                            normalized_mid,
                            len(data_responses),
                            len(ingredient_responses),
                        )
                        self.page.refresh()  # type: ignore[attr-defined]
                        self._wait_until_detail_ready(normalized_mid, timeout=wait_timeout)
                        last_activity = time.monotonic()
                    continue
                path = endpoint_path(packet.url)
                if path not in {DATA_ENDPOINT, INGREDIENT_ENDPOINT}:
                    continue
                request = packet.request.postData
                response = packet.response.body
                if isinstance(request, dict) and request.get("goodsMid") not in {
                    None,
                    normalized_mid,
                }:
                    continue
                last_activity = time.monotonic()
                if response_requires_captcha(response):
                    if not captcha_waiting:
                        captcha_waiting = True
                        logger.warning(
                            "产品详情触发图片验证码，暂停并等待人工处理：mid=%s",
                            normalized_mid,
                        )
                        if on_captcha is not None:
                            on_captcha(normalized_mid)
                    continue
                if captcha_waiting and (
                    not isinstance(response, dict) or response.get("retCode") != 200
                ):
                    logger.debug(
                        "验证码恢复期间忽略过渡响应：mid=%s path=%s",
                        normalized_mid,
                        path,
                    )
                    continue
                self._validate_response(path, response)
                if path == DATA_ENDPOINT:
                    data_responses.append(response)
                else:
                    ingredient_responses.append(response)
                if captcha_waiting and data_responses and ingredient_responses:
                    captcha_waiting = False
                    logger.info("图片验证码已通过，恢复产品详情采集：mid=%s", normalized_mid)
                    if on_captcha_resolved is not None:
                        on_captcha_resolved(normalized_mid)
        finally:
            listener.stop()

        if not data_responses:
            raise RuntimeError("未捕获产品详情 data 响应")
        if not ingredient_responses:
            raise RuntimeError("未捕获产品详情 ingredient 响应")

        unique_data = unique_responses(data_responses)
        unique_ingredients = unique_responses(ingredient_responses)
        if len(unique_data) > 1:
            logger.warning("同一详情页返回了 %d 份不同的 data 响应，将保留第一份", len(unique_data))

        product = unique_data[0].get("data")
        if not isinstance(product, dict):
            raise RuntimeError("data 响应缺少产品对象")
        brand_name = str(product.get("brandName") or "unknown-brand").strip()
        product_name = str(product.get("name") or "").strip()
        result_dir = output_root / safe_filename(brand_directory or brand_name) / normalized_mid
        write_json_atomic(result_dir / "data.json", unique_data[0])
        write_ingredient_files(result_dir, unique_ingredients)

        logger.info(
            "产品详情已保存: brand=%s mid=%s ingredient=%d 次请求/%d 份唯一响应",
            brand_name,
            normalized_mid,
            len(ingredient_responses),
            len(unique_ingredients),
        )
        return ProductDetailResult(
            mid=normalized_mid,
            brand_name=brand_name,
            product_name=product_name,
            output_dir=result_dir,
            ingredient_responses=len(ingredient_responses),
            ingredient_files=len(unique_ingredients),
        )

    def _wait_until_detail_ready(self, mid: str, *, timeout: float | None) -> str:
        started = time.monotonic()
        last_notice = 0.0
        while True:
            current_url = str(getattr(self.page, "url", "") or "")
            if detail_mid_from_url(current_url) == mid:
                logger.info("产品详情页已就绪: mid=%s", mid)
                return current_url
            now = time.monotonic()
            if timeout is not None and now - started >= timeout:
                raise TimeoutError(f"等待产品详情页超时（{timeout:g} 秒）：mid={mid}")
            if last_notice == 0 or now - last_notice >= 30:
                logger.warning(
                    "当前不是目标产品详情页，保持页面不动并等待：mid=%s current=%s",
                    mid,
                    current_url,
                )
                last_notice = now
            time.sleep(2)

    @staticmethod
    def _validate_response(path: str, response: object) -> None:
        if not isinstance(response, dict) or response.get("retCode") != 200:
            code = response.get("retCode") if isinstance(response, dict) else None
            message = response.get("msg") if isinstance(response, dict) else "响应不是 JSON"
            raise RuntimeError(f"详情接口失败：path={path} retCode={code} msg={message}")
