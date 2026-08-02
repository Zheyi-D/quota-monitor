#!/usr/bin/env python3
"""香港入境处预约配额监控 — CLI 入口。

用法:
  python monitor.py                        # 持续监控（默认 10 分钟间隔）
  python monitor.py --once                  # 单次运行，打印当前状态
  python monitor.py --interval 300          # 每 5 分钟轮询
  python monitor.py --config myconfig.json  # 使用指定配置文件
  python monitor.py --ci                    # CI 模式（从环境变量读取配置）

CI 模式环境变量:
  FEISHU_WEBHOOK_URL   - 飞书群机器人 webhook URL
  EMAIL_SUBSCRIBERS    - 邮件订阅者列表，JSON 数组格式
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

# 修复 Windows 控制台编码问题
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def safe_print(*args, **kwargs):
    """安全的 print，处理 Windows GBK 编码问题。"""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        # 回退：移除无法编码的字符
        safe_args = []
        for a in args:
            if isinstance(a, str):
                a = a.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
                    sys.stdout.encoding or "utf-8", errors="replace"
                )
            safe_args.append(a)
        print(*safe_args, **kwargs)

from quota_monitor.core import (
    DEFAULT_OFFICES,
    detect_changes,
    fetch_snapshot,
    format_changes,
    has_significant_change,
    load_config,
)
from quota_monitor.notify import send_notifications
from quota_monitor.state import load_state, save_state

logger = logging.getLogger("quota_monitor")

# 默认轮询间隔（秒）
DEFAULT_INTERVAL = 600
# 初始化基准后的等待间隔（秒）— 首次运行后不通知，等下一轮对比
INITIAL_WAIT = 10


def setup_logging(config):
    """配置日志系统：同时输出到文件和控制台。"""
    log_cfg = config.get("log", {})
    level = getattr(logging, log_cfg.get("level", "INFO"), logging.INFO)
    log_file = log_cfg.get("file", "monitor.log")
    max_size = log_cfg.get("max_size_mb", 10) * 1024 * 1024
    backup_count = log_cfg.get("backup_count", 3)

    root_logger = logging.getLogger("quota_monitor")
    root_logger.setLevel(level)

    # 文件 handler（带轮转）
    try:
        fh = RotatingFileHandler(
            log_file, maxBytes=max_size, backupCount=backup_count, encoding="utf-8"
        )
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root_logger.addHandler(fh)
    except IOError as e:
        print(f"警告: 无法创建日志文件 {log_file}: {e}")

    # 控制台 handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    root_logger.addHandler(ch)

    return root_logger


def run_once(config, state_path="state.json"):
    """单次运行：拉取 API、检测变化、发送通知。"""
    logger.info("单次运行模式")

    # 加载配置
    offices = config.get("offices", DEFAULT_OFFICES)
    office_codes = list(offices.keys()) if isinstance(offices, dict) else offices
    date_range = config.get("date_range", {})
    retry_cfg = config.get("retry", {})
    notify_cfg = config.get("notifications", {})

    # 加载上次状态
    state = load_state(state_path)
    old_snapshot = state.get("last_snapshot", {})
    is_first_run = not old_snapshot

    # 拉取 API
    logger.info("正在拉取配额数据...")
    new_snapshot = fetch_snapshot(
        offices=office_codes,
        date_start=date_range.get("start"),
        date_end=date_range.get("end"),
        svc_id=config.get("api", {}).get("svc_id", 579),
        timeout=config.get("api", {}).get("timeout", 30),
        max_retries=retry_cfg.get("max_retries", 3),
        backoff_base=retry_cfg.get("backoff_base_seconds", 5),
    )

    if not new_snapshot:
        logger.error("无法获取配额数据")
        return

    # 保存新快照
    save_state(state_path, new_snapshot)

    # 检测变化
    changes = detect_changes(old_snapshot, new_snapshot)

    # 打印结果
    if is_first_run:
        logger.info("首次运行，基准快照已建立 (%d 条记录)", len(new_snapshot))
        _print_summary(new_snapshot, offices)
        logger.info("下次轮询时将检测变化并发送通知")
    elif has_significant_change(changes):
        message = format_changes(changes, offices)
        safe_print("\n" + message)
        logger.info("检测到配额变化，发送通知...")
        result = send_notifications(message, notify_cfg)
        logger.info("通知结果: 飞书=%s",
                    "OK" if result["feishu"] else "FAIL")
    else:
        logger.info("配额状态无变化")
        _print_summary(new_snapshot, offices)


def run_loop(config, interval, state_path="state.json"):
    """持续轮询模式。"""
    logger.info("持续监控模式，间隔 %d 秒 (%.1f 分钟)", interval, interval / 60)

    offices = config.get("offices", DEFAULT_OFFICES)
    office_codes = list(offices.keys()) if isinstance(offices, dict) else offices
    date_range = config.get("date_range", {})
    retry_cfg = config.get("retry", {})
    notify_cfg = config.get("notifications", {})

    # 加载上次状态
    state = load_state(state_path)
    old_snapshot = state.get("last_snapshot", {})
    is_first_run = not old_snapshot

    # 如果没有基准快照，先建立基准
    if is_first_run:
        logger.info("首次运行，正在建立基准快照...")
        new_snapshot = fetch_snapshot(
            offices=office_codes,
            date_start=date_range.get("start"),
            date_end=date_range.get("end"),
            svc_id=config.get("api", {}).get("svc_id", 579),
            timeout=config.get("api", {}).get("timeout", 30),
            max_retries=retry_cfg.get("max_retries", 3),
            backoff_base=retry_cfg.get("backoff_base_seconds", 5),
        )
        if new_snapshot:
            save_state(state_path, new_snapshot)
            old_snapshot = new_snapshot
            logger.info("基准快照已建立 (%d 条记录)，开始监控...", len(new_snapshot))
            _print_summary(new_snapshot, offices)
        else:
            logger.warning("首次拉取失败，将在下一轮重试")

    running = True

    def handle_shutdown(signum, frame):
        nonlocal running
        sig_name = signal.Signals(signum).name
        logger.info("收到 %s 信号，正在退出...", sig_name)
        running = False

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    cycle = 0
    try:
        while running:
            cycle += 1
            logger.info("─" * 40)
            logger.info("第 %d 轮轮询 %s", cycle, datetime.now().strftime("%H:%M:%S"))

            new_snapshot = fetch_snapshot(
                offices=office_codes,
                date_start=date_range.get("start"),
                date_end=date_range.get("end"),
                svc_id=config.get("api", {}).get("svc_id", 579),
                timeout=config.get("api", {}).get("timeout", 30),
                max_retries=retry_cfg.get("max_retries", 3),
                backoff_base=retry_cfg.get("backoff_base_seconds", 5),
            )

            if not new_snapshot:
                logger.warning("本轮拉取失败，等待下次轮询")
                if running:
                    time.sleep(interval)
                continue

            # 检测变化
            if old_snapshot:
                changes = detect_changes(old_snapshot, new_snapshot)
                if has_significant_change(changes):
                    message = format_changes(changes, offices)
                    logger.info("检测到配额变化！")
                    print("\n" + message + "\n")
                    result = send_notifications(message, notify_cfg)
                    logger.info("通知: 飞书=%s",
                                "OK" if result["feishu"] else "FAIL")
                else:
                    # 简要汇总
                    available = sum(1 for v in new_snapshot.values()
                                    if v in ("quota-g", "quota-y"))
                    logger.info("无变化 — 当前 %d 条记录中有配额", available)
            else:
                logger.info("基准快照为空，本轮仅记录数据")

            # 保存新快照
            save_state(state_path, new_snapshot)
            old_snapshot = new_snapshot

            # 等待下一轮
            if running:
                logger.debug("等待 %d 秒后下一轮...", interval)
                # 用分段 sleep 来响应退出信号
                for _ in range(interval):
                    if not running:
                        break
                    time.sleep(1)

    finally:
        logger.info("监控已停止")


def _print_summary(snapshot, offices):
    """打印当前配额摘要。"""
    if not snapshot:
        return

    # 统计各状态数量
    counts = {"quota-g": 0, "quota-y": 0, "quota-r": 0}
    for status in snapshot.values():
        if status in counts:
            counts[status] += 1

    total = sum(counts.values())
    safe_print(f"\n  当前配额状态 ({total} 条记录):")
    safe_print(f"    [G] 有名额: {counts['quota-g']}")
    safe_print(f"    [Y] 少量:   {counts['quota-y']}")
    safe_print(f"    [R] 已满:   {counts['quota-r']}")

    # 找出第一个有配额的日期
    available_dates = set()
    for (date, office, qtype), status in snapshot.items():
        if status in ("quota-g", "quota-y") and qtype == "R":
            available_dates.add(date)

    if available_dates:
        sorted_dates = sorted(available_dates, key=lambda d: tuple(map(int, d.split("/"))))
        safe_print(f"    最早可约日期: {sorted_dates[0]}")
        safe_print(f"    可约日期范围: {sorted_dates[0]} ~ {sorted_dates[-1]}")
    else:
        safe_print("    !! 当前无可用配额")
    safe_print()


def main():
    parser = argparse.ArgumentParser(
        description="香港入境处预约配额监控",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config", "-c",
        default="config.json",
        help="配置文件路径 (默认: config.json)",
    )
    parser.add_argument(
        "--once", "-1",
        action="store_true",
        help="单次运行模式",
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=None,
        help="轮询间隔秒数 (默认: 600，即 10 分钟)",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI 模式（从环境变量读取配置，不持续运行）",
    )
    parser.add_argument(
        "--state", "-s",
        default="state.json",
        help="状态文件路径 (默认: state.json)",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="安静模式，减少控制台输出",
    )

    args = parser.parse_args()

    # 加载配置
    if args.ci:
        config = {
            "api": {"svc_id": 579, "timeout": 30},
            "offices": DEFAULT_OFFICES,
            "date_range": {"start": None, "end": None},
            "retry": {"max_retries": 3, "backoff_base_seconds": 5},
            "log": {"level": "INFO", "file": "monitor.log", "max_size_mb": 10, "backup_count": 3},
            "notifications": {
                "feishu": {
                    "enabled": True,
                    "webhook_url": os.environ.get("FEISHU_WEBHOOK_URL", ""),
                },
                "email": {
                    "enabled": True,
                    "subscribers": (
                        json.loads(os.environ.get("EMAIL_SUBSCRIBERS", "[]"))
                        if os.environ.get("EMAIL_SUBSCRIBERS") else []
                    ),
                    "min_interval_minutes": 30,
                },
            },
        }
    else:
        config = load_config(args.config)

    # 设置日志
    if args.quiet:
        config.setdefault("log", {})["level"] = "WARNING"
    setup_logging(config)

    # 确定轮询间隔
    interval = args.interval or DEFAULT_INTERVAL
    if interval < 30:
        logger.warning("轮询间隔 %d 秒过短，已调整为 30 秒", interval)
        interval = 30

    # 运行
    if args.once or args.ci:
        run_once(config, args.state)
    else:
        run_loop(config, interval, args.state)


if __name__ == "__main__":
    main()
