"""BEBD cookie 的完整导出、落盘、恢复与会话校验。"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

COOKIE_FILE_VERSION = 1
_LOGIN_URL_MARKERS = ("/login", "#/login", "signin", "sign-in")
_COOKIE_KEYS = (
    "name",
    "value",
    "domain",
    "path",
    "expires",
    "httpOnly",
    "secure",
    "sameSite",
    "priority",
    "sameParty",
    "sourceScheme",
    "sourcePort",
    "partitionKey",
    "partitionKeyOpaque",
)


@dataclass(frozen=True)
class SessionCheck:
    ok: bool
    url: str
    reason: str


def export_all_cookies(page: object) -> list[dict[str, Any]]:
    """通过 CDP 导出整个隔离浏览器的 cookie jar，保留还原所需属性。"""
    result = page.run_cdp("Network.getAllCookies")  # type: ignore[attr-defined]
    raw = result.get("cookies", []) if isinstance(result, dict) else []
    cookies = [_normalize_cookie(item) for item in raw if isinstance(item, dict)]
    cookies = [item for item in cookies if item.get("name") and item.get("domain")]
    logger.info("已从浏览器导出 %d 条 cookie", len(cookies))
    return cookies


def _normalize_cookie(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: raw[key] for key in _COOKIE_KEYS if key in raw and raw[key] is not None}


def save_cookie_file(path: Path, cookies: list[dict[str, Any]], *, source_url: str) -> None:
    """原子写入 cookie 文件，并将权限限制为当前用户读写。"""
    if not cookies:
        raise ValueError("没有可保存的 cookie；请确认已经完成登录")
    payload = {
        "version": COOKIE_FILE_VERSION,
        "saved_at": datetime.now(UTC).isoformat(),
        "source_url": source_url,
        "cookies": cookies,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    logger.info("cookie 已保存到 %s（%d 条，内容不会写入日志）", path, len(cookies))


def load_cookie_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"cookie 文件不存在：{path}；请先执行 bebd-crawler login")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != COOKIE_FILE_VERSION:
        raise ValueError(f"不支持的 cookie 文件格式：{path}")
    raw = payload.get("cookies")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"cookie 文件为空：{path}")
    cookies = [_normalize_cookie(item) for item in raw if isinstance(item, dict)]
    if not cookies:
        raise ValueError(f"cookie 文件没有有效条目：{path}")
    return cookies


def refresh_cookie_file(page: object, path: Path, *, source_url: str) -> int:
    """从仍处于登录状态的页面重新导出 cookie，并原子覆盖本地登录态。"""
    cookies = export_all_cookies(page)
    save_cookie_file(path, cookies, source_url=source_url)
    logger.info("登录态已回刷：%d 条 cookie", len(cookies))
    return len(cookies)


def inject_cookies(page: object, cookies: list[dict[str, Any]]) -> int:
    """按保存时的原属性逐条恢复 cookie，失败会汇总报错。"""
    success_count = 0
    for cookie in cookies:
        params = _cookie_set_params(cookie)
        try:
            result = page.run_cdp("Network.setCookie", **params)  # type: ignore[attr-defined]
            succeeded = not isinstance(result, dict) or bool(result.get("success", True))
        except Exception as exc:
            logger.warning(
                "恢复 cookie 失败: name=%s domain=%s error=%s",
                cookie.get("name", "?"),
                cookie.get("domain", "?"),
                exc,
            )
            succeeded = False
        success_count += int(succeeded)
    if success_count != len(cookies):
        raise RuntimeError(f"cookie 恢复不完整：成功 {success_count}/{len(cookies)}")
    logger.info("已恢复 %d 条 cookie", success_count)
    return success_count


def _cookie_set_params(cookie: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "name",
        "value",
        "domain",
        "path",
        "expires",
        "httpOnly",
        "secure",
        "sameSite",
        "priority",
        "sameParty",
        "sourceScheme",
        "sourcePort",
        "partitionKey",
    }
    params = {key: value for key, value in cookie.items() if key in allowed}
    params.setdefault("path", "/")
    return params


def navigate_and_check(page: object, target_url: str, *, wait_seconds: float = 2.0) -> SessionCheck:
    page.get(target_url)  # type: ignore[attr-defined]
    try:
        page.wait.doc_loaded(timeout=20)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.debug("等待页面加载完成超时，将按当前页面继续检查：%s", exc)
    time.sleep(wait_seconds)
    url = str(getattr(page, "url", "") or "").strip()
    return check_session_url(url, target_url)


def check_session_url(current_url: str, target_url: str) -> SessionCheck:
    """做保守的 URL 级会话判断；真实页面特征将在登录探查后补强。"""
    lowered = current_url.lower()
    if not current_url:
        return SessionCheck(False, current_url, "页面没有返回 URL")
    if any(marker in lowered for marker in _LOGIN_URL_MARKERS):
        return SessionCheck(False, current_url, "页面仍处于登录入口")
    current_host = (urlparse(current_url).hostname or "").lower()
    target_host = (urlparse(target_url).hostname or "").lower()
    if current_host != target_host:
        return SessionCheck(False, current_url, f"页面跳转到了非目标域名：{current_host or '未知'}")
    return SessionCheck(True, current_url, "目标页面可访问")
