"""通知模块 — 飞书 Webhook + 邮件 (agently-cli) 双通道通知。

在 CI (GitHub Actions) 中通过环境变量获取配置：
  - FEISHU_WEBHOOK_URL: 飞书群机器人 webhook URL
  - EMAIL_SUBSCRIBERS: JSON 数组，邮件订阅者列表

本地运行时通过 config.json 配置。
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timedelta

import requests

logger = logging.getLogger("quota_monitor")

# 频率控制的最小间隔（秒）
DEFAULT_MIN_INTERVAL = {
    "feishu": 600,   # 10 分钟
    "email": 1800,   # 30 分钟
}

# 每日邮件上限
DEFAULT_MAX_EMAIL_DAILY = 45

# 本地状态文件（用于频率控制）
_NOTIFY_STATE_FILE = "notify_state.json"


# ─── 飞书通知 ────────────────────────────────────────────────────

# 飞书 API 端点
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


def _get_tenant_access_token(app_id, app_secret):
    """获取飞书 tenant_access_token（缓存 1.5 小时）。"""
    resp = requests.post(FEISHU_TOKEN_URL, json={
        "app_id": app_id,
        "app_secret": app_secret,
    }, timeout=15)
    if resp.status_code != 200:
        logger.error("获取飞书 token 失败: HTTP %d", resp.status_code)
        return None

    body = resp.json()
    if body.get("code") != 0:
        logger.error("获取飞书 token 失败: code=%d msg=%s",
                     body.get("code"), body.get("msg"))
        return None

    return body["tenant_access_token"]


def send_feishu_api(text, app_id=None, app_secret=None, chat_id=None,
                    title="🔔 香港入境处预约配额监控"):
    """通过飞书自建应用 API 发送消息卡片到指定群聊。

    Args:
        text: 消息正文（Markdown）
        app_id: 飞书应用 App ID
        app_secret: 飞书应用 App Secret
        chat_id: 目标群聊的 chat_id
        title: 卡片标题

    Returns:
        bool: 是否发送成功
    """
    if not app_id or not app_secret:
        app_id = os.environ.get("FEISHU_APP_ID", "")
        app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if not chat_id:
        chat_id = os.environ.get("FEISHU_CHAT_ID", "")

    if not all([app_id, app_secret, chat_id]):
        logger.warning("飞书 API 配置不完整 (需要 APP_ID, APP_SECRET, CHAT_ID)，跳过发送")
        return False

    # 获取 token
    token = _get_tenant_access_token(app_id, app_secret)
    if not token:
        return False

    # 构造消息卡片
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps({
            "header": {
                "title": {"content": title, "tag": "plain_text"},
                "template": "green",
            },
            "elements": [
                {"tag": "markdown", "content": text},
            ],
        }),
    }

    try:
        resp = requests.post(
            FEISHU_MSG_URL,
            params={"receive_id_type": "chat_id"},
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            body = resp.json()
            if body.get("code") == 0:
                logger.info("飞书消息发送成功 (API 模式)")
                return True
            else:
                logger.error("飞书 API 返回错误: code=%d, msg=%s",
                             body.get("code"), body.get("msg"))
                return False
        else:
            logger.error("飞书 API HTTP %d: %s", resp.status_code, resp.text[:200])
            return False
    except requests.Timeout:
        logger.error("飞书 API 请求超时")
        return False
    except Exception as e:
        logger.error("飞书 API 异常: %s", e)
        return False


def send_feishu_webhook(webhook_url, text, title="🔔 香港入境处预约配额监控"):
    """通过飞书 Webhook 发送消息卡片到群聊（群自定义机器人）。

    Args:
        webhook_url: 飞书自定义机器人 Webhook URL
        text: 消息正文（Markdown）
        title: 卡片标题

    Returns:
        bool: 是否发送成功
    """
    if not webhook_url:
        logger.warning("飞书 webhook URL 未配置，跳过发送")
        return False

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"content": title, "tag": "plain_text"},
                "template": "green",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": text,
                }
            ],
        },
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        if resp.status_code == 200:
            body = resp.json()
            if body.get("code") == 0:
                logger.info("飞书消息发送成功 (webhook 模式)")
                return True
            else:
                logger.error("飞书 API 返回错误: code=%d, msg=%s",
                             body.get("code"), body.get("msg"))
                return False
        else:
            logger.error("飞书 webhook HTTP %d: %s", resp.status_code, resp.text[:200])
            return False
    except requests.Timeout:
        logger.error("飞书 webhook 请求超时")
        return False
    except Exception as e:
        logger.error("飞书 webhook 异常: %s", e)
        return False


# ─── 邮件 (QQ SMTP 优先，agently-cli 本地回退) ──────────────────

# QQ 邮箱 SMTP 配置
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 587


def send_email_smtp(to, subject, body, username=None, password=None):
    """通过 QQ SMTP 发送邮件（推荐，CI 友好，500封/天）。

    Args:
        to: 收件人邮箱
        subject: 邮件主题
        body: 邮件正文（纯文本）
        username: QQ 邮箱地址，为 None 时从环境变量 SMTP_USERNAME 读取
        password: QQ SMTP 授权码，为 None 时从环境变量 SMTP_PASSWORD 读取

    Returns:
        bool: 是否发送成功
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    if not username:
        username = os.environ.get("SMTP_USERNAME", "")
    if not password:
        password = os.environ.get("SMTP_PASSWORD", "")

    if not username or not password:
        logger.warning("未配置 QQ SMTP 凭据，跳过邮件发送")
        return False

    msg = MIMEMultipart()
    msg["From"] = f"Quota Monitor <{username}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        server.starttls()
        server.login(username, password)
        server.sendmail(username, [to], msg.as_string())
        server.quit()
        logger.info("QQ SMTP 邮件发送成功: %s", to)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("QQ SMTP 认证失败，请检查邮箱地址和授权码")
        return False
    except smtplib.SMTPConnectError:
        logger.error("无法连接 QQ SMTP 服务器")
        return False
    except Exception as e:
        logger.error("SMTP 异常: %s", e)
        return False


