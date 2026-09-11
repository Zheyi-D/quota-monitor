#!/usr/bin/env python3
"""CI 入口脚本 — 供 GitHub Actions 调用，负责：拉取 API → 检测变化 → 通知 → 导出数据。"""

import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

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
from quota_monitor.notify import (
    send_feishu_api,
    send_feishu_dm,
    send_feishu_webhook,
    send_wecom_webhook,
)
from quota_monitor.notification_state import filter_repeat_releases
from quota_monitor.notification_window import filter_notification_window
from quota_monitor.release_sink import build_release_signal, deliver_outbox
from quota_monitor.state import load_state
from quota_monitor.wecom_outbox import enqueue as enqueue_wecom, deliver as deliver_wecom

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("ci_run")

NOTIFY_LOG = "data/notify_log.json"
RUN_LOG = "data/run.log"
RELEASE_LOG = "data/release_log.json"
RELEASE_LOG_MAX_EVENTS = 2000
HONG_KONG_TZ = timezone(timedelta(hours=8))
DEFAULT_POLL_INTERVAL_SECONDS = 30
MAX_POLL_INTERVAL_SECONDS = 60
MAX_POLL_ITERATIONS = 2
DEFAULT_NOTIFICATION_REARM_SECONDS = 0
MAX_NOTIFICATION_REARM_SECONDS = 86400
HUMAN_NOTIFICATION_ENV_VARS = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_CHAT_ID",
    "FEISHU_WEBHOOK_URL",
    "WECOM_WEBHOOK_URL",
)


def _apply_notification_pause():
    """暂停人工消息通道，同时保留监控、看板和 ReleaseSignal。"""
    paused = os.environ.get("NOTIFICATIONS_PAUSED", "").strip().lower()
    if paused not in ("1", "true", "yes", "on"):
        return False

    for name in HUMAN_NOTIFICATION_ENV_VARS:
        os.environ.pop(name, None)
    logger.warning("人工通知已通过 NOTIFICATIONS_PAUSED 暂停")
    return True


def _wecom_webhook_urls():
    """读取企业微信 Webhook；支持用逗号或换行分隔多个群。"""
    if os.environ.get("QUOTA_WECOM_SOURCE", "legacy").strip() != "legacy":
        return []  # Only quota WeCom is disabled; other task responsibilities remain.
    raw = os.environ.get("WECOM_WEBHOOK_URL", "")
    urls = [item.strip() for item in raw.replace("\r", "\n").replace("\n", ",").split(",")]
    return list(dict.fromkeys(item for item in urls if item))


def _format_wecom_message(message):
    """替换本仓库看板链接，并移除“加群方式”及其后的推广信息。"""
    message = message.replace(
        "https://Zheyi-D.github.io/quota-monitor",
        "https://g2867082586-boop.github.io/quota-monitor/",
    )
    kept_lines = []
    for line in message.splitlines():
        if line.lstrip().startswith("📖 加群方式"):
            break
        kept_lines.append(line)
    return "\n".join(kept_lines).rstrip()


def _send_wecom_broadcast(message):
    """并行发送企业微信群通知，返回 (状态, 目标群数)。"""
    webhook_urls = _wecom_webhook_urls()
    if not webhook_urls:
        return "skipped", 0

    message = _format_wecom_message(message)
    succeeded = 0
    with ThreadPoolExecutor(max_workers=min(len(webhook_urls), 5)) as pool:
        futures = {
            pool.submit(send_wecom_webhook, url, message): url
            for url in webhook_urls
        }
        for future in as_completed(futures):
            if future.result():
                succeeded += 1

    if succeeded == len(webhook_urls):
        return "OK", len(webhook_urls)
    if succeeded:
        return "PARTIAL", len(webhook_urls)
    return "FAIL", len(webhook_urls)


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


