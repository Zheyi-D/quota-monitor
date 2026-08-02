#!/usr/bin/env python3
"""CI 入口脚本 — 供 GitHub Actions 调用，负责：拉取 API → 检测变化 → 通知 → 导出数据。"""

import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from quota_monitor.notify import send_feishu_api, send_feishu_dm, send_feishu_webhook
from quota_monitor.state import load_state

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

        # 2. 追加新行，保留最近 10000 行
        lines = existing.splitlines(True)
        lines.append(new_line)
        if len(lines) > 10000:
            lines = lines[-10000:]

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


def _save_state_remote(state_file, snapshot, state_extra=None):
    """通过 GitHub API 保存 state.json，不依赖 git push。"""
    import base64 as _b64, time as _time

    serializable_snapshot = {}
    for key, status in snapshot.items():
        serializable_snapshot["|".join(key)] = status

    state = {
        "version": 1,
        "last_snapshot": serializable_snapshot,
        "last_snapshot_time": _time.strftime(
            "%Y-%m-%dT%H:%M:%S", _time.gmtime(_time.time() + 8 * 3600)
        ),
    }
    if state_extra:
        state.update(state_extra)

    content = json.dumps(state, ensure_ascii=False, indent=2)
    content_b64 = _b64.b64encode(content.encode()).decode()

    try:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        api_url = f"repos/{repo}/contents/{state_file}"

        r = subprocess.run(["gh", "api", api_url], capture_output=True, text=True, timeout=10)
        sha = None
        if r.returncode == 0:
            sha = json.loads(r.stdout).get("sha")

        body = {"message": "Update state", "content": content_b64}
        if sha:
            body["sha"] = sha

        result = subprocess.run(
            ["gh", "api", "-X", "PUT", api_url, "--input", "-"],
            input=json.dumps(body), capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            logger.debug("state.json 已通过 API 写入 GitHub")
        else:
            logger.debug("state.json API 写入失败: %s", result.stderr[:100])
    except Exception as e:
        logger.debug("state.json API 异常: %s", e)

    # 始终写本地文件
    with open(state_file, "w", encoding="utf-8") as f:
        f.write(content)


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
    notify_result = {"feishu": None, "feishu_dm": 0}
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

        # Feishu 群聊广播（支持多群：逗号分隔 chat_id）
        app_id = os.environ.get("FEISHU_APP_ID", "")
        app_secret = os.environ.get("FEISHU_APP_SECRET", "")
        chat_ids_raw = os.environ.get("FEISHU_CHAT_ID", "")
        webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
        if app_id and app_secret and chat_ids_raw:
            chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]
            ok_all = True
            with ThreadPoolExecutor(max_workers=len(chat_ids)) as pool:
                futures = {pool.submit(send_feishu_api, message, app_id, app_secret, cid): cid for cid in chat_ids}
                for f in as_completed(futures):
                    if not f.result(): ok_all = False
            notify_result["feishu"] = "OK" if ok_all else "PARTIAL"
            logger.info("飞书群聊通知: %s (%d群)", notify_result["feishu"], len(chat_ids))
        elif webhook_url:
            ok = send_feishu_webhook(webhook_url, message)
            notify_result["feishu"] = "OK" if ok else "FAIL"
            logger.info("飞书通知: %s", notify_result["feishu"])
        else:
            notify_result["feishu"] = "skipped"

        # Feishu DM 按日期过滤通知（在邮件之前，避免被慢速SMTP阻塞）
        if app_id and app_secret:
            feishu_subs = _load_json_encrypted("data/feishu_subs.json")
            if feishu_subs and isinstance(feishu_subs, list) and feishu_subs:
                released_dates = {date for (date, _, _), _, _ in changes.get("newly_available", [])}
                dms_to_send = []
                for sub in feishu_subs:
                    open_id = sub.get("open_id", "")
                    user_dates = sub.get("dates") or []
                    user_offices = sub.get("offices") or []
                    if not open_id:
                        continue
                    date_match = (not user_dates or any(d in released_dates for d in user_dates))
                    if not date_match:
                        continue
                    dm_lines = ["## 🔔 你关注的日期有新增配额！\n"]
                    for (date, office, qtype), old_s, new_s in changes["newly_available"]:
                        if user_dates and date not in user_dates:
                            continue
                        if user_offices and office not in user_offices:
                            continue
                        office_name = DEFAULT_OFFICES.get(office, office)
                        dm_lines.append(f"  • {date}  {office_name}({office})")
                    if len(dm_lines) == 1:
                        continue  # no matching changes for this user
                    dm_lines.append(f"\n📋 [预约办理]({BOOKING_URL}) ｜ 📊 [实时看板]({DASHBOARD_URL})")
                    dms_to_send.append((open_id, "\n".join(dm_lines)))

                dm_sent = 0
                if dms_to_send:
                    def _send_one(oid, text):
                        try:
                            return send_feishu_dm(text, app_id, app_secret, oid)
                        except Exception as e:
                            logger.warning("飞书 DM 发送失败 open_id=%s: %s", oid[:16], e)
                            return False
                    with ThreadPoolExecutor(max_workers=min(len(dms_to_send), 5)) as pool:
                        futures = {pool.submit(_send_one, oid, text): oid for oid, text in dms_to_send}
                        for f in as_completed(futures):
                            if f.result(): dm_sent += 1
                if dm_sent > 0:
                    logger.info("飞书 DM 通知: %d/%d", dm_sent, len(feishu_subs))
                notify_result["feishu_dm"] = dm_sent

        # 邮件通知已下架

        # 写日志
        _append_notify_log({
            "time": datetime.now().isoformat(),
            "event": "quota_change",
            "changes": len(changes.get("newly_available", [])),
            "feishu": notify_result["feishu"],
            "feishu_dm": notify_result.get("feishu_dm", 0),
            "summary": f"配额变化: {len(changes.get('newly_available',[]))} 个日期"
        })

    else:
        logger.info("配额状态无变化")
        _append_run_log("OK | 配额状态无变化")
        _append_notify_log({
            "time": datetime.now().isoformat(),
            "event": "no_change",
            "summary": "无变化"
        })

    # ── 5. 保存状态（通过 GitHub API 直接写入，避免 git push 不可靠导致重复通知）──
    _save_state_remote("state.json", snapshot)

    logger.info("CI Run 完成")


# ─── URL Constants (used by DM) ───────────────────────────────────

DASHBOARD_URL = "https://Zheyi-D.github.io/quota-monitor"
BOOKING_URL = "https://www.gov.hk/sc/apps/immdicbooking2.htm"


if __name__ == "__main__":
    main()