def send_email_agently(to, subject, body):
    """通过 agently-cli 发送邮件（本地回退方案）。

    Returns:
        bool: 是否发送成功
    """
    import subprocess

    try:
        subprocess.run(["agently-cli", "--version"],
                       capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("agently-cli 不可用，跳过邮件发送")
        return False

    base_cmd = [
        "agently-cli", "message", "+send",
        "--to", to, "--subject", subject, "--body", body,
        "--format", "json",
    ]
    env = os.environ.copy()
    env.setdefault("LARKSUITE_CLI_NO_UPDATE_NOTIFIER", "1")

    # 第一阶段
    try:
        result = subprocess.run(
            base_cmd, capture_output=True, text=True, timeout=30, env=env)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False

    if result.returncode == 0:
        logger.info("agently-cli 邮件发送成功: %s", to)
        return True

    if result.returncode != 8:
        logger.error("agently-cli 错误: exit=%d", result.returncode)
        return False

    # 解析 confirmation token
    try:
        response = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        logger.error("agently-cli JSON 解析失败")
        return False

    token = response.get("data", {}).get("confirmation_token", "")
    if not token:
        return False

    # 第二阶段
    confirm_cmd = base_cmd + ["--confirmation-token", token]
    try:
        result2 = subprocess.run(
            confirm_cmd, capture_output=True, text=True, timeout=30, env=env)
    except subprocess.TimeoutExpired:
        return False

    ok = result2.returncode == 0
    logger.info("agently-cli 邮件: %s -> %s", "OK" if ok else "FAIL", to)
    return ok


# ─── 频率控制 ───────────────────────────────────────────────────────

def _load_notify_state():
    """加载通知频率控制状态。"""
    if os.path.exists(_NOTIFY_STATE_FILE):
        try:
            with open(_NOTIFY_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_notify_state(state):
    """保存通知频率控制状态。"""
    try:
        with open(_NOTIFY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.warning("无法保存通知状态: %s", e)


def _can_send(channel, min_interval_seconds, max_daily=None):
    """检查某通知通道是否可以发送。

    Args:
        channel: 通道名 ("feishu" 或 "email")
        min_interval_seconds: 最小发送间隔
        max_daily: 每日上限（仅 email 使用）

    Returns:
        bool: 是否可以发送
    """
    state = _load_notify_state()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    # 检查最小间隔
    last_key = f"last_{channel}_time"
    last_time_str = state.get(last_key)
    if last_time_str:
        try:
            last_time = datetime.fromisoformat(last_time_str)
            elapsed = (now - last_time).total_seconds()
            if elapsed < min_interval_seconds:
                logger.info("%s 通知距上次仅 %.0f 秒，跳过（最小间隔 %d 秒）",
                            channel, elapsed, min_interval_seconds)
                return False
        except ValueError:
            pass

    # 检查每日上限
    if max_daily:
        count_key = f"daily_{channel}_count"
        date_key = f"daily_{channel}_date"
        if state.get(date_key) != today_str:
            state[count_key] = 0
            state[date_key] = today_str

        if state.get(count_key, 0) >= max_daily:
            logger.warning("%s 已达每日上限 %d，跳过发送", channel, max_daily)
            return False

    return True


def _record_sent(channel):
    """记录一次通知发送。"""
    state = _load_notify_state()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    state[f"last_{channel}_time"] = now.isoformat()

    count_key = f"daily_{channel}_count"
    date_key = f"daily_{channel}_date"
    if state.get(date_key) != today_str:
        state[count_key] = 0
        state[date_key] = today_str
    state[count_key] = state.get(count_key, 0) + 1

    _save_notify_state(state)


# ─── 统一发送接口 ───────────────────────────────────────────────────

def send_notifications(text, subject, config=None):
    """统一发送通知（飞书 + 邮件），带频率控制。

    飞书支持两种模式：
      - API 模式：自建应用，需要 APP_ID + APP_SECRET + CHAT_ID
      - Webhook 模式：群自定义机器人，只需要 webhook_url
      API 模式优先。

    Args:
        text: 飞书消息正文
        subject: 邮件主题
        config: 通知配置 dict，为 None 时从环境变量读取（CI 模式）

    Returns:
        dict: {"feishu": bool, "email": int} — email 为成功发送数
    """
    if config is None:
        config = _ci_config()

    result = {"feishu": False, "email": 0}

    # ── 飞书通知 ──
    feishu_cfg = config.get("feishu", {})
    feishu_enabled = feishu_cfg.get("enabled", True)
    app_id = feishu_cfg.get("app_id", "")
    app_secret = feishu_cfg.get("app_secret", "")
    chat_id = feishu_cfg.get("chat_id", "")
    webhook_url = feishu_cfg.get("webhook_url", "")

    if feishu_enabled:
        min_interval = feishu_cfg.get("min_interval_minutes", 10) * 60
        if _can_send("feishu", min_interval):
            # API 模式优先（自建应用）
            if app_id and app_secret and chat_id:
                if send_feishu_api(text, app_id, app_secret, chat_id):
                    _record_sent("feishu")
                    result["feishu"] = True
            # Webhook 模式（群自定义机器人）
            elif webhook_url:
                if send_feishu_webhook(webhook_url, text):
                    _record_sent("feishu")
                    result["feishu"] = True

    # ── 邮件通知 ──
    email_cfg = config.get("email", {})
    email_enabled = email_cfg.get("enabled", False)
    subscribers = email_cfg.get("subscribers", [])
    smtp_username = email_cfg.get("smtp_username", "")
    smtp_password = email_cfg.get("smtp_password", "")

    if email_enabled and subscribers:
        min_interval = email_cfg.get("min_interval_minutes", 30) * 60
        if _can_send("email", min_interval, max_daily=DEFAULT_MAX_EMAIL_DAILY):
            for recipient in subscribers:
                # QQ SMTP 优先
                sent = send_email_smtp(recipient, subject, text,
                                       smtp_username, smtp_password)
                # agently-cli 本地回退
                if not sent:
                    sent = send_email_agently(recipient, subject, text)
                if sent:
                    result["email"] += 1
                if len(subscribers) > 1:
                    time.sleep(2)
            if result["email"] > 0:
                _record_sent("email")

    return result


def _ci_config():
    """从环境变量构建 CI 模式配置。

    飞书支持两种方式：
      - FEISHU_APP_ID + FEISHU_APP_SECRET + FEISHU_CHAT_ID → API 模式（自建应用）
      - FEISHU_WEBHOOK_URL → Webhook 模式（群自定义机器人）
    """
    config = {
        "feishu": {
            "enabled": False,
            "app_id": "",
            "app_secret": "",
            "chat_id": "",
            "webhook_url": "",
        },
        "email": {"enabled": False, "subscribers": []},
    }

    # 飞书 API 模式 (自建应用 — CI 环境变量)
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    chat_id = os.environ.get("FEISHU_CHAT_ID", "")
    if app_id and app_secret and chat_id:
        config["feishu"]["enabled"] = True
        config["feishu"]["app_id"] = app_id
        config["feishu"]["app_secret"] = app_secret
        config["feishu"]["chat_id"] = chat_id

    # 飞书 Webhook 模式 (群自定义机器人)
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if webhook_url:
        config["feishu"]["enabled"] = True
        config["feishu"]["webhook_url"] = webhook_url

    # 邮件 (QQ SMTP + 订阅者列表)
    smtp_username = os.environ.get("SMTP_USERNAME", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    email_subscribers = os.environ.get("EMAIL_SUBSCRIBERS", "")
    if smtp_username and smtp_password:
        try:
            subscribers = json.loads(email_subscribers) if email_subscribers else []
            if isinstance(subscribers, list) and subscribers:
                config["email"]["enabled"] = True
                config["email"]["smtp_username"] = smtp_username
                config["email"]["smtp_password"] = smtp_password
                config["email"]["subscribers"] = subscribers
        except json.JSONDecodeError:
            logger.warning("EMAIL_SUBSCRIBERS JSON 解析失败")

    return config