def _record_release_event(newly_available, path=RELEASE_LOG, now=None):
    """将本仓库捕获的放号事件写入独立 JSON，供实时看板使用。"""
    if not newly_available:
        return None

    event_time = now or datetime.now(HONG_KONG_TZ).replace(microsecond=0).isoformat()
    data = {
        "version": 1,
        "monitoring_since": event_time,
        "events": [],
    }

    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            data.update(loaded)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        logger.warning("放号历史不存在或损坏，将从当前事件重新建立")

    if not isinstance(data.get("events"), list):
        data["events"] = []
    if not data.get("monitoring_since"):
        data["monitoring_since"] = event_time

    event = {
        "time": event_time,
        "count": len(newly_available),
        "items": [
            {
                "date": date,
                "office": office,
                "quota_type": quota_type,
                "status": new_status,
            }
            for (date, office, quota_type), _old_status, new_status in newly_available
        ],
    }
    data["version"] = 1
    data["events"].append(event)
    data["events"] = data["events"][-RELEASE_LOG_MAX_EVENTS:]

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary_path = path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temporary_path, path)
    return event


def _record_dashboard_release(changes, is_first_run, path=RELEASE_LOG, now=None):
    """记录全部去重后的放号事件，不受群消息的两周窗口限制。"""
    newly_available = changes.get("newly_available", [])
    if is_first_run or not newly_available:
        return None
    return _record_release_event(newly_available, path=path, now=now)


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
    persisted = False

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
            persisted = True
            logger.debug("state.json 已通过 API 写入 GitHub")
        else:
            logger.debug("state.json API 写入失败: %s", result.stderr[:100])
    except Exception as e:
        logger.debug("state.json API 异常: %s", e)

    # 始终写本地文件
    with open(state_file, "w", encoding="utf-8") as f:
        f.write(content)
    return persisted


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


