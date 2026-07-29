#!/usr/bin/env python3
"""CI 入口脚本 — 供 GitHub Actions 调用，负责：拉取 API → 检测变化 → 通知 → 导出数据。"""

import json
import logging
import os
import sys
from datetime import datetime

# 确保模块可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from quota_monitor.core import (
    DEFAULT_OFFICES,
    detect_changes,
    export_web_data,
    fetch_snapshot,
    format_changes,
    has_significant_change,
)
from quota_monitor.notify import send_email_smtp, send_feishu_api, send_feishu_webhook
from quota_monitor.state import load_state, save_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("ci_run")


def main():
    logger.info("CI Run — %s", datetime.now().isoformat())

    # ── 1. 拉取 API ──
    logger.info("拉取配额数据...")
    snapshot = fetch_snapshot()
    if not snapshot:
        logger.error("无法获取配额数据，退出")
        sys.exit(1)

    logger.info("成功拉取 %d 条记录", len(snapshot))

    # ── 2. 导出 web 数据 ──
    export_web_data(snapshot, "data/quota.json")

    # ── 3. 加载上次状态，检测变化 ──
    state = load_state("state.json")
    old_snapshot = state.get("last_snapshot", {})
    is_first_run = not old_snapshot

    changes = detect_changes(old_snapshot, snapshot)

    # ── 4. 保存新状态 ──
    save_state("state.json", snapshot)

    # ── 5. 发送通知 ──
    if is_first_run:
        logger.info("首次运行，基准快照已建立，不发送通知")
    elif has_significant_change(changes):
        message = format_changes(changes, DEFAULT_OFFICES)
        logger.info("检测到配额变化！")
        print(message)

        # Feishu — API 模式优先（自建应用）
        app_id = os.environ.get("FEISHU_APP_ID", "")
        app_secret = os.environ.get("FEISHU_APP_SECRET", "")
        chat_id = os.environ.get("FEISHU_CHAT_ID", "")
        webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")

        if app_id and app_secret and chat_id:
            ok = send_feishu_api(message, app_id, app_secret, chat_id)
            logger.info("飞书通知 (API): %s", "OK" if ok else "FAIL")
        elif webhook_url:
            ok = send_feishu_webhook(webhook_url, message)
            logger.info("飞书通知 (webhook): %s", "OK" if ok else "FAIL")
        else:
            logger.info("未配置飞书通知，跳过")

        # Email via QQ SMTP
        smtp_user = os.environ.get("SMTP_USERNAME", "")
        smtp_pass = os.environ.get("SMTP_PASSWORD", "")
        if smtp_user and smtp_pass:
            # 合并 Secrets 订阅者 + 网页自助订阅者
            subscribers = []
            secret_subs = os.environ.get("EMAIL_SUBSCRIBERS", "")
            if secret_subs:
                try:
                    subscribers.extend(json.loads(secret_subs))
                except json.JSONDecodeError:
                    logger.warning("EMAIL_SUBSCRIBERS JSON 解析失败")

            # 读取网页自助订阅者
            subs_file = "data/subscribers.json"
            if os.path.exists(subs_file):
                try:
                    with open(subs_file) as f:
                        web_subs = json.load(f)
                        for addr in web_subs:
                            if addr not in subscribers:
                                subscribers.append(addr)
                except (json.JSONDecodeError, IOError):
                    logger.warning("subscribers.json 读取失败")

            if subscribers:
                subject = f"[配额监控] {datetime.now().strftime('%m/%d %H:%M')} 有变化"
                sent_count = 0
                for addr in subscribers:
                    if send_email_smtp(addr, subject, message, smtp_user, smtp_pass):
                        sent_count += 1
                logger.info("邮件通知: %d/%d 封发送成功", sent_count, len(subscribers))
            else:
                logger.info("无邮件订阅者，跳过邮件通知")
    else:
        logger.info("配额状态无变化")

    # ── 6. 发送欢迎邮件给新订阅者 ──
    _send_welcome_emails()

    logger.info("CI Run 完成")


def _send_welcome_emails():
    """检测新订阅者并发送欢迎邮件。"""
    smtp_user = os.environ.get("SMTP_USERNAME", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")
    if not smtp_user or not smtp_pass:
        return

    subs_file = "data/subscribers.json"
    welcomed_file = "data/welcomed.json"

    # 读取所有订阅者
    all_subs = []
    if os.path.exists(subs_file):
        try:
            with open(subs_file) as f:
                all_subs = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # 读取已欢迎列表
    welcomed = []
    if os.path.exists(welcomed_file):
        try:
            with open(welcomed_file) as f:
                welcomed = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # 找出新订阅者
    new_subs = [e for e in all_subs if e not in welcomed]

    if not new_subs:
        return

    welcome_body = (
        "你已成功订阅香港入境处预约配额监控！\n\n"
        "当各人事登记办事处放出新的换领身份证预约名额时，\n"
        "我们会第一时间通过邮件通知你。\n\n"
        "📊 实时看板：https://Zheyi-D.github.io/quota-monitor\n"
        "📋 预约办理：https://www.gov.hk/sc/apps/immdicbooking2.htm\n"
        "📱 飞书群：https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=ff3i6631-016b-40cc-989e-e4651ccd353c\n\n"
        "— quota-monitor"
    )

    for addr in new_subs:
        if send_email_smtp(addr, "[quota-monitor] 订阅确认", welcome_body, smtp_user, smtp_pass):
            welcomed.append(addr)
            logger.info("欢迎邮件已发送: %s", addr)
        else:
            logger.warning("欢迎邮件发送失败: %s", addr)

    # 保存已欢迎列表
    if welcomed:
        with open(welcomed_file, "w") as f:
            json.dump(welcomed, f)


if __name__ == "__main__":
    main()
