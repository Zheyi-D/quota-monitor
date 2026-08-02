"""通知模块 — 飞书 Webhook + 邮件 (agently-cli) 双通道通知。

在 CI (GitHub Actions) 中通过环境变量获取配置：
  - FEISHU_WEBHOOK_URL: 飞书群机器人 webhook URL
  - EMAIL_SUBSCRIBERS: JSON 数组，邮件订阅者列表

本地运行时通过 config.json 配置。
"""

import json
import logging
import os
import re
import subprocess
import time

import requests

logger = logging.getLogger("quota_monitor")


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


def send_feishu_dm(text, app_id, app_secret, open_id, title="🔔 预约配额通知"):
    """通过飞书 API 发送私聊消息卡片到用户（DM）。

    Args:
        text: 消息正文（Markdown）
        app_id: 飞书应用 App ID
        app_secret: 飞书应用 App Secret
        open_id: 收件人 open_id（ou_xxx）
        title: 卡片标题

    Returns:
        bool: 是否发送成功
    """
    if not all([app_id, app_secret, open_id]):
        logger.warning("飞书 DM 参数不完整，跳过")
        return False

    token = _get_tenant_access_token(app_id, app_secret)
    if not token:
        return False

    payload = {
        "receive_id": open_id,
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
            params={"receive_id_type": "open_id"},
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
                logger.info("飞书 DM 发送成功 (open_id=%s)", open_id[:16])
                return True
            else:
                logger.error("飞书 DM 返回错误: code=%d, msg=%s",
                             body.get("code"), body.get("msg"))
                return False
        else:
            logger.error("飞书 DM HTTP %d: %s", resp.status_code, resp.text[:200])
            return False
    except requests.Timeout:
        logger.error("飞书 DM 请求超时")
        return False
    except Exception as e:
        logger.error("飞书 DM 异常: %s", e)
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
    """通过 QQ SMTP 发送邮件（CI 友好，500封/天）。

    Args:
        to: 收件人邮箱
        subject: 邮件主题
        body: 邮件正文（自动检测 HTML/纯文本）
        username: QQ 邮箱地址
        password: QQ SMTP 授权码

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

    # 自动检测 HTML
    is_html = bool(re.search(r"<\w+[^>]*>", body))
    subtype = "html" if is_html else "plain"

    msg = MIMEMultipart()
    msg["From"] = f"Quota Monitor <{username}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, subtype, "utf-8"))

    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        server.starttls()
        server.login(username, password)
        server.sendmail(username, [to], msg.as_string())
        server.quit()
        logger.info("QQ SMTP 邮件发送成功")
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
    logger.info("agently-cli 邮件: %s", "OK" if ok else "FAIL")
    return ok


# ─── 统一发送接口 ───────────────────────────────────────────────────

def send_notifications(text, subject, config=None):
    """统一发送通知（飞书 + 邮件），无频率控制。

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
        if app_id and app_secret and chat_id:
            result["feishu"] = send_feishu_api(text, app_id, app_secret, chat_id)
        elif webhook_url:
            result["feishu"] = send_feishu_webhook(webhook_url, text)

    # ── 邮件通知 ──
    email_cfg = config.get("email", {})
    email_enabled = email_cfg.get("enabled", False)
    subscribers = email_cfg.get("subscribers", [])
    smtp_username = email_cfg.get("smtp_username", "")
    smtp_password = email_cfg.get("smtp_password", "")

    if email_enabled and subscribers:
        for recipient in subscribers:
            sent = send_email_smtp(recipient, subject, text,
                                   smtp_username, smtp_password)
            if not sent:
                sent = send_email_agently(recipient, subject, text)
            if sent:
                result["email"] += 1
            if len(subscribers) > 1:
                time.sleep(2)

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
