#!/usr/bin/env python3
"""隔离模式（-I 语义）下调用 workbench.tools 的包装器。

用法：python3 -I scripts/tool_call_isolated.py <tool> <input.json>
显式插入项目路径，避免 -P 导致找不到 workbench，同时保持隔离模式安全语义。
"""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.argv = ["workbench.tools", "call", sys.argv[1], sys.argv[2]]
runpy.run_module("workbench.tools", run_name="__main__")
