#!/usr/bin/env python3
"""香港入境处预约配额监控 — 快速启动脚本。

用法:
  python monitor.py --once           # 单次运行
  python monitor.py --interval 600   # 持续轮询
  python monitor.py --ci             # CI 模式（从环境变量读取）
"""

import sys
import os

# 确保 quota_monitor 模块可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from quota_monitor.monitor import main

if __name__ == "__main__":
    main()
