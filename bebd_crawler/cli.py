"""命令行入口。"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from bebd_crawler.adapters.bebd import BebdSearchCrawler
from bebd_crawler.auth import (
    export_all_cookies,
    inject_cookies,
    load_cookie_file,
    navigate_and_check,
    refresh_cookie_file,
    save_cookie_file,
)
from bebd_crawler.brands import load_brands, normalize_brands
from bebd_crawler.browser import close_browser, create_browser_page
from bebd_crawler.config import PROJECT_ROOT, Settings
from bebd_crawler.output import (
    BrandSummary,
    JsonlSink,
    create_run_dir,
    safe_filename,
    write_summary,
)

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bebd-crawler")
    parser.add_argument("--verbose", action="store_true", help="显示调试日志")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login", help="打开可见浏览器，手工登录后保存 cookie")
    check = subparsers.add_parser("session-check", help="恢复本地 cookie 并验证登录态")
    check.add_argument("--keep-open", action="store_true", help="验证后保持浏览器开启")
    crawl = subparsers.add_parser("crawl", help="搜索品牌词并拦截商品列表响应")
    crawl.add_argument(
        "--brand",
        action="append",
        dest="brands",
        help="搜索词；可重复传入，例如 --brand 欧莱雅 --brand 雅诗兰黛",
    )
    crawl.add_argument("--brands-file", type=Path, help="JSON、简单 YAML 或逐行文本品牌清单")
    crawl.add_argument("--limit", type=int, default=90, help="每个搜索词最多采集条数（默认 90）")
    crawl.add_argument(
        "--login-wait",
        type=float,
        default=0,
        help="等待手工登录的秒数；0 表示一直等待（默认 0）",
    )
    crawl.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "data", help="结果根目录"
    )
    return parser


def run_login(settings: Settings) -> int:
    page = None
    try:
        page = create_browser_page(headless=False)
        page.get(settings.target_url)  # type: ignore[attr-defined]
        print("\n请在刚打开的浏览器中手工完成 BEBD 登录。")
        print("确认已进入数据中心页面后，回到这里按 Enter 保存登录态。")
        input()

        check = navigate_and_check(page, settings.target_url)
        if not check.ok:
            logger.error("登录校验失败：%s；当前 URL=%s", check.reason, check.url)
            return 2
        cookies = export_all_cookies(page)
        save_cookie_file(settings.cookie_path, cookies, source_url=check.url)
        print(f"登录态已保存：{settings.cookie_path}")
        print(f"共保存 {len(cookies)} 条 cookie。")
        return 0
    except KeyboardInterrupt:
        logger.warning("用户取消登录")
        return 130
    except Exception as exc:
        logger.exception("登录流程失败：%s", exc)
        return 1
    finally:
        close_browser(page)


def run_session_check(settings: Settings, *, keep_open: bool) -> int:
    page = None
    try:
        cookies = load_cookie_file(settings.cookie_path)
        page = create_browser_page(headless=False)
        page.get("https://bebd.bevol.com/")  # type: ignore[attr-defined]
        inject_cookies(page, cookies)
        check = navigate_and_check(page, settings.target_url)
        if not check.ok:
            logger.error("登录态无效：%s；请重新执行 bebd-crawler login", check.reason)
            return 2
        refresh_cookie_file(page, settings.cookie_path, source_url=check.url)
        print(f"登录态有效，已打开：{check.url}")
        if keep_open:
            print("浏览器将保持开启；检查完成后回到这里按 Enter 关闭。")
            input()
        return 0
    except Exception as exc:
        logger.error("登录态检查失败：%s", exc)
        return 1
    finally:
        close_browser(page)


def _resolve_brands(args: argparse.Namespace) -> list[str]:
    if args.brands and args.brands_file:
        raise ValueError("--brand 与 --brands-file 不能同时使用")
    if args.brands:
        return normalize_brands(args.brands)
    if args.brands_file:
        return load_brands(args.brands_file)
    default_path = PROJECT_ROOT / "config" / "brands.yaml"
    if not default_path.exists():
        default_path = PROJECT_ROOT / "config" / "brands.example.yaml"
    return load_brands(default_path)


def run_crawl(settings: Settings, args: argparse.Namespace) -> int:
    page = None
    started_at = datetime.now(UTC)
    summaries: list[BrandSummary] = []
    run_dir: Path | None = None
    session_verified = False
    cookies_refreshed = False
    try:
        brands = _resolve_brands(args)
        if args.limit < 1:
            raise ValueError("--limit 必须大于 0")
        run_dir = create_run_dir(args.output_dir.expanduser())
        cookies = load_cookie_file(settings.cookie_path)
        page = create_browser_page(headless=False)
        page.get("https://bebd.bevol.com/")  # type: ignore[attr-defined]
        inject_cookies(page, cookies)
        page.get(settings.target_url)  # type: ignore[attr-defined]
        crawler = BebdSearchCrawler(page)
        ready_url = crawler.wait_until_search_ready(
            settings.target_url,
            timeout=args.login_wait if args.login_wait > 0 else None,
        )
        session_verified = True

        for keyword in brands:
            output_path = run_dir / f"{safe_filename(keyword)}.jsonl"
            sink = JsonlSink(output_path)
            summary = BrandSummary(
                keyword=keyword,
                status="running",
                requested_limit=args.limit,
                output_file=output_path.name,
            )
            summaries.append(summary)
            try:
                stats = crawler.crawl(keyword, limit=args.limit, on_page=sink.append)
                summary.status = "success"
                summary.api_total = stats.total
                summary.pages = stats.pages
                summary.received = stats.received
                summary.written = stats.written
            except Exception as exc:
                summary.status = "failed"
                summary.written = sink.written
                summary.error = str(exc)
                raise
            finally:
                write_summary(run_dir, summaries, started_at=started_at, status="running")

        refresh_cookie_file(page, settings.cookie_path, source_url=str(page.url or ready_url))
        cookies_refreshed = True
        summary_path = write_summary(run_dir, summaries, started_at=started_at, status="success")
        print(f"采集完成：{run_dir}")
        print(f"运行摘要：{summary_path}")
        for item in summaries:
            print(
                f"- {item.keyword}: {item.written} 条 / {item.pages} 页，"
                f"接口总数 {item.api_total}"
            )
        return 0
    except KeyboardInterrupt:
        logger.warning("用户中断采集")
        if run_dir:
            write_summary(run_dir, summaries, started_at=started_at, status="cancelled")
        return 130
    except Exception as exc:
        logger.exception("采集失败：%s", exc)
        if run_dir:
            write_summary(run_dir, summaries, started_at=started_at, status="failed")
            logger.error("已保留部分结果：%s", run_dir)
        return 1
    finally:
        if page is not None and session_verified and not cookies_refreshed:
            try:
                refresh_cookie_file(
                    page,
                    settings.cookie_path,
                    source_url=str(getattr(page, "url", "") or settings.target_url),
                )
            except Exception as exc:
                logger.warning("采集结束时回刷登录态失败，保留原 cookie 文件：%s", exc)
        close_browser(page)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    if args.command == "login":
        return run_login(settings)
    if args.command == "session-check":
        return run_session_check(settings, keep_open=args.keep_open)
    if args.command == "crawl":
        return run_crawl(settings, args)
    parser.error(f"未知命令：{args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
