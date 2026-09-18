"""DrissionPage 浏览器生命周期。"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def create_browser_page(*, headless: bool = False, user_data_path: Path | None = None) -> object:
    """创建隔离的 Chromium 页面。

    登录阶段默认可见。没有显式指定用户目录时使用 DrissionPage 的自动端口与临时目录，
    避免读取用户日常 Chrome 配置。
    """
    from DrissionPage import ChromiumOptions, ChromiumPage

    options = ChromiumOptions()
    if user_data_path is None:
        options.auto_port()
    else:
        user_data_path.mkdir(parents=True, exist_ok=True)
        options.set_user_data_path(str(user_data_path))
        options.auto_port()
    options.set_argument("--disable-blink-features=AutomationControlled")
    options.set_argument("--lang=zh-CN")
    options.set_argument("--window-size=1440,960")
    options.set_pref("intl.accept_languages", "zh-CN,zh,en-US,en")
    options.headless(headless)
    logger.info("启动浏览器: headless=%s", headless)
    return ChromiumPage(addr_or_opts=options)


def close_browser(page: object | None) -> None:
    if page is None:
        return
    try:
        page.quit()  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - browser cleanup is best effort
        logger.warning("关闭浏览器失败: %s", exc)

