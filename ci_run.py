#!/usr/bin/env python3
"""CI 入口脚本 — 供 GitHub Actions 调用，负责：拉取 API → 检测变化 → 通知 → 导出数据。"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta

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
RUN_LOG = "data/run.log"


def _append_run_log(line):
    """通过 GitHub API 追加一行到 CI 运行日志，不依赖 git push。"""
    import base64, time as _time
    bj_ts = _time.time() + 8 * 3600
    ts = _time.strftime("%Y-%m-%d %H:%M:%S BJT", _time.gmtime(bj_ts))
    new_line = f"[{ts}] {line}\n"

    try:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        api_url = f"repos/{repo}/contents/data/run.log"

        # 1. 读取已有日志
        existing = ""
        sha = None
        r = subprocess.run(["gh", "api", api_url], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            data = json.loads(r.stdout)
            existing = base64.b64decode(data["content"]).decode()
            sha = data.get("sha")
        elif "Not Found" not in r.stderr:
            logger.debug("读取 run.log 失败: %s", r.stderr[:100])

        # 2. 追加新行，保留最近 200 行
        lines = existing.splitlines(True)
        lines.append(new_line)
        if len(lines) > 200:
            lines = lines[-200:]

        # 3. 写入
        content_b64 = base64.b64encode("".join(lines).encode()).decode()
        body = {"message": "Update run log", "content": content_b64}
        if sha:
            body["sha"] = sha

        result = subprocess.run(
            ["gh", "api", "-X", "PUT", api_url, "--input", "-"],
            input=json.dumps(body), capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            logger.debug("写入 run.log 失败: %s", result.stderr[:100])
        else:
            # API 成功，同时写本地供 Pages 部署
            local_content = "".join(lines)
            with open(RUN_LOG, "w") as f:
                f.write(local_content)
    except Exception as e:
        logger.debug("run.log API 异常: %s", e)
        # API 失败时至少写本地
        try:
            with open(RUN_LOG, "w") as f:
                f.write(new_line)
        except Exception:
            pass


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


RELEASE_LOG = "data/release_log.json"


def _bj_now():
    """返回北京时间 datetime，带 +08:00 时区信息。"""
    import time as _time
    bj_ts = _time.time() + 8 * 3600  # UTC+8
    bj_t = _time.gmtime(bj_ts)
    # 构造带时区的 datetime
    from datetime import timezone, timedelta
    tz_bj = timezone(timedelta(hours=8))
    dt_utc = datetime(*bj_t[:6])
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(tz_bj)


def _append_release_log(newly_available):
    """通过 GitHub API 追加放号记录到 release_log.json，保留 60 天。"""
    import base64

    # 提取本批次放出的日期
    all_dates = []
    for (date, office, qtype), old_s, new_s in newly_available:
        if qtype == "R":
            all_dates.append(date)

    if not all_dates:
        return

    unique_dates = sorted(set(all_dates), key=lambda d: tuple(map(int, d.split("/"))))

    entry = {
        "t": _bj_now().isoformat(),
        "count": len(unique_dates),
        "dates": unique_dates,
    }

    try:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        api_url = f"repos/{repo}/contents/{RELEASE_LOG}"

        # 1. 读取已有日志
        logs = []
        sha = None
        r = subprocess.run(["gh", "api", api_url], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            data = json.loads(r.stdout)
            existing = base64.b64decode(data["content"]).decode()
            logs = json.loads(existing) if existing.strip() else []
            sha = data.get("sha")
        elif "Not Found" not in (r.stderr or ""):
            logger.debug("读取 release_log 失败: %s", r.stderr[:100])

        if not isinstance(logs, list):
            logs = []

        # 2. 插入新记录，保留 60 天
        logs.insert(0, entry)
        cutoff = datetime.now().replace(microsecond=0) - timedelta(days=60)
        logs = [e for e in logs if datetime.fromisoformat(e["t"]) > cutoff]

        # 3. 写回
        content_b64 = base64.b64encode(json.dumps(logs, ensure_ascii=False).encode()).decode()
        body = {"message": "Update release log", "content": content_b64}
        if sha:
            body["sha"] = sha

        result = subprocess.run(
            ["gh", "api", "-X", "PUT", api_url, "--input", "-"],
            input=json.dumps(body), capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            logger.info("放号记录已追加: %d 个日期 (via API)", len(unique_dates))
        else:
            logger.warning("写入 release_log 失败: %s", result.stderr[:200])

    except Exception as e:
        logger.warning("release_log API 异常: %s", e)


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
        _append_run_log("INIT | 首次运行，基准快照已建立")
        _append_notify_log({
            "time": datetime.now().isoformat(),
            "event": "first_run",
            "summary": "首次运行，基准快照已建立"
        })
    elif has_significant_change(changes):
        message = format_changes(changes, DEFAULT_OFFICES)
        logger.info("检测到配额变化！")
        print(message)
        _append_run_log(f"ALERT | 新配额放出: {len(changes.get('newly_available',[]))} 个")

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
                    bj_now = _time.strftime("%m/%d %H:%M", _time.gmtime(_time.time() + 8 * 3600))
                    subject = f"[配额监控] {bj_now} 有变化"
                    sent_count = 0
                    for addr in subscribers:
                        email_body = _email_html("🔔 新预约配额放出！", message) + _email_footer(addr)
                        if send_email_smtp(addr, subject, email_body, smtp_user, smtp_pass):
                            sent_count += 1
                    logger.info("邮件通知: %d/%d", sent_count, len(subscribers))
                    notify_result["email"] = sent_count

            # 写日志
            _append_notify_log({
                "time": datetime.now().isoformat(),
                "event": "quota_change",
                "changes": len(changes.get("newly_available", [])),
                "feishu": notify_result["feishu"],
                "email": notify_result["email"],
                "summary": f"配额变化: {len(changes.get('newly_available',[]))} 个日期"
            })

            # 记录放号到 release_log.json（仅 newly_available，忽略自动滚动的新日期）
            if changes.get("newly_available"):
                _append_release_log(changes["newly_available"])

    else:
        logger.info("配额状态无变化")
        _append_run_log("OK | 配额状态无变化")
        _append_notify_log({
            "time": datetime.now().isoformat(),
            "event": "no_change",
            "summary": "无变化"
        })

    # ── 5. 保存状态 ──
    save_state("state.json", snapshot)

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

    welcome_body = _email_html(
        "您已成功订阅香港入境处预约配额监控！",
        "当各人事登记办事处放出新的换领身份证预约名额时，我们会第一时间通过邮件通知您。",
    )

    for addr in new_subs:
        email_body = welcome_body + _email_footer(addr)
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


# ─── HTML Email Templates ───────────────────────────────────

QR_URL = "https://Zheyi-D.github.io/quota-monitor/feishu-qr.jpg"
DASHBOARD_URL = "https://Zheyi-D.github.io/quota-monitor"
BOOKING_URL = "https://www.gov.hk/sc/apps/immdicbooking2.htm"
FS_GROUP_URL = "https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=49ar968e-150c-4e7f-bae4-95cae408033b"
UNSUB_BASE = "https://quota-monitor.deng-zheyi.workers.dev/api/unsubscribe?email="


def _email_html(title, body_text):
    """生成带二维码的 HTML 邮件。"""
    return f"""\
