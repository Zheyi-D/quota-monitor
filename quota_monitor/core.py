"""API 拉取 + 变化检测 — 香港入境处预约配额监控的核心逻辑。"""

import json
import logging
import time
from datetime import datetime

import requests

logger = logging.getLogger("quota_monitor")

# API 基础配置
API_URL = "https://eservices.es2.immd.gov.hk/surgecontrolgate/ticket/getSituation"

# 配额状态 → 可用等级 (数字越小越好)
STATUS_LEVEL = {
    "quota-g": 1,   # 绿 — 有名额
    "quota-y": 2,   # 黄 — 少量名额
    "quota-r": 3,   # 红 — 已满
    "no-quotaR": 4, # 不提供一般服务
    "no-quotaK": 4, # 不提供延长服务
}

# 状态显示名称
STATUS_NAMES = {
    "quota-g": "有名额 🟢",
    "quota-y": "少量 🟡",
    "quota-r": "已满 🔴",
    "no-quotaR": "不提供",
    "no-quotaK": "不提供",
}

# 时段类型名称
QUOTA_TYPES = {
    "R": "一般服务时段",
    "K": "延长服务时段",
}

DEFAULT_OFFICES = {
    "FTO": "火炭办事处",
    "RHK": "港岛办事处",
    "RKO": "九龙办事处",
    "RTK": "将军澳办事处",
    "TMO": "屯门办事处",
    "YLO": "元朗办事处",
}


