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


def format_changes(changes, offices=None):
    """将变化字典格式化为人类可读的消息文本。

    Args:
        changes: detect_changes() 返回的变化字典
        offices: 办事处名称映射 dict

    Returns:
        str: 格式化的消息文本
    """
    if not offices:
        offices = DEFAULT_OFFICES

    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"📅 检测时间：{now}")
    lines.append("")

    has_any = False

    if changes.get("newly_available"):
        has_any = True
        lines.append("🟢 **新放出名额！**")
        lines.append("")
        for (date, office, qtype), old_s, new_s in changes["newly_available"]:
            office_name = offices.get(office, office)
            qtype_name = QUOTA_TYPES.get(qtype, qtype)
            status_name = STATUS_NAMES.get(new_s, new_s)
            lines.append(f"  • {date}  {office_name}({office})  {qtype_name} → {status_name}")

    if changes.get("newly_added"):
        has_any = True
        lines.append("")
        lines.append("🆕 **新日期可约：**")
        lines.append("")
        for (date, office, qtype), new_s in changes["newly_added"]:
            if is_available(new_s):
                office_name = offices.get(office, office)
                qtype_name = QUOTA_TYPES.get(qtype, qtype)
                status_name = STATUS_NAMES.get(new_s, new_s)
                lines.append(f"  • {date}  {office_name}({office})  {qtype_name} → {status_name}")

    if changes.get("newly_full"):
        has_any = True
        lines.append("")
        lines.append("🔴 **名额已满：**")
        lines.append("")
        for (date, office, qtype), old_s, new_s in changes["newly_full"]:
            office_name = offices.get(office, office)
            qtype_name = QUOTA_TYPES.get(qtype, qtype)
            lines.append(f"  • {date}  {office_name}({office})  {qtype_name} 已满")

    if not has_any:
        lines.append("✅ 配额状态无变化")
        lines.append(f"  共监控 {len(DEFAULT_OFFICES)} 个办事处")

    # 添加网页看板链接（部署后填入实际地址）
    lines.append("")
    lines.append("—" * 30)
    lines.append("📊 实时看板：https://Zheyi-D.github.io/quota-monitor")
    lines.append("📋 预约办理：https://www.gov.hk/sc/apps/immdicbooking2.htm")
    lines.append("🪧 配额查询：https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/?l=zh-CN&appId=579")
    lines.append("📱 加入飞书群：https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=ff3i6631-016b-40cc-989e-e4651ccd353c")
    lines.append("")
    lines.append("⚠️ 本系统为第三方开源工具，非香港入境事务处官方服务。请以官网信息为准。")
    lines.append("   仅供学习交流，请勿用于商业盈利目的。")

    return "\n".join(lines)


def has_significant_change(changes):
    """判断是否有值得通知的变化（新名额放出）。"""
    return bool(changes.get("newly_available") or changes.get("newly_added"))


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
