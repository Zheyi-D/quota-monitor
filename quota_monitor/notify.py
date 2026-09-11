"""通知模块 — 飞书与企业微信群通知。

在 CI (GitHub Actions) 中通过环境变量获取配置：
  - FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID: 飞书自建应用
  - WECOM_WEBHOOK_URL: 企业微信群机器人 Webhook URL

本地运行时通过 config.json 配置。
"""

import json
import logging
import os
from urllib.parse import parse_qs, urlsplit

import requests

logger = logging.getLogger("quota_monitor")


# ─── 企业微信通知 ────────────────────────────────────────────────

WECOM_WEBHOOK_HOST = "qyapi.weixin.qq.com"
WECOM_WEBHOOK_PATH = "/cgi-bin/webhook/send"
WECOM_MARKDOWN_MAX_BYTES = 4096


def _split_utf8(text, max_bytes):
    """按 UTF-8 字节数拆分文本，不截断多字节字符。"""
    if max_bytes <= 0:
        raise ValueError("max_bytes 必须大于 0")

    chunks = []
    current = ""
    for line in text.splitlines(keepends=True):
        while line:
            remaining = max_bytes - len(current.encode("utf-8"))
            if remaining <= 0:
                chunks.append(current.rstrip("\n"))
                current = ""
                remaining = max_bytes

            if len(line.encode("utf-8")) <= remaining:
                current += line
                break

            if current:
                chunks.append(current.rstrip("\n"))
                current = ""
                continue

            encoded = line.encode("utf-8")
            cut = min(max_bytes, len(encoded))
            while cut > 0 and cut < len(encoded) and (encoded[cut] & 0xC0) == 0x80:
                cut -= 1
            if cut == 0:
                raise ValueError("max_bytes 小于单个 UTF-8 字符")
            chunks.append(encoded[:cut].decode("utf-8").rstrip("\n"))
            line = encoded[cut:].decode("utf-8")

    if current or not chunks:
        chunks.append(current.rstrip("\n"))
    return chunks


def _validate_wecom_webhook_url(webhook_url):
    """只允许企业微信官方 HTTPS 群机器人地址，避免误发 Secret。"""
    parsed = urlsplit(webhook_url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == WECOM_WEBHOOK_HOST
        and parsed.path == WECOM_WEBHOOK_PATH
        and bool(parse_qs(parsed.query).get("key"))
    )


def send_wecom_webhook(webhook_url, text, title="香港入境处预约配额监控"):
    """通过企业微信群机器人发送 Markdown 通知。

    企业微信单条 Markdown 最多 4096 UTF-8 字节；超长内容会按字节安全拆分，
    每一段都会保留标题。

    Returns:
        bool: 所有分段是否均发送成功
    """
    if not webhook_url:
        logger.warning("企业微信 webhook URL 未配置，跳过发送")
        return False
    if not _validate_wecom_webhook_url(webhook_url):
        logger.error("企业微信 webhook URL 格式无效，必须使用官方 HTTPS 群机器人地址")
        return False

    prefix = f"## 🔔 {title}\n"
    content_limit = WECOM_MARKDOWN_MAX_BYTES - len(prefix.encode("utf-8"))
    chunks = _split_utf8(text, content_limit)

    for index, chunk in enumerate(chunks, start=1):
        content = prefix + chunk
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }
        try:
            resp = requests.post(
                webhook_url,
                json=payload,
                timeout=15,
                allow_redirects=False,
            )
        except requests.Timeout:
            logger.error("企业微信 webhook 请求超时 (分段 %d/%d)", index, len(chunks))
            return False
        except requests.RequestException as exc:
            logger.error("企业微信 webhook 请求失败 (分段 %d/%d): %s",
                         index, len(chunks), type(exc).__name__)
            return False

        if resp.status_code != 200:
            logger.error("企业微信 webhook HTTP %d (分段 %d/%d)",
                         resp.status_code, index, len(chunks))
            return False
        try:
            body = resp.json()
        except ValueError:
            logger.error("企业微信 webhook 返回了无效 JSON (分段 %d/%d)",
                         index, len(chunks))
            return False
        if body.get("errcode") != 0:
            logger.error("企业微信 API 返回错误: errcode=%s errmsg=%s",
                         body.get("errcode"), body.get("errmsg"))
            return False

    logger.info("企业微信群通知发送成功 (%d 条)", len(chunks))
    return True


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
    """统一发送飞书与企业微信群通知。邮件功能已下架。

    飞书支持两种模式：
      - API 模式：自建应用，需要 APP_ID + APP_SECRET + CHAT_ID
      - Webhook 模式：群自定义机器人，只需要 webhook_url
      API 模式优先。

    Args:
        text: Markdown 消息正文
        config: 通知配置 dict，为 None 时从环境变量读取（CI 模式）

    Returns:
        dict: {"feishu": bool, "wecom": bool}
    """
    if config is None:
        config = _ci_config()

    result = {"feishu": False, "wecom": False}

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

    # ── 企业微信群通知 ──
    wecom_cfg = config.get("wecom", {})
    if wecom_cfg.get("enabled", False):
        wecom_url = wecom_cfg.get("webhook_url", "")
        if wecom_url:
            result["wecom"] = send_wecom_webhook(wecom_url, text)

    return result


def _ci_config():
    """从环境变量构建 CI 模式配置。

    飞书支持两种方式：
      - FEISHU_APP_ID + FEISHU_APP_SECRET + FEISHU_CHAT_ID → API 模式（自建应用）
      - FEISHU_WEBHOOK_URL → Webhook 模式（群自定义机器人）
    企业微信使用 WECOM_WEBHOOK_URL → 群机器人 Webhook。
    """
    config = {
        "feishu": {
            "enabled": False,
            "app_id": "",
            "app_secret": "",
            "chat_id": "",
            "webhook_url": "",
        },
        "wecom": {
            "enabled": False,
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

    # 企业微信群机器人
    wecom_url = os.environ.get("WECOM_WEBHOOK_URL", "")
    if wecom_url:
        config["wecom"]["enabled"] = True
        config["wecom"]["webhook_url"] = wecom_url

    return config
