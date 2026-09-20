"""命令行入口。"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from bebd_crawler.adapters.bebd import BebdSearchCrawler, CaptchaRequiredError, SearchPage
from bebd_crawler.adapters.product_detail import MID_PATTERN, ProductDetailCrawler
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
from bebd_crawler.human_timing import HumanTiming
from bebd_crawler.output import (
    BrandSummary,
    DetailProgress,
    JsonlSink,
    MidLineSink,
    SearchProgress,
    create_run_dir,
    read_detail_state,
    read_search_state,
    safe_filename,
    write_detail_state,
    write_search_state,
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
    detail = subparsers.add_parser("product-detail", help="按商品 mid 拦截产品详情和成分响应")
    detail.add_argument("--mid", required=True, help="商品 MID")
    detail.add_argument(
        "--login-wait",
        type=float,
        default=0,
        help="等待手工登录的秒数；0 表示一直等待（默认 0）",
    )
    detail.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "data", help="结果根目录"
    )
    search_all = subparsers.add_parser(
        "search-all", help="搜索一个品牌词并翻完账号可访问的全部结果"
    )
    search_all.add_argument("--brand", required=True, help="搜索词，例如欧莱雅")
    search_all.add_argument(
        "--login-wait",
        type=float,
        default=0,
        help="等待手工登录的秒数；0 表示一直等待（默认 0）",
    )
    search_all.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "data", help="结果根目录"
    )
    details = subparsers.add_parser(
        "product-details", help="从 MID 文件断点批量采集产品详情"
    )
    details.add_argument("--brand", required=True, help="结果目录品牌名，例如欧莱雅")
    details.add_argument("--limit", type=int, default=10, help="本次新采集商品数（默认 10）")
    details.add_argument("--mids-file", type=Path, help="MID 文件；默认读取品牌目录下的全集文件")
    details.add_argument(
        "--no-cookie",
        action="store_true",
        help="使用干净浏览器直接打开，不读取或注入本地 cookie",
    )
    details.add_argument(
        "--login-wait",
        type=float,
        default=0,
        help="等待手工登录的秒数；0 表示一直等待（默认 0）",
    )
    details.add_argument(
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
            refresh_cookie_file(
                page,
                settings.cookie_path,
                source_url=str(getattr(page, "url", "") or check.url),
            )
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


def run_product_detail(settings: Settings, args: argparse.Namespace) -> int:
    page = None
    session_verified = False
    cookies_refreshed = False
    try:
        cookies = load_cookie_file(settings.cookie_path)
        page = create_browser_page(headless=False)
        page.get("https://bebd.bevol.com/")  # type: ignore[attr-defined]
        inject_cookies(page, cookies)

        result = ProductDetailCrawler(page).crawl(
            args.mid,
            output_root=args.output_dir.expanduser(),
            wait_timeout=args.login_wait if args.login_wait > 0 else None,
        )
        session_verified = True
        refresh_cookie_file(page, settings.cookie_path, source_url=str(page.url))
        cookies_refreshed = True
        print(f"产品详情采集完成：{result.output_dir}")
        print(f"品牌：{result.brand_name}")
        print(f"产品：{result.product_name}")
        print(
            f"ingredient：拦截 {result.ingredient_responses} 次，"
            f"保存 {result.ingredient_files} 份唯一响应"
        )
        return 0
    except KeyboardInterrupt:
        logger.warning("用户中断产品详情采集")
        return 130
    except Exception as exc:
        logger.exception("产品详情采集失败：%s", exc)
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
                logger.warning("产品详情采集结束时回刷登录态失败：%s", exc)
        close_browser(page)


def run_search_all(settings: Settings, args: argparse.Namespace) -> int:
    page = None
    session_verified = False
    cookies_refreshed = False
    state_path: Path | None = None
    brand = ""
    completed_page = 0
    api_total = 0
    sink: MidLineSink | None = None
    try:
        brand = str(args.brand).strip()
        if not brand:
            raise ValueError("--brand 不能为空")
        brand_dir = args.output_dir.expanduser() / safe_filename(brand)
        output_path = brand_dir / "search-result-mids-all.json"
        state_path = brand_dir / "state.md"
        previous_state = read_search_state(state_path)
        sink = MidLineSink(output_path, resume=True)
        completed_page = previous_state.completed_page if previous_state is not None else 0
        api_total = previous_state.api_total if previous_state is not None else 0
        if previous_state is not None and previous_state.captured != sink.written:
            logger.warning(
                "进度文件记录 %d 条，MID 文件实际 %d 条；续跑时仍会按 MID 去重",
                previous_state.captured,
                sink.written,
            )
            if sink.written < previous_state.captured:
                raise RuntimeError("MID 文件少于进度记录，不能安全跳过已完成页")
        start_page = completed_page + 1
        write_search_state(
            state_path,
            SearchProgress(
                brand=brand,
                completed_page=completed_page,
                captured=sink.written,
                next_page=start_page,
                api_total=api_total,
                status="准备继续",
            ),
        )
        logger.info(
            "读取断点：已完成第 %d 页、已有 %d 条，将从第 %d 页继续",
            completed_page,
            sink.written,
            start_page,
        )

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

        assert state_path is not None

        def save_page_progress(page_data: SearchPage, written: int) -> None:
            nonlocal completed_page, api_total
            completed_page = page_data.current
            api_total = page_data.total
            write_search_state(
                state_path,
                SearchProgress(
                    brand=brand,
                    completed_page=completed_page,
                    captured=written,
                    next_page=completed_page + 1,
                    api_total=api_total,
                    status="采集中",
                ),
            )

        stats = crawler.crawl(
            brand,
            limit=None,
            on_page=sink.append,
            start_page=start_page,
            already_written=sink.written,
            on_progress=save_page_progress,
        )
        api_total = stats.total
        final_status = "已完成" if stats.written >= stats.total else "页面已无下一页"
        write_search_state(
            state_path,
            SearchProgress(
                brand=brand,
                completed_page=completed_page,
                captured=stats.written,
                next_page=completed_page + 1,
                api_total=api_total,
                status=final_status,
            ),
        )
        refresh_cookie_file(page, settings.cookie_path, source_url=str(page.url or ready_url))
        cookies_refreshed = True

        print(f"搜索结果已保存：{output_path}")
        print(
            f"搜索词：{brand}；保存 {stats.written} 条 / {stats.pages} 页；"
            f"接口总数 {stats.total}；停止原因 {stats.stop_reason}"
        )
        if stats.stop_reason == "next_page_unavailable" and stats.written < stats.total:
            logger.warning(
                "页面已禁止继续翻页：本次保存的是账号当前可访问的全部 %d 条，接口报告总数 %d",
                stats.written,
                stats.total,
            )
        return 0
    except KeyboardInterrupt:
        logger.warning("用户中断全部搜索结果采集；已完成页面仍保留在结果文件中")
        if state_path is not None and sink is not None:
            write_search_state(
                state_path,
                SearchProgress(
                    brand=brand,
                    completed_page=completed_page,
                    captured=sink.written,
                    next_page=completed_page + 1,
                    api_total=api_total,
                    status="已中断，可继续",
                ),
            )
        return 130
    except CaptchaRequiredError as exc:
        logger.warning("%s；完成验证码后可从第 %d 页继续", exc, completed_page + 1)
        if state_path is not None and sink is not None:
            write_search_state(
                state_path,
                SearchProgress(
                    brand=brand,
                    completed_page=completed_page,
                    captured=sink.written,
                    next_page=completed_page + 1,
                    api_total=api_total,
                    status="遇到图片验证码，可人工处理后继续",
                ),
            )
        return 2
    except Exception as exc:
        logger.exception("全部搜索结果采集失败：%s", exc)
        if state_path is not None and sink is not None:
            write_search_state(
                state_path,
                SearchProgress(
                    brand=brand,
                    completed_page=completed_page,
                    captured=sink.written,
                    next_page=completed_page + 1,
                    api_total=api_total,
                    status=f"失败，可继续（{type(exc).__name__}）",
                ),
            )
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
                logger.warning("全部搜索结果采集结束时回刷登录态失败：%s", exc)
        close_browser(page)


def _load_mid_lines(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"MID 文件不存在：{path}")
    mids: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        mid = line.strip()
        if not MID_PATTERN.fullmatch(mid):
            raise ValueError(f"MID 文件第 {line_number} 行格式不正确")
        if mid in seen:
            raise ValueError(f"MID 文件第 {line_number} 行重复：{mid}")
        seen.add(mid)
        mids.append(mid)
    return mids


def _detail_is_complete(brand_dir: Path, mid: str) -> bool:
    product_dir = brand_dir / mid
    return (product_dir / "data.json").is_file() and (product_dir / "ingredient.json").is_file()


def run_product_details(settings: Settings, args: argparse.Namespace) -> int:
    page = None
    session_verified = False
    cookies_refreshed = False
    brand = str(args.brand).strip()
    brand_dir = args.output_dir.expanduser() / safe_filename(brand)
    state_path = brand_dir / "detail-state.md"
    mids_path = (
        args.mids_file.expanduser()
        if args.mids_file
        else brand_dir / "search-result-mids-all.json"
    )
    mids: list[str] = []
    completed: set[str] = set()
    next_line = 1
    current_mid = ""
    last_completed_mid = ""

    def mid_at(line_number: int) -> str:
        return mids[line_number - 1] if 1 <= line_number <= len(mids) else ""

    def save_state(status: str) -> None:
        write_detail_state(
            state_path,
            DetailProgress(
                brand=brand,
                status=status,
                total_mids=len(mids),
                completed_details=len(completed),
                next_line=next_line,
                next_mid=mid_at(next_line),
                last_completed_mid=last_completed_mid,
            ),
        )

    try:
        if not brand:
            raise ValueError("--brand 不能为空")
        if args.limit < 1:
            raise ValueError("--limit 必须大于 0")
        mids = _load_mid_lines(mids_path)
        previous = read_detail_state(state_path)
        if previous is not None:
            if previous.brand != brand:
                raise ValueError("详情进度文件品牌与命令参数不一致")
            next_line = previous.next_line
            last_completed_mid = previous.last_completed_mid
        completed = {mid for mid in mids if _detail_is_complete(brand_dir, mid)}
        if next_line < 1 or next_line > len(mids) + 1:
            raise ValueError("详情进度文件的下一行超出 MID 文件范围")
        save_state("准备继续")
        logger.info(
            "详情断点：MID 共 %d 个，已有 %d 个完整目录，从第 %d 行继续，本次新采集 %d 个",
            len(mids),
            len(completed),
            next_line,
            args.limit,
        )

        page = create_browser_page(headless=False)
        if not args.no_cookie:
            cookies = load_cookie_file(settings.cookie_path)
            page.get("https://bebd.bevol.com/")  # type: ignore[attr-defined]
            inject_cookies(page, cookies)
        else:
            logger.info("本次使用干净浏览器，不读取或注入本地 cookie")
        human_timing = HumanTiming()
        detail_crawler = ProductDetailCrawler(page, human_timing=human_timing)
        newly_collected = 0
        made_request = False

        def captcha_paused(mid: str) -> None:
            nonlocal current_mid
            current_mid = mid
            save_state("遇到图片验证码，已暂停；完成验证后自动恢复")

        def captcha_resolved(mid: str) -> None:
            nonlocal current_mid
            current_mid = mid
            save_state("图片验证码已通过，继续采集")

        while next_line <= len(mids) and newly_collected < args.limit:
            current_mid = mids[next_line - 1]
            if current_mid in completed:
                logger.info("第 %d 行 MID 已有完整详情，跳过：%s", next_line, current_mid)
                last_completed_mid = current_mid
                next_line += 1
                save_state("跳过已有详情，继续")
                continue
            if made_request:
                delay = human_timing.between_actions(1.0, 10.0)
                logger.info("商品之间随机等待 %.2f 秒", delay)
            result = detail_crawler.crawl(
                current_mid,
                output_root=args.output_dir.expanduser(),
                brand_directory=brand,
                wait_timeout=args.login_wait if args.login_wait > 0 else None,
                on_captcha=captcha_paused,
                on_captcha_resolved=captcha_resolved,
            )
            made_request = True
            session_verified = True
            completed.add(current_mid)
            newly_collected += 1
            last_completed_mid = current_mid
            next_line += 1
            save_state(f"采集中，本次已完成 {newly_collected}/{args.limit}")
            logger.info(
                "批量详情 %d/%d：%s %s",
                newly_collected,
                args.limit,
                result.mid,
                result.product_name,
            )
            refresh_cookie_file(page, settings.cookie_path, source_url=str(page.url))
            cookies_refreshed = True

        final_status = (
            "全部完成"
            if next_line > len(mids)
            else f"本次已完成 {newly_collected} 个，可继续"
        )
        save_state(final_status)
        refresh_cookie_file(page, settings.cookie_path, source_url=str(page.url))
        cookies_refreshed = True
        print(f"批量产品详情完成：本次新采集 {newly_collected} 个")
        print(f"累计完整详情：{len(completed)}/{len(mids)}")
        print(f"进度文件：{state_path}")
        return 0
    except KeyboardInterrupt:
        logger.warning("用户中断批量产品详情采集")
        if mids:
            save_state("已中断，可继续")
        return 130
    except Exception as exc:
        logger.exception("批量产品详情采集失败：%s", exc)
        if mids:
            save_state(f"失败，可从当前 MID 继续（{type(exc).__name__}）")
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
                logger.warning("批量详情结束时回刷登录态失败：%s", exc)
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
    if args.command == "product-detail":
        return run_product_detail(settings, args)
    if args.command == "search-all":
        return run_search_all(settings, args)
    if args.command == "product-details":
        return run_product_details(settings, args)
    parser.error(f"未知命令：{args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