def fetch_snapshot(offices=None, date_start=None, date_end=None,
                   svc_id=579, timeout=30, max_retries=3, backoff_base=5):
    """拉取 API 并返回规范化的快照字典。

    Args:
        offices: 要监控的办事处代码集合，None 表示全部
        date_start: 起始日期 (MM/DD/YYYY)，None 不限制
        date_end: 结束日期 (MM/DD/YYYY)，None 不限制
        svc_id: 服务 ID
        timeout: 请求超时秒数
        max_retries: 最大重试次数
        backoff_base: 退避基数秒数

    Returns:
        dict: {(date, officeId, type): status_string}
    """
    params = {"svcId": svc_id}

    for attempt in range(max_retries):
        try:
            resp = requests.get(
                API_URL,
                params=params,
                timeout=timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/130.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://eservices.es2.immd.gov.hk/",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.Timeout:
            logger.warning("请求超时，第 %d/%d 次重试", attempt + 1, max_retries)
        except requests.ConnectionError as e:
            logger.warning("连接失败，第 %d/%d 次重试: %s", attempt + 1, max_retries, e)
        except requests.HTTPError as e:
            logger.error("HTTP 错误 %d，不重试", e.response.status_code)
            return {}
        except Exception as e:
            logger.error("未知错误: %s", e)
            return {}

        if attempt < max_retries - 1:
            time.sleep(backoff_base * (2 ** attempt))

    else:
        logger.error("所有 %d 次重试均失败", max_retries)
        return {}

    if "data" not in data:
        logger.error("API 返回格式异常，缺少 'data' 字段")
        return {}

    snapshot = {}
    offices_filter = set(offices) if offices else None

    for record in data["data"]:
        office = record.get("officeId", "")
        date = record.get("date", "")

        # 过滤办事处
        if offices_filter and office not in offices_filter:
            continue

        # 过滤日期范围
        if date_start and _date_cmp(date, date_start) < 0:
            continue
        if date_end and _date_cmp(date, date_end) > 0:
            continue

        snapshot[(date, office, "R")] = record.get("quotaR", "")
        snapshot[(date, office, "K")] = record.get("quotaK", "")

    logger.info("成功拉取 %d 条记录", len(snapshot))
    return snapshot


def status_level(status):
    """返回配额状态的可用等级，数字越小越好。"""
    return STATUS_LEVEL.get(status, 4)


def is_available(status):
    """判断当前状态是否表示有名额。"""
    return status_level(status) <= 2


def detect_changes(old_snapshot, new_snapshot, notify_full=True):
    """对比两个快照，检测配额变化。

    Args:
        old_snapshot: 上次快照 dict
        new_snapshot: 本次快照 dict
        notify_full: 是否通知"名额已满"的变化

    Returns:
        dict: {
            "newly_available": [(key, old_status, new_status), ...],
            "newly_full": [(key, old_status, new_status), ...],
            "newly_added": [(key, new_status), ...],
        }
    """
    changes = {
        "newly_available": [],
        "newly_full": [],
        "newly_added": [],
    }

    for key, new_status in new_snapshot.items():
        old_status = old_snapshot.get(key)

        if old_status is None:
            # 新出现的记录（新日期进入窗口或首次运行）
            if is_available(new_status):
                changes["newly_added"].append((key, new_status))
        else:
            old_lv = status_level(old_status)
            new_lv = status_level(new_status)

            if old_lv > 2 >= new_lv:
                # 从不可用/已满 → 有名额（最重要！）
                changes["newly_available"].append((key, old_status, new_status))
            elif notify_full and old_lv <= 2 < new_lv:
                # 从有名额 → 已满
                changes["newly_full"].append((key, old_status, new_status))

    return changes


def _load_template():
    """从文件加载通知模板，如文件不存在返回 None。"""
    import base64
    import os

    tpl_path = "data/notify_template.json"
    if not os.path.exists(tpl_path):
        return None

    try:
        import json
        with open(tpl_path) as f:
            wrapper = json.load(f)
        if wrapper and isinstance(wrapper, dict) and wrapper.get("enc"):
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            key = base64.b64decode(
                os.environ.get("ENCRYPTION_KEY",
                               "MC31nrort3V69A/EloZj9TXVAeNdB2zO2dh0ZNvEfk0="))
            aes = AESGCM(key)
            raw = base64.b64decode(wrapper["data"])
            iv, ct = raw[:12], raw[12:]
            return json.loads(aes.decrypt(iv, ct, None))
        return wrapper
    except Exception:
        return None


def _render_item(tpl_item, **kwargs):
    """渲染单行模板，将 {{key}} 替换为实际值。"""
    import re
    result = tpl_item
    for key, val in kwargs.items():
        result = result.replace("{{%s}}" % key, str(val))
    return result


def format_changes(changes, offices=None):
    """将变化字典格式化为人类可读的消息文本。

    支持自定义模板：data/notify_template.json（加密）。
    如文件不存在则使用硬编码默认模板。

    Args:
        changes: detect_changes() 返回的变化字典
        offices: 办事处名称映射 dict

    Returns:
        str: 格式化的消息文本
    """
    if not offices:
        offices = DEFAULT_OFFICES

    import time as _time
    bj_ts = _time.time() + 8 * 3600
    now = _time.strftime("%Y-%m-%d %H:%M:%S", _time.gmtime(bj_ts))

    # 默认链接（模板中可覆盖）
    default_links = {
        "dashboard_url": "https://Zheyi-D.github.io/quota-monitor",
        "booking_url": "https://www.gov.hk/sc/apps/immdicbooking2.htm",
        "quota_url": "https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/?l=zh-CN&appId=579",
        "group_url": "https://scn7uo58gnuo.feishu.cn/wiki/QSFlwcMBmil7sGkZRBTcAWqwnCf",
    }

    # 默认模板
    default_header = "📅 检测时间（北京时间）：{{time}}\n\n🟢 **新放出名额！**\n"
    default_item = "  • {{date}}  {{office_name}}({{office}})  {{qtype_name}} → {{status_name}}"
    default_footer = (
        "\n——————————————————————————————\n"
        "📊 实时看板：{{dashboard_url}}\n"
        "📋 预约办理：{{booking_url}}\n"
        "🪧 配额查询：{{quota_url}}\n"
        "📖 加群方式：{{group_url}}\n"
        "\n⚠️ 本系统为第三方开源工具，非香港入境事务处官方服务。请以官网信息为准。\n"
        "   仅供学习交流，请勿用于商业盈利目的。"
    )

    # 尝试加载自定义模板
    tpl = _load_template()
    if tpl and isinstance(tpl, dict):
        header = tpl.get("header", default_header)
        item_fmt = tpl.get("item", default_item)
        footer = tpl.get("footer", default_footer)
        links = {**default_links, **tpl.get("links", {})}
    else:
        header = default_header
        item_fmt = default_item
        footer = default_footer
        links = default_links

    lines = []
    # 渲染 header
    header_rendered = _render_item(header, time=now, **links)
    lines.append(header_rendered)

    if changes.get("newly_available"):
        for (date, office, qtype), old_s, new_s in changes["newly_available"]:
            office_name = offices.get(office, office)
            qtype_name = QUOTA_TYPES.get(qtype, qtype)
            status_name = STATUS_NAMES.get(new_s, new_s)
            item_rendered = _render_item(
                item_fmt,
                date=date, office_name=office_name, office=office,
                qtype_name=qtype_name, status_name=status_name,
                **links,
            )
            lines.append(item_rendered)

    # 渲染 footer
    footer_rendered = _render_item(footer, time=now, **links)
    lines.append(footer_rendered)

    return "\n".join(lines)


def has_significant_change(changes):
    """判断是否有值得通知的变化（新名额放出）。"""
    return bool(changes.get("newly_available"))


def _date_cmp(d1, d2):
    """比较两个 MM/DD/YYYY 格式的日期。返回 -1/0/1。"""
    try:
        p1 = d1.split("/")
        p2 = d2.split("/")
        for i in (2, 0, 1):  # year, month, day
            a, b = int(p1[i]), int(p2[i])
            if a < b:
                return -1
            if a > b:
                return 1
        return 0
    except (IndexError, ValueError):
        return 0


def export_web_data(snapshot, path="data/quota.json"):
    """将快照导出为 web 看板使用的 JSON 格式。

    Args:
        snapshot: {(date, officeId, type): status} 字典
        path: 输出文件路径
    """
    import os

    flat = {}
    for (date, office, qtype), status in snapshot.items():
        flat[f"{date}|{office}|{qtype}"] = status

    # 确保目录存在
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    # 原子写入
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(flat, f, ensure_ascii=False)
    os.replace(tmp_path, path)
    logger.info("Web 数据已导出到 %s (%d 条记录)", path, len(flat))


def load_config(path="config.json"):
    """加载配置文件。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("配置文件 %s 不存在，使用默认配置", path)
        return _default_config()
    except json.JSONDecodeError as e:
        logger.error("配置文件 %s 格式错误: %s，使用默认配置", path, e)
        return _default_config()


def _default_config():
    """返回默认配置。"""
    return {
        "api": {"svc_id": 579, "timeout": 30},
        "offices": DEFAULT_OFFICES,
        "date_range": {"start": None, "end": None},
        "notifications": {
            "feishu": {"enabled": False, "webhook_url": ""},
            "email": {"enabled": False, "subscribers": [], "min_interval_minutes": 30},
        },
        "retry": {"max_retries": 3, "backoff_base_seconds": 5},
        "log": {"level": "INFO", "file": "monitor.log", "max_size_mb": 10, "backup_count": 3},
    }