def _run_poll_cycle(log_no_change=True):
    """执行一次配额检查；中间轮询可省略无变化日志以减少 API 写入。"""
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
    notification_episodes = state.get("notification_episodes", {})
    changes, notification_episodes = filter_repeat_releases(
        changes,
        snapshot,
        notification_episodes,
        rearm_seconds=_notification_rearm_seconds(),
    )

    # ReleaseSignal carries public quota rows only. It is a wake-up hint; the
    # receiving project independently rechecks the official source.
    pending_release_signals = state.get("pending_release_signals", [])
    if not isinstance(pending_release_signals, list):
        logger.warning("ReleaseSignal outbox 格式无效，已安全清空")
        pending_release_signals = []
    else:
        pending_release_signals = [
            item for item in pending_release_signals if isinstance(item, dict)
        ]
    release_url = os.environ.get("HKID_RELEASE_WEBHOOK_URL", "").strip()
    release_secret = os.environ.get("HKID_RELEASE_WEBHOOK_SECRET", "")
    newly_available = changes.get("newly_available", [])
    # The public dashboard tracks every deduplicated release detected by this
    # fork. Human messages remain restricted to appointments in the next two
    # weeks below, but that delivery policy must not erase dashboard history.
    _record_dashboard_release(changes, is_first_run)

    if not is_first_run and newly_available and release_url and release_secret:
        signal = build_release_signal(newly_available)
        known_ids = {
            item.get("event_id")
            for item in pending_release_signals
            if isinstance(item, dict)
        }
        if signal["event_id"] not in known_ids:
            pending_release_signals.append(signal)

    # Human-facing messages are intentionally narrower than the internal
    # ReleaseSignal bridge. Only appointments within the next two weeks are
    # rendered or delivered to Feishu/WeCom subscribers.
    push_changes = filter_notification_window(
        changes,
        today=datetime.now(HONG_KONG_TZ).date(),
    )

    # ── 4. 发送通知 ──
    notify_result = {"feishu": None, "feishu_dm": 0, "wecom": None}
    if is_first_run:
        logger.info("首次运行，基准快照已建立，不发送通知")
        _append_run_log("INIT | 首次运行，基准快照已建立")
        _append_notify_log({
            "time": datetime.now().isoformat(),
            "event": "first_run",
            "summary": "首次运行，基准快照已建立"
        })
    elif has_significant_change(push_changes):
        message = format_changes(push_changes, DEFAULT_OFFICES)
        logger.info("检测到配额变化！")
        print(message)
        _append_run_log(f"ALERT | 两周内新配额放出: {len(push_changes.get('newly_available',[]))} 个")

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

        # 企业微信群机器人广播（与飞书配置相互独立）
        wecom_status, wecom_target_count = "queued", len(_wecom_webhook_urls())
        notify_result["wecom"] = wecom_status
        logger.info("企业微信群通知: %s (%d群)", wecom_status, wecom_target_count)

        # Feishu DM 按日期过滤通知（在邮件之前，避免被慢速SMTP阻塞）
        if app_id and app_secret:
            feishu_subs = _load_json_encrypted("data/feishu_subs.json")
            if feishu_subs and isinstance(feishu_subs, list) and feishu_subs:
                released_dates = {date for (date, _, _), _, _ in push_changes.get("newly_available", [])}
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
                    for (date, office, qtype), old_s, new_s in push_changes["newly_available"]:
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
            "changes": len(push_changes.get("newly_available", [])),
            "feishu": notify_result["feishu"],
            "feishu_dm": notify_result.get("feishu_dm", 0),
            "wecom": notify_result["wecom"],
            "summary": f"两周内配额变化: {len(push_changes.get('newly_available',[]))} 个日期"
        })

    elif has_significant_change(changes):
        logger.info("新增配额均不在未来两周内，跳过群消息推送")
        if log_no_change:
            _append_run_log(
                "SKIP | 新增配额不在未来两周内: "
                f"{len(changes.get('newly_available', []))} 个"
            )
            _append_notify_log({
                "time": datetime.now().isoformat(),
                "event": "quota_change_outside_notification_window",
                "summary": "新增配额不在未来两周内，未推送"
            })

    else:
        logger.info("配额状态无变化")
        if log_no_change:
            _append_run_log("OK | 配额状态无变化")
            _append_notify_log({
                "time": datetime.now().isoformat(),
                "event": "no_change",
                "summary": "无变化"
            })

    # ── 5. 投递无 PII ReleaseSignal outbox；失败不影响飞书通知或快照更新 ──
    if pending_release_signals and release_url and release_secret:
        try:
            pending_release_signals, rejected_ids = deliver_outbox(
                pending_release_signals,
                url=release_url,
                secret=release_secret,
                timeout_seconds=float(
                    os.environ.get("HKID_RELEASE_WEBHOOK_TIMEOUT_SECONDS", "10")
                ),
                max_attempts=int(os.environ.get("HKID_RELEASE_WEBHOOK_MAX_ATTEMPTS", "3")),
                backoff_seconds=float(
                    os.environ.get("HKID_RELEASE_WEBHOOK_BACKOFF_SECONDS", "1")
                ),
            )
        except ValueError:
            logger.error("ReleaseSignal 配置无效，事件保留在 outbox")
        else:
            if rejected_ids:
                logger.error("ReleaseSignal 被永久拒绝: %d 个事件", len(rejected_ids))
                _append_run_log(
                    "ERROR | ReleaseSignal permanent rejection: " + str(len(rejected_ids))
                )
            if pending_release_signals:
                logger.warning("ReleaseSignal 待重试: %d 个事件", len(pending_release_signals))

    # New detections only: never reconstruct old notifications from the snapshot.
    legacy_wecom = os.environ.get("QUOTA_WECOM_SOURCE", "legacy").strip() == "legacy"
    # A source switch is not a historical backlog replay. Keep discovery/dedup state.
    prior_wecom = state.get("pending_wecom", []) if legacy_wecom else []
    pending_wecom = enqueue_wecom(prior_wecom, push_changes,
        now=time.time(), enabled=not is_first_run and bool(_wecom_webhook_urls()))
    def persist_wecom(items):
        return _save_state_remote("state.json", snapshot, state_extra={
            "pending_wecom": items, "pending_release_signals": pending_release_signals,
            "notification_episodes": notification_episodes})
    pending_wecom, delivery = deliver_wecom(pending_wecom, snapshot, _wecom_webhook_urls(),
        now=time.time(), format_message=lambda changes: _format_wecom_message(format_changes(changes, DEFAULT_OFFICES)),
        send=send_wecom_webhook, persist=persist_wecom)
    logger.info("WECOM_OUTBOX status=%s pending=%d", delivery, len(pending_wecom))
    _append_notify_log({"time": datetime.now(HONG_KONG_TZ).isoformat(),
        "event": "wecom_delivery", "wecom": delivery, "pending": len(pending_wecom)})

    # ── 6. 保存状态（通过 GitHub API 直接写入，outbox 可跨运行恢复）──
    _save_state_remote(
        "state.json",
        snapshot,
        state_extra={
            "pending_release_signals": pending_release_signals,
            "pending_wecom": pending_wecom,
            "notification_episodes": notification_episodes,
        },
    )

    logger.info("本轮配额检查完成")


