#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
n-back 测试辅助工具 —— 本地服务端（v1）

职责：
  1. 监听 127.0.0.1:8765，接收 Tampermonkey 脚本 POST /evt 过来的 JSON：
         { "t": 时间戳, "type": "stim|sound|dom|start|canvas|canvas_warning", "detail": ... }
  2. 全部事件按到达顺序追加写入 log.jsonl（ts 单调递增）。
  3. 控制台大字打印“最新决策”：F 绿 / J 蓝 / NONE 红。
  4. --mode hint 只看不按；--mode auto 用 pynput 真实按键。

用法：
    python server.py --mode hint
    python server.py --mode auto --wait-ms 120
    python server.py --mode hint --port 8765 --log log.jsonl

阶段切换：
    服务端运行期间，直接在键盘上按数字键 1 / 2 即可切换阶段（全局生效，不需要切窗口）。
        阶段 1：左箭头 → F，右箭头 → J
        阶段 2：字母 R → F，其他字母 → J
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, request

try:
    from pynput import keyboard
except Exception as _e:  # 没装 pynput 也不该整个服务起不来
    keyboard = None
    _PYNPUT_ERR = _e
else:
    _PYNPUT_ERR = None


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

CONFIG = {
    "host": "127.0.0.1",
    "port": 8765,
    "wait_ms": 0,          # auto 模式：stim 到来后等多久再按（期间收到 sound 就放弃按键）
    "log_name": "log.jsonl",
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "hint",
    "wait_ms": 500,
    "port": 8765,
    "stage": 1,
    "axis": "lr",
    "left": {"label": "左", "key": "f", "signs": ["←", "◀", "left", "larr", "<"]},
    "right": {"label": "右", "key": "j", "signs": ["→", "▶", "right", "rarr", ">"]},
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config(path: str | None = None) -> Dict[str, Any]:
    """读 config.json；缺的字段用默认值补齐。CLI 参数在 main() 里再覆盖。"""
    cfg: Dict[str, Any] = json.loads(json.dumps(DEFAULT_CONFIG))
    path = path or os.path.join(BASE_DIR, "config.json")
    saved: Dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
        except Exception:
            saved = {}
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg

# ANSI 颜色
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
BLUE = "\033[94m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
GREY = "\033[90m"

COLOR_OF = {"F": GREEN, "J": BLUE, "NONE": RED, "?": YELLOW}


# 6 行 × 5 列的点阵字，用来在控制台打“大字”
GLYPHS: Dict[str, list] = {
    "F": [
        "█████",
        "█    ",
        "█    ",
        "████ ",
        "█    ",
        "█    ",
    ],
    "J": [
        "█████",
        "    █",
        "    █",
        "    █",
        "█   █",
        " ███ ",
    ],
    "N": [
        "█   █",
        "██  █",
        "█ █ █",
        "█  ██",
        "█   █",
        "█   █",
    ],
    "O": [
        " ███ ",
        "█   █",
        "█   █",
        "█   █",
        "█   █",
        " ███ ",
    ],
    "E": [
        "█████",
        "█    ",
        "████ ",
        "█    ",
        "████ ",
        "█████",
    ],
    "?": [
        " ███ ",
        "█   █",
        "   █ ",
        "  █  ",
        "     ",
        "  █  ",
    ],
}

PRINT_LOCK = threading.Lock()


def enable_console() -> None:
    """Windows 控制台打开 VT 转义，并尽量用 UTF-8 输出方块字符。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if os.name != "nt":
        return
    try:
        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(handle, ctypes.byref(mode)):
            k32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass
    # 设置窗口标题，方便在副屏任务栏一眼找到
    try:
        k32.SetConsoleTitleW("n-back helper  |  stage keys: 1 / 2")
    except Exception:
        pass


def emit(text: str) -> None:
    with PRINT_LOCK:
        print(text, flush=True)


def big_word(word: str, color: str, subtitle: str = "", note: str = "") -> None:
    """把 F / J / NONE / ? 打成 6 行大字；自定义按键没有点阵就退化成一行粗体。"""
    word = (word or "?").upper()
    out = [""]
    if subtitle:
        out.append(f"{DIM}{subtitle}{RESET}")
    if any(ch not in GLYPHS for ch in word):
        out.append(f"{BOLD}{color}  {word}  {RESET}")
    else:
        lines = [""] * 6
        for ch in word:
            glyph = GLYPHS[ch]
            for i in range(6):
                lines[i] += glyph[i] + "  "
        out.append(f"{BOLD}{color}{chr(10).join(lines).rstrip()}{RESET}")
    if note:
        out.append(f"{color}{note}{RESET}")
    out.append("")
    emit("\n".join(out))


def _clip(text: str, limit: int = 110) -> str:
    s = " ".join(str(text).split())
    return s[:limit] + ("…" if len(s) > limit else "")


# --------------------------------------------------------------------------- #
# log.jsonl 写入（时间戳强制单调递增）
# --------------------------------------------------------------------------- #

class JsonlWriter:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._seq = 0
        self._last_ts = 0.0

    def write(self, payload: Dict[str, Any]) -> int:
        with self._lock:
            self._seq += 1
            ts = time.time()
            if ts <= self._last_ts:          # 同一毫秒内多条事件也保证严格递增
                ts = self._last_ts + 0.0005
            self._last_ts = ts
            record = {
                "seq": self._seq,
                "ts": round(ts, 6),
                "clock": datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3],
            }
            record.update(payload)
            try:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception as exc:
                emit(f"{RED}[log] 写入失败：{exc}{RESET}")
            return self._seq


# --------------------------------------------------------------------------- #
# 判定：stim detail → F / J
# --------------------------------------------------------------------------- #

def _detail_text(detail: Any) -> str:
    if isinstance(detail, str):
        return detail.strip()
    try:
        return json.dumps(detail, ensure_ascii=False)
    except Exception:
        return str(detail)


def _hit_sign(s: str, low: str, side: Dict[str, Any]) -> bool:
    signs = side.get("signs") or []
    if any(x and x in s for x in signs):
        return True
    return any(x.lower() in low for x in signs if x and x.isascii() and x.isalpha())


def decide_stim(stage: int, detail: Any, cfg: Dict[str, Any]) -> Tuple[str, str]:
    """返回 (按键, 理由)。判不出来时按键为 '?'（不按）。"""
    s = _detail_text(detail)
    low = s.lower()
    left, right = cfg["left"], cfg["right"]

    if stage == 1:
        hl, hr = _hit_sign(s, low, left), _hit_sign(s, low, right)
        if hl and not hr:
            return left["key"], f"{left['label']}（{left['signs'][0]}）"
        if hr and not hl:
            return right["key"], f"{right['label']}（{right['signs'][0]}）"
        if hl and hr:
            return "?", "两个符号同时出现，无法判定"
        return "?", "没认出刺激符号（等补识别规则）"

    # 阶段 2：字母 R → 左键，其他字母 → 右键
    m = re.search(r"(?:^|[^A-Za-z])([A-Za-z])(?![A-Za-z])", s)
    if m:
        letter = m.group(1).upper()
        if letter == "R":
            return left["key"], "字母 R → " + left["label"]
        return right["key"], f"字母 {letter} ≠ R → " + right["label"]
    return "?", "没认出单个字母"


# 状态机 / 决策
# --------------------------------------------------------------------------- #

class Engine:
    def __init__(self, mode: str, wait_ms: int, writer: JsonlWriter,
                 cfg: Optional[Dict[str, Any]] = None) -> None:
        self.cfg = cfg or load_config()
        self.mode = mode
        self.wait_ms = max(0, int(wait_ms))
        self.writer = writer
        self.lock = threading.RLock()
        self.stage = int(self.cfg.get("stage", 1))
        self.left_key = self.cfg["left"]["key"].lower()
        self.right_key = self.cfg["right"]["key"].lower()
        self.colors = {
            self.left_key: GREEN,
            self.right_key: BLUE,
            "NONE": RED,
            "?": YELLOW,
        }
        self.trial = 0
        self.current: Dict[str, Any] = {
            "trial": 0,
            "key": "NONE",
            "reason": "空闲",
            "overridden": False,
            "at": time.time(),
        }
        self._timers: Dict[int, threading.Timer] = {}
        self._controller = None
        self._listener = None
        if mode == "auto":
            if keyboard is None:
                raise RuntimeError(f"auto 模式需要 pynput，但导入失败：{_PYNPUT_ERR}")
            self._controller = keyboard.Controller()
        # dom 打印节流
        self._dom_win = time.time()
        self._dom_printed = 0
        self._dom_suppressed = 0

    # ---------------- 对外入口 ---------------- #

    def handle(self, etype: str, detail: Any, t_client: Any = None) -> int:
        etype = (etype or "unknown").strip().lower()
        with self.lock:
            stage_snapshot = self.stage
            # stim 会开启新 trial，日志里直接记它对应的 trial 号（而不是上一题）
            trial_snapshot = self.trial + (1 if etype == "stim" else 0)

        if isinstance(detail, str) and len(detail) > 20000:
            stored_detail: Any = detail[:20000] + "…<truncated>"
        else:
            stored_detail = detail

        seq = self.writer.write(
            {
                "type": etype,
                "detail": stored_detail,
                "t_client": t_client,
                "stage": stage_snapshot,
                "trial": trial_snapshot,
                "mode": self.mode,
            }
        )

        if etype == "stim":
            self._on_stim(detail)
        elif etype == "sound":
            self._on_sound(detail)
        elif etype == "start":
            self._on_start(detail)
        elif etype == "canvas_warning":
            self._on_canvas_warning(detail)
        elif etype == "canvas":
            self._on_canvas(detail)
        elif etype == "dom":
            self._on_dom(detail)
        else:
            emit(f"{GREY}[{datetime.now():%H:%M:%S}] #{seq} {etype}  {_clip(_fmt_detail(detail), 90)}{RESET}")
        return seq

    # ---------------- 事件处理 ---------------- #

    def _on_stim(self, detail: Any) -> None:
        with self.lock:
            self.trial += 1
            tid = self.trial
            key, reason = decide_stim(self.stage, detail, self.cfg)
            self.current = {
                "trial": tid,
                "key": key,
                "reason": reason,
                "overridden": False,
                "at": time.time(),
            }
            old = self._timers.pop(tid - 1, None) if tid > 1 else None
        if old is not None:
            old.cancel()

        stage_name = "阶段1 符号" if self.stage == 1 else "阶段2 字母 R / 其他"
        sub = f"[{datetime.now():%H:%M:%S}] trial #{tid}  {stage_name}  stim={_clip(_detail_text(detail), 60)}"
        note = f"→ {reason} · " + (f"按 {key.upper()}" if key in (self.left_key, self.right_key) else "不按键")
        big_word(key, self.colors.get(key, YELLOW), subtitle=sub, note=note)

        if key not in (self.left_key, self.right_key):
            return
        if self.mode != "auto" or self._controller is None:
            return

        timer = threading.Timer(self.wait_ms / 1000.0, self._do_press, args=(tid, key))
        timer.daemon = True
        with self.lock:
            self._timers[tid] = timer
        timer.start()

    def _do_press(self, tid: int, key: str) -> None:
        with self.lock:
            if self.current["trial"] != tid:      # 已经被新的 stim 顶掉了
                return
            if self.current["overridden"]:
                emit(f"{GREY}    ↳ trial #{tid} 已收到 sound，放弃按键（NONE）{RESET}")
                return
        try:
            self._controller.press(key)
            time.sleep(0.03)
            self._controller.release(key)
            emit(f"{self.colors.get(key, YELLOW)}{BOLD}    ↳ 已真实按下 {key}（trial #{tid}）{RESET}")
        except Exception as exc:
            emit(f"{RED}    ↳ 按键失败：{exc}{RESET}")

    def _on_sound(self, detail: Any) -> None:
        with self.lock:
            was = self.current["key"]
            tid = self.current["trial"]
            changed = was in (self.left_key, self.right_key) and not self.current["overridden"]
            self.current["overridden"] = True
            self.current["key"] = "NONE"
            self.current["reason"] = "sound 事件覆盖为 NONE"
        if changed:
            big_word(
                "NONE",
                RED,
                subtitle=f"[{datetime.now():%H:%M:%S}] trial #{tid}  sound → 改判 NONE",
                note="这一题有声，不按键",
            )
        else:
            emit(f"{RED}[{datetime.now():%H:%M:%S}] #{self.writer._seq} sound → NONE（当前无待判 trial）{RESET}")

    def _on_start(self, detail: Any) -> None:
        with self.lock:
            self.trial = 0
            self.current = {"trial": 0, "key": "NONE", "reason": "等待第一题", "overridden": False, "at": time.time()}
        emit("")
        emit(f"{BOLD}{CYAN}════════════ 检测到 start：新一轮测试开始，trial 计数归零 ════════════{RESET}")
        emit(f"{GREY}    detail: {_clip(_fmt_detail(detail), 100)}{RESET}")

    def _on_canvas_warning(self, detail: Any) -> None:
        emit(f"{BOLD}{YELLOW}[{datetime.now():%H:%M:%S}] ⚠ canvas_warning：{_clip(_fmt_detail(detail), 110)}{RESET}")

    def _on_canvas(self, detail: Any) -> None:
        emit(f"{GREY}[{datetime.now():%H:%M:%S}] canvas {_clip(_fmt_detail(detail), 90)}{RESET}")

    def _on_dom(self, detail: Any) -> None:
        # dom 事件量最大：限速打印，避免把大字决策刷走
        now = time.time()
        with self.lock:
            if now - self._dom_win >= 1.0:
                self._dom_win = now
                self._dom_printed = 0
            if self._dom_printed >= 10:
                self._dom_suppressed += 1
                return
            self._dom_printed += 1
            suppressed = self._dom_suppressed
            self._dom_suppressed = 0
        text = _clip(_fmt_detail(detail), 84)
        emit(f"{GREY}[{datetime.now():%H:%M:%S}] #{self.writer._seq} dom  {text}{RESET}")
        if suppressed:
            emit(f"{GREY}        … 另有 {suppressed} 条 dom 只写入 log.jsonl{RESET}")

    # ---------------- 阶段切换 ---------------- #

    def set_stage(self, stage: int) -> None:
        if stage not in (1, 2):
            return
        with self.lock:
            if self.stage == stage:
                emit(f"{YELLOW}阶段已经是 {stage}{RESET}")
                return
            self.stage = stage
            self.current["key"] = "NONE"
            self.current["reason"] = "阶段已切换"
        lk, rk = self.left_key.upper(), self.right_key.upper()
        rule = (f"{self.cfg['left']['label']} → {lk}，{self.cfg['right']['label']} → {rk}"
                if stage == 1 else f"字母 R → {lk}，其他字母 → {rk}")
        emit("")
        emit(f"{BOLD}{YELLOW}==============================  STAGE {stage}  =============================={RESET}")
        emit(f"{BOLD}{YELLOW}    {rule}{RESET}")
        emit("")

    def start_keyboard_listener(self) -> None:
        if keyboard is None:
            emit(f"{RED}未安装 pynput，数字键切换阶段不可用（pip install pynput）{RESET}")
            return

        def on_press(key) -> None:
            try:
                ch = getattr(key, "char", None)
            except Exception:
                ch = None
            if ch in ("1", "2"):
                self.set_stage(int(ch))

        try:
            self._listener = keyboard.Listener(on_press=on_press)
            self._listener.daemon = True
            self._listener.start()
        except Exception as exc:
            emit(f"{RED}键盘监听启动失败：{exc}{RESET}")


def _fmt_detail(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    try:
        return json.dumps(detail, ensure_ascii=False)
    except Exception:
        return str(detail)


# --------------------------------------------------------------------------- #
# Flask
# --------------------------------------------------------------------------- #

app = Flask(__name__)
ENGINE: Optional[Engine] = None


@app.after_request
def _cors(resp):
    # 正常走 GM_xmlhttpRequest 不需要 CORS，这里只是给手工 curl / fetch 调试留个后路
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/evt", methods=["POST", "OPTIONS"])
def evt():
    if request.method == "OPTIONS":
        return ("", 204)
    if ENGINE is None:
        return jsonify(ok=False, error="engine not ready"), 503

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="body must be JSON object {t,type,detail}"), 400

    seq = ENGINE.handle(str(data.get("type", "unknown")), data.get("detail"), data.get("t"))
    return jsonify(ok=True, seq=seq)


@app.route("/health", methods=["GET"])
def health():
    if ENGINE is None:
        return jsonify(ok=False), 503
    with ENGINE.lock:
        state = {
            "ok": True,
            "mode": ENGINE.mode,
            "stage": ENGINE.stage,
            "wait_ms": ENGINE.wait_ms,
            "axis": ENGINE.cfg.get("axis"),
            "left": ENGINE.cfg["left"],
            "right": ENGINE.cfg["right"],
            "trial": ENGINE.trial,
            "verdict": ENGINE.current["key"],
            "log": ENGINE.writer.path,
        }
    return jsonify(state)


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>测试辅助 · 本地服务</title>
<style>
  body{font-family:"Microsoft YaHei",system-ui,sans-serif;background:#0f1115;color:#e9edf3;margin:0;padding:40px}
  .card{max-width:660px;margin:0 auto;background:#171a21;border:1px solid #2a2f3a;border-radius:14px;padding:28px 30px}
  h1{font-size:22px;margin:0 0 10px}
  p,li{line-height:1.9;color:#b9c0cc}
  .btn{display:inline-block;margin:16px 0 10px;padding:12px 22px;background:#2ea043;color:#fff;text-decoration:none;border-radius:10px;font-weight:600}
  code{background:#0b0d11;padding:2px 6px;border-radius:5px;color:#8fd3ff}
</style>
</head>
<body>
<div class="card">
  <h1>本地服务在跑</h1>
  <p>端口 8765，事件入口 <code>/evt</code>。服务端窗口会在下面两种情况出大字：收到 stim，或收到 sound。</p>
  <a class="btn" href="/helper.user.js">① 点这里安装 Tampermonkey 脚本</a>
  <ul>
    <li>Tampermonkey 会弹安装页，点“安装”确认</li>
    <li>回到测试页按 F5 刷新一次（脚本是 document-start，必须刷新才生效）</li>
    <li>再点 Tampermonkey 图标，应该能看到脚本名，而不是“没有运行中的脚本”</li>
    <li>跑一小段后，同目录的 <code>log.jsonl</code> 就会开始长</li>
  </ul>
</div>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    return app.response_class(INDEX_HTML, mimetype="text/html")


@app.route("/helper.user.js", methods=["GET"])
def userscript():
    """让 Edge 直接打开这个地址就能触发 Tampermonkey 的安装页。"""
    path = os.path.join(BASE_DIR, "helper.user.js")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            body = fh.read()
    except Exception as exc:
        return f"read userscript failed: {exc}", 500
    resp = app.response_class(body, mimetype="text/javascript")
    resp.headers["Content-Type"] = "text/javascript; charset=utf-8"
    resp.headers["Cache-Control"] = "no-cache, no-store"
    return resp


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #

def main() -> None:
    global ENGINE

    parser = argparse.ArgumentParser(description="测试辅助本地服务端")
    parser.add_argument("--mode", choices=["hint", "auto"], default=None,
                        help="hint=只显示不按键；auto=用 pynput 真实按键。默认读 config.json")
    parser.add_argument("--wait-ms", type=int, default=None,
                        help="auto 模式下 stim 后等待多少毫秒再按。默认读 config.json")
    parser.add_argument("--port", type=int, default=None, help="监听端口，默认 8765")
    parser.add_argument("--log", default=None, help="日志文件路径，默认脚本同目录 log.jsonl")
    parser.add_argument("--config", default=None, help="config.json 路径，默认脚本同目录")
    args = parser.parse_args()

    cfg = load_config(args.config)
    mode = args.mode or cfg["mode"]
    wait_ms = args.wait_ms if args.wait_ms is not None else cfg["wait_ms"]
    port = args.port or cfg["port"]
    log = args.log or os.path.join(BASE_DIR, CONFIG["log_name"])

    enable_console()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)   # 别让每条请求刷屏

    writer = JsonlWriter(log)

    try:
        ENGINE = Engine(mode, wait_ms, writer, cfg)
    except RuntimeError as exc:
        emit(f"{RED}{exc}{RESET}")
        emit(f"{YELLOW}已降级为 hint 模式（只显示，不按键）{RESET}")
        ENGINE = Engine("hint", wait_ms, writer, cfg)

    lk, rk = ENGINE.left_key.upper(), ENGINE.right_key.upper()
    ll, rl = ENGINE.cfg["left"]["label"], ENGINE.cfg["right"]["label"]
    emit("")
    emit(f"{BOLD}{CYAN}  测试辅助 · 本地服务{RESET}")
    emit(f"{CYAN}  模式：{RESET}{BOLD}{ENGINE.mode}{RESET}    "
         f"{CYAN}等待窗口 WAIT_MS：{RESET}{ENGINE.wait_ms}    "
         f"{CYAN}阶段：{RESET}{ENGINE.stage}")
    emit(f"{CYAN}  监听：{RESET}http://{CONFIG['host']}:{port}/evt")
    emit(f"{CYAN}  日志：{RESET}{writer.path}")
    emit(f"{GREY}  按数字键 1 / 2 切换阶段；Ctrl+C 退出{RESET}")
    emit("")
    emit(f"{BOLD}  {GREEN}{lk}{RESET} = {ll}（阶段1）/ 字母 R（阶段2）"
         f"    {BOLD}{BLUE}{rk}{RESET} = {rl}（阶段1）/ 其他字母（阶段2）"
         f"    {BOLD}{RED}NONE{RESET} = 有声覆盖")
    emit(f"{DIM}  颜色：左键绿 / 右键蓝 / NONE 红；sound 事件会把当前 trial 立刻改判 NONE{RESET}")
    emit("")

    ENGINE.start_keyboard_listener()

    try:
        app.run(host=CONFIG["host"], port=port, threaded=True, use_reloader=False, debug=False)
    except KeyboardInterrupt:
        pass
    finally:
        if ENGINE._listener is not None:
            ENGINE._listener.stop()
        emit(f"{GREY}服务已停止{RESET}")


if __name__ == "__main__":
    main()
