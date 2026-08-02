"""通知模块 — 飞书群聊 + 私聊通知。

在 CI (GitHub Actions) 中通过环境变量获取配置：
  - FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID: 飞书自建应用

本地运行时通过 config.json 配置。
"""

import json
import logging
import os

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


# ─── 统一发送接口 ───────────────────────────────────────────────────

def send_notifications(text, config=None):
    """统一发送飞书通知。邮件功能已下架。

    飞书支持两种模式：
      - API 模式：自建应用，需要 APP_ID + APP_SECRET + CHAT_ID
      - Webhook 模式：群自定义机器人，只需要 webhook_url
      API 模式优先。

    Args:
        text: 飞书消息正文
        config: 通知配置 dict，为 None 时从环境变量读取（CI 模式）

    Returns:
        dict: {"feishu": bool}
    """
    if config is None:
        config = _ci_config()

    result = {"feishu": False}

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

    return config