def _poll_settings():
    """读取并校验轮询设置，限制为一次任务最多检查两次。"""
    try:
        iterations = int(os.environ.get("POLL_ITERATIONS", "1"))
    except ValueError:
        iterations = 1
        logger.warning("POLL_ITERATIONS 无效，回退为 1")
    if not 1 <= iterations <= MAX_POLL_ITERATIONS:
        logger.warning(
            "POLL_ITERATIONS 必须在 1-%d 之间，回退为 1",
            MAX_POLL_ITERATIONS,
        )
        iterations = 1

    try:
        interval_seconds = int(
            os.environ.get(
                "POLL_INTERVAL_SECONDS", str(DEFAULT_POLL_INTERVAL_SECONDS)
            )
        )
    except ValueError:
        interval_seconds = DEFAULT_POLL_INTERVAL_SECONDS
        logger.warning(
            "POLL_INTERVAL_SECONDS 无效，回退为 %d",
            DEFAULT_POLL_INTERVAL_SECONDS,
        )
    if not 0 <= interval_seconds <= MAX_POLL_INTERVAL_SECONDS:
        logger.warning(
            "POLL_INTERVAL_SECONDS 必须在 0-%d 之间，回退为 %d",
            MAX_POLL_INTERVAL_SECONDS,
            DEFAULT_POLL_INTERVAL_SECONDS,
        )
        interval_seconds = DEFAULT_POLL_INTERVAL_SECONDS

    return iterations, interval_seconds


def _notification_rearm_seconds():
    """Return the continuous-unavailability window required before re-alerting."""

    try:
        seconds = int(
            os.environ.get(
                "NOTIFICATION_REARM_SECONDS",
                str(DEFAULT_NOTIFICATION_REARM_SECONDS),
            )
        )
    except ValueError:
        seconds = DEFAULT_NOTIFICATION_REARM_SECONDS
        logger.warning(
            "NOTIFICATION_REARM_SECONDS invalid; using %d",
            DEFAULT_NOTIFICATION_REARM_SECONDS,
        )
    if not 0 <= seconds <= MAX_NOTIFICATION_REARM_SECONDS:
        logger.warning(
            "NOTIFICATION_REARM_SECONDS must be between 0 and %d; using %d",
            MAX_NOTIFICATION_REARM_SECONDS,
            DEFAULT_NOTIFICATION_REARM_SECONDS,
        )
        seconds = DEFAULT_NOTIFICATION_REARM_SECONDS
    return seconds


def main():
    logger.info("CI Run — %s", datetime.now().isoformat())
    notifications_paused = _apply_notification_pause()

    # workflow_dispatch 可只测试企业微信，不抓取或改写配额状态。
    if os.environ.get("WECOM_TEST_ONLY", "").lower() in ("1", "true", "yes"):
        if notifications_paused:
            logger.info("人工通知当前已暂停，跳过企业微信测试消息")
            return
        status, target_count = _send_wecom_broadcast(
            "企业微信群机器人连接测试成功。\n\n"
            "如果你看到了这条消息，GitHub Actions Secret 已配置正确。"
        )
        logger.info("企业微信测试通知: %s (%d群)", status, target_count)
        if status != "OK":
            sys.exit(1)
        return

    iterations, interval_seconds = _poll_settings()
    for index in range(iterations):
        logger.info("开始配额检查 %d/%d", index + 1, iterations)
        _run_poll_cycle(log_no_change=index == iterations - 1)
        if index < iterations - 1:
            logger.info("等待 %d 秒后进行下一次检查", interval_seconds)
            time.sleep(interval_seconds)

    logger.info("CI Run 完成")


# ─── URL Constants (used by DM) ───────────────────────────────────

DASHBOARD_URL = "https://g2867082586-boop.github.io/quota-monitor/"
BOOKING_URL = "https://www.gov.hk/sc/apps/immdicbooking2.htm"


if __name__ == "__main__":
    main()