<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#333">
<h2 style="color:#1a73e8">{title}</h2>
<p style="font-size:15px;line-height:1.8">{body_text.replace(chr(10),'<br>')}</p>
<hr style="border:none;border-top:1px solid #ddd;margin:20px 0">
<table cellpadding="8"><tr>
<td style="vertical-align:top;padding-right:16px">
<b>🔗 快速入口</b><br><br>
📊 <a href="{DASHBOARD_URL}" style="color:#1a73e8">实时看板</a><br>
📋 <a href="{BOOKING_URL}" style="color:#1a73e8">预约办理</a><br>
📱 <a href="{FS_GROUP_URL}" style="color:#1a73e8">加入飞书群</a>
</td>
<td style="text-align:center;vertical-align:top">
<b>📱 扫码加飞书群</b><br>
<img src="{QR_URL}" width="120" height="120" style="border-radius:8px;margin-top:4px" alt="飞书群二维码">
</td>
</tr></table>
<p style="font-size:12px;color:#999;margin-top:16px">⚠️ 免责声明：本系统为第三方开源工具，非香港入境事务处官方服务。请以官网信息为准。本项目仅供学习交流，请勿用于商业盈利目的。</p>
</body></html>"""


def _email_footer(email_addr):
    """邮件底部退订链接。"""
    return f'<p style="font-size:12px;color:#aaa;margin-top:20px;border-top:1px solid #eee;padding-top:12px">不想再收到此类邮件？<a href="{UNSUB_BASE}{email_addr}" style="color:#aaa">一键退订</a></p>'


if __name__ == "__main__":
    main()
