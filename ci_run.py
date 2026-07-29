#!/usr/bin/env python3
"""CI 入口脚本 — 供 GitHub Actions 调用，负责：拉取 API → 检测变化 → 通知 → 导出数据。"""

import hashlib
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

NOTIFY_LOG = "data/notify_log.json"


def _load_json_encrypted(path):
    """读取 JSON 文件，支持加密格式和明文格式（向后兼容）。"""
    if not os.path.exists(path):
        return None

    with open(path) as f:
        data = json.load(f)

    if data and isinstance(data, dict) and data.get("enc"):
        # 加密格式
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import base64
        key = base64.b64decode(os.environ.get("ENCRYPTION_KEY", ""))
        if not key:
            logger.warning("ENCRYPTION_KEY 未配置，无法解密 %s", path)
            return None
        aes = AESGCM(key)
        raw = base64.b64decode(data["data"])
        iv, ct = raw[:12], raw[12:]
        return json.loads(aes.decrypt(iv, ct, None))

    # 明文格式（向后兼容）
    return data


def _save_json_encrypted(path, data):
    """加密保存 JSON 文件。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import base64
    key = base64.b64decode(os.environ.get("ENCRYPTION_KEY", ""))
    if key:
        aes = AESGCM(key)
        iv = os.urandom(12)
        plaintext = json.dumps(data, ensure_ascii=False).encode()
        ct = aes.encrypt(iv, plaintext, None)
        raw = iv + ct
        with open(path, "w") as f:
            json.dump({"enc": True, "data": base64.b64encode(raw).decode()}, f)
    else:
        # 无密钥时明文存储（向后兼容）
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False)


def _hash_message(msg):
    """生成消息内容的 SHA256 指纹用于去重。"""
    return hashlib.sha256(msg.encode()).hexdigest()[:16]


def _append_notify_log(entry):
    """追加一条通知日志。"""
    logs = []
    if os.path.exists(NOTIFY_LOG):
        try:
            with open(NOTIFY_LOG) as f:
                logs = json.load(f)
        except (json.JSONDecodeError, IOError):
            logs = []
    logs.append(entry)
    # 只保留最近 500 条
    if len(logs) > 500:
        logs = logs[-500:]
    with open(NOTIFY_LOG, "w") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


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

    # 记录最后更新时间（北京时间）
    import time as _time
    bj_ts = _time.time() + 8 * 3600
    bj_str = _time.strftime("%Y-%m-%d %H:%M:%S", _time.gmtime(bj_ts))
    with open("data/last_update.json", "w") as f:
        json.dump({"time": bj_str}, f)

    # ── 3. 加载上次状态，检测变化 ──
    state = load_state("state.json")
    old_snapshot = state.get("last_snapshot", {})
    is_first_run = not old_snapshot

    changes = detect_changes(old_snapshot, snapshot)

    # ── 4. 发送通知 ──
    notify_result = {"feishu": None, "email": 0, "welcome": 0}
    if is_first_run:
        logger.info("首次运行，基准快照已建立，不发送通知")
        _append_notify_log({
            "time": datetime.now().isoformat(),
            "event": "first_run",
            "summary": "首次运行，基准快照已建立"
        })
    elif has_significant_change(changes):
        message = format_changes(changes, DEFAULT_OFFICES)
        change_hash = _hash_message(message)

        # 去重：和上次通知的变化内容一样就跳过
        state_extra = state.get("_extra", {}) if state else {}
        last_hash = state_extra.get("last_notify_hash", "")
        if change_hash == last_hash:
            logger.info("检测到配额变化但内容与上次相同，跳过通知")
            _append_notify_log({
                "time": datetime.now().isoformat(),
                "event": "duplicate_skipped",
                "summary": "重复变化，跳过通知"
            })
        else:
            logger.info("检测到配额变化！")
            print(message)
            state_extra["last_notify_hash"] = change_hash

            # Feishu 通知
            app_id = os.environ.get("FEISHU_APP_ID", "")
            app_secret = os.environ.get("FEISHU_APP_SECRET", "")
            chat_id = os.environ.get("FEISHU_CHAT_ID", "")
            webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
            if app_id and app_secret and chat_id:
                ok = send_feishu_api(message, app_id, app_secret, chat_id)
                notify_result["feishu"] = "OK" if ok else "FAIL"
                logger.info("飞书通知: %s", notify_result["feishu"])
            elif webhook_url:
                ok = send_feishu_webhook(webhook_url, message)
                notify_result["feishu"] = "OK" if ok else "FAIL"
                logger.info("飞书通知: %s", notify_result["feishu"])
            else:
                notify_result["feishu"] = "skipped"

            # 邮件通知
            smtp_user = os.environ.get("SMTP_USERNAME", "")
            smtp_pass = os.environ.get("SMTP_PASSWORD", "")
            if smtp_user and smtp_pass:
                subscribers = []
                secret_subs = os.environ.get("EMAIL_SUBSCRIBERS", "")
                if secret_subs:
                    try:
                        subscribers.extend(json.loads(secret_subs))
                    except json.JSONDecodeError:
                        pass
                subs_file = "data/subscribers.json"
                web_subs = _load_json_encrypted(subs_file)
                if web_subs and isinstance(web_subs, list):
                    for addr in web_subs:
                        if addr not in subscribers:
                            subscribers.append(addr)
                if subscribers:
                    subject = f"[配额监控] {datetime.now().strftime('%m/%d %H:%M')} 有变化"
                    sent_count = 0
                    for addr in subscribers:
                        email_body = message + f"\n🔕 退订：https://quota-monitor.deng-zheyi.workers.dev/api/unsubscribe?email={addr}"
                        if send_email_smtp(addr, subject, email_body, smtp_user, smtp_pass):
                            sent_count += 1
                    logger.info("邮件通知: %d/%d", sent_count, len(subscribers))
                    notify_result["email"] = sent_count

            # 写日志
            _append_notify_log({
                "time": datetime.now().isoformat(),
                "event": "quota_change",
                "changes": len(changes.get("newly_available", [])) + len(changes.get("newly_added", [])),
                "feishu": notify_result["feishu"],
                "email": notify_result["email"],
                "summary": f"配额变化: new={len(changes.get('newly_available',[]))} added={len(changes.get('newly_added',[]))}"
            })
    else:
        logger.info("配额状态无变化")
        _append_notify_log({
            "time": datetime.now().isoformat(),
            "event": "no_change",
            "summary": "无变化"
        })

    # ── 5. 保存状态（含去重哈希）──
    save_state("state.json", snapshot, state_extra)

    # ── 6. 一次性初始化 welcomed.json ──
    _init_welcomed()

    logger.info("CI Run 完成")


def _init_welcomed():
    """一次性初始化 welcomed.json — 把现有订阅者全部标记为已欢迎。"""
    subs_file = "data/subscribers.json"
    welcomed_file = "data/welcomed.json"

    all_subs = _load_json_encrypted(subs_file)
    if not isinstance(all_subs, list):
        all_subs = []

    welcomed = _load_json_encrypted(welcomed_file)
    if not isinstance(welcomed, list):
        welcomed = []

    if not welcomed and all_subs:
        _save_json_encrypted(welcomed_file, list(all_subs))
        logger.info("welcomed.json 已初始化，%d 位现有订阅者标记为已欢迎", len(all_subs))


def _send_welcome_emails():
    """检测新订阅者并发送欢迎邮件。（由 welcome.yml 调用）"""
    smtp_user = os.environ.get("SMTP_USERNAME", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")
    if not smtp_user or not smtp_pass:
        return

    subs_file = "data/subscribers.json"
    welcomed_file = "data/welcomed.json"

    # 读取所有订阅者
    all_subs = _load_json_encrypted(subs_file)
    if not isinstance(all_subs, list):
        all_subs = []

    # 读取已欢迎列表
    welcomed = _load_json_encrypted(welcomed_file)
    if not isinstance(welcomed, list):
        welcomed = []

    # 找出新订阅者
    new_subs = [e for e in all_subs if e not in welcomed]

    if not new_subs:
        return

    welcome_body = (
        "您已成功订阅香港入境处预约配额监控！\n\n"
        "当各人事登记办事处放出新的换领身份证预约名额时，\n"
        "我们会第一时间通过邮件通知您。\n\n"
        "📊 实时看板：https://Zheyi-D.github.io/quota-monitor\n"
        "📋 预约办理：https://www.gov.hk/sc/apps/immdicbooking2.htm\n"
        "🪧 配额查询：https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/?l=zh-CN&appId=579\n"
        "📱 飞书群：https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=ff3i6631-016b-40cc-989e-e4651ccd353c\n\n"
        "⚠️ 免责声明：本系统为第三方开源工具，非香港入境事务处官方服务。\n"
        "请以官网信息为准，本系统不对数据延迟或遗漏承担责任。\n"
        "本项目仅供学习交流，请勿用于商业盈利目的。\n\n"
        "— quota-monitor"
    )

    for addr in new_subs:
        email_body = welcome_body + f"\n🔕 退订：https://quota-monitor.deng-zheyi.workers.dev/api/unsubscribe?email={addr}"
        if send_email_smtp(addr, "[quota-monitor] 订阅确认", email_body, smtp_user, smtp_pass):
            welcomed.append(addr)
            logger.info("欢迎邮件已发送 (第%d封)", len(welcomed))
        else:
            logger.warning("欢迎邮件发送失败 (第%d封)", len(welcomed) + 1)

    # 保存已欢迎列表
    if welcomed:
        _save_json_encrypted(welcomed_file, welcomed)

    # 写日志
    _append_notify_log({
        "time": datetime.now().isoformat(),
        "event": "welcome_email",
        "sent": len(welcomed),
        "total_new": len(new_subs),
        "summary": f"欢迎邮件: {len(welcomed)}/{len(new_subs)}"
    })


if __name__ == "__main__":
    main()
