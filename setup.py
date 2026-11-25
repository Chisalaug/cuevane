#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性引导配置：先确认“看到什么 → 按哪个键”，再把结果写进 config.json。

用法：
    python setup.py            # 交互式引导（没有 config.json 时由 bat 自动调用）
    python setup.py --ensure   # 有配置就静默退出；没有就进引导
    python setup.py --reask    # 不管有没有配置，重新走一遍引导

配置写完之后，server.py / watcher.py 都从同一份 config.json 读参数。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from typing import Any, Dict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "hint",
    "wait_ms": 500,
    "port": 8765,
    "stage": 1,
    "axis": "lr",
    "left": {"label": "左", "key": "f", "signs": ["←", "◀", "left", "larr", "<"]},
    "right": {"label": "右", "key": "j", "signs": ["→", "▶", "right", "rarr", ">"]},
}

REQUIRED = {
    "flask": "flask",
    "pynput": "pynput",
    "mss": "mss",
    "numpy": "numpy",
}


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def load_config() -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    saved = _load_json(CONFIG_PATH)
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def check_deps() -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for mod, pkg in REQUIRED.items():
        out[pkg] = importlib.util.find_spec(mod) is not None
    return out


def ask(prompt: str, default: str = "") -> str:
    suffix = f"  [默认 {default}]" if default else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    return raw or default


def ask_key(side: str, default: str) -> str:
    while True:
        raw = ask(f"{side} 触发后按哪个键（单字母，如 f / j / a / k）", default).lower()
        if raw == "":
            raw = default
        if len(raw) == 1 or raw in ("space", "enter", "tab", "shift", "ctrl", "alt"):
            return raw
        print("  ⚠ 只接受单个字符；再输一次。")


def dep_warning() -> None:
    deps = check_deps()
    missing = [pkg for ok, pkg in zip(deps.values(), REQUIRED.values()) if not ok]
    if missing:
        print("\n  ⚠ 以下依赖还没装：" + ", ".join(missing))
        print(f"    装一下：python -m pip install {' '.join(missing)}")
        print("  （提示：flask / pynput 缺了服务端跑不起来；mss / numpy 缺了截屏识别起不来）\n")


def wizard(cfg: Dict[str, Any]) -> Dict[str, Any]:
    print("=" * 64)
    print("  引导配置：先把“看到什么 → 按哪个键”定下来，再装油猴脚本")
    print("=" * 64)
    print()

    dep_warning()

    print("【1/4】刺激是哪一类？")
    print("  1) 左右箭头  ←  /  →        （默认，最常见）")
    print("  2) 上下箭头  ↑  /  ↓")
    print("  3) 自定义两个符号（自己输入）")
    axis = ask("选 1 / 2 / 3", "1")
    if axis == "2":
        cfg["axis"] = "ud"
        cfg["left"]["label"] = "上"
        cfg["right"]["label"] = "下"
        cfg["left"]["signs"] = ["↑", "▲", "up", "uarr"]
        cfg["right"]["signs"] = ["↓", "▼", "down", "darr"]
    elif axis == "3":
        cfg["axis"] = "custom"
        s1 = ask("第一个符号（如 ← / R / A）", cfg["left"]["signs"][0])
        s2 = ask("第二个符号（如 → / L / B）", cfg["right"]["signs"][0])
        cfg["left"]["label"] = s1
        cfg["right"]["label"] = s2
        cfg["left"]["signs"] = [s1]
        cfg["right"]["signs"] = [s2]
    else:
        cfg["axis"] = "lr"
        cfg["left"]["label"] = "左"
        cfg["right"]["label"] = "右"
        cfg["left"]["signs"] = ["←", "◀", "left", "larr", "<"]
        cfg["right"]["signs"] = ["→", "▶", "right", "rarr", ">"]

    print()
    print("【2/4】两个按键是什么？")
    cfg["left"]["key"] = ask_key(f"{cfg['left']['label']}（{cfg['left']['signs'][0]}）", cfg["left"]["key"])
    cfg["right"]["key"] = ask_key(f"{cfg['right']['label']}（{cfg['right']['signs'][0]}）", cfg["right"]["key"])

    print()
    print("【3/4】运行模式")
    mode = ask("hint = 只在副屏提示，不碰键盘；auto = 真的帮你按", cfg["mode"])
    cfg["mode"] = "auto" if mode.lower() in ("auto", "a", "1") else "hint"
    if cfg["mode"] == "auto":
        cfg["wait_ms"] = int(ask("看到刺激后等多少毫秒再按（stop-signal 建议 500）", str(cfg["wait_ms"])) or "500")

    print()
    print("【4/4】确认下面这份配置：")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    ok = ask("保存并继续？(y/n)", "y")
    if ok.lower() not in ("y", "yes", "是", ""):
        print("已取消，未写入。")
        sys.exit(1)

    save_config(cfg)
    print(f"\n  ✓ 配置已写入：{CONFIG_PATH}")
    return cfg


def main() -> None:
    ensure = "--ensure" in sys.argv
    reask = "--reask" in sys.argv
    cfg = load_config()

    if ensure and os.path.exists(CONFIG_PATH):
        print(f"[setup] 已有配置：{CONFIG_PATH}")
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return

    cfg = wizard(cfg)
    if not ensure:
        try:
            input("\n回车继续启动服务端 …")
        except EOFError:
            pass


if __name__ == "__main__":
    main()
