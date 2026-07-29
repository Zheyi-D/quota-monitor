"""状态持久化 — 快照保存与加载，原子写入防损坏。"""

import json
import logging
import os
import tempfile
from datetime import datetime

logger = logging.getLogger("quota_monitor")

STATE_VERSION = 1


def load_state(path="state.json"):
    """加载状态文件。

    Args:
        path: 状态文件路径

    Returns:
        dict: 状态字典，包含 last_snapshot、last_snapshot_time 等
              如文件不存在或损坏，返回空状态
    """
    if not os.path.exists(path):
        logger.info("状态文件 %s 不存在，视为首次运行", path)
        return _empty_state()

    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error("状态文件 %s 读取失败: %s", path, e)
        # 备份损坏的文件
        _backup_corrupt(path)
        return _empty_state()

    # 版本检查
    if state.get("version") != STATE_VERSION:
        logger.warning("状态文件版本不匹配 (got %s, want %s)，重置",
                       state.get("version"), STATE_VERSION)
        return _empty_state()

    # 重建 snapshot（JSON key 是字符串，需要还原为 tuple）
    raw_snapshot = state.get("last_snapshot", {})
    snapshot = {}
    for key_str, status in raw_snapshot.items():
        parts = key_str.split("|")
        if len(parts) == 3:
            snapshot[(parts[0], parts[1], parts[2])] = status

    state["last_snapshot"] = snapshot
    return state


def save_state(path, snapshot, state_extra=None):
    """原子写入状态文件。

    先写临时文件，再 rename，防止写入中断导致数据损坏。

    Args:
        path: 状态文件路径
        snapshot: {(date, office, type): status} 快照字典
        state_extra: 额外状态字段 dict (如 notification_timestamps)
    """
    # 将 tuple key 序列化为 "date|office|type" 格式的字符串
    serializable_snapshot = {}
    for key, status in snapshot.items():
        key_str = "|".join(key)
        serializable_snapshot[key_str] = status

    state = {
        "version": STATE_VERSION,
        "last_snapshot": serializable_snapshot,
        "last_snapshot_time": datetime.now().isoformat(),
    }

    if state_extra:
        state.update(state_extra)

    try:
        dir_name = os.path.dirname(path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
            logger.debug("状态已保存到 %s (%d 条记录)", path, len(serializable_snapshot))
        except Exception:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
    except Exception as e:
        logger.error("保存状态文件失败: %s", e)


def _empty_state():
    """返回空初始状态。"""
    return {
        "version": STATE_VERSION,
        "last_snapshot": {},
        "last_snapshot_time": None,
    }


def _backup_corrupt(path):
    """备份损坏的状态文件。"""
    backup_path = path + ".corrupt." + datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        os.rename(path, backup_path)
        logger.warning("已备份损坏的状态文件到 %s", backup_path)
    except OSError as e:
        logger.error("无法备份损坏的状态文件: %s", e)
