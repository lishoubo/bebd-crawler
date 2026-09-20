"""少量、可控的人类化页面交互节奏。"""
from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


class HumanTiming:
    def __init__(
        self,
        *,
        random_source: random.Random | random.SystemRandom | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.random = random_source or random.SystemRandom()
        self.sleep = sleeper

    def before_next_page(self, page: object, button: object) -> float:
        """翻页前滚动、定位按钮、悬停并随机等待。"""
        distance = self.random.randint(180, 520)
        page.scroll.down(distance)  # type: ignore[attr-defined]
        self.sleep(self.random.uniform(0.35, 0.85))
        button.scroll.to_see(center=True)  # type: ignore[attr-defined]
        try:
            button.hover()  # type: ignore[attr-defined]
        except Exception as exc:
            logger.debug("翻页按钮悬停失败，继续执行：%s", exc)
        delay = self.random.uniform(0.8, 1.8)
        self.sleep(delay)
        return delay

    def after_page_loaded(self) -> float:
        delay = self.random.uniform(0.6, 1.4)
        self.sleep(delay)
        return delay

    def browse_product_detail(self, page: object) -> float:
        """详情页加载后向下浏览一小段，并停留片刻。"""
        distance = self.random.randint(220, 620)
        page.scroll.down(distance)  # type: ignore[attr-defined]
        delay = self.random.uniform(0.8, 1.8)
        self.sleep(delay)
        logger.debug("产品详情页随机向下滚动 %dpx，等待 %.2fs", distance, delay)
        return delay

    def between_actions(self, minimum: float = 0.8, maximum: float = 1.8) -> float:
        delay = self.random.uniform(minimum, maximum)
        self.sleep(delay)
        return delay
