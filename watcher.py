#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stim 像素识别器（降级方案：不依赖 DOM，不需要 opencv）

为什么需要它：
    PsychoJS / PsychoPy 导出的任务，刺激是画在 canvas 上的，DOM 里查不到箭头和字母。
    本任务：白底黑字，屏幕中央同一时刻只有一个大字形：
        ← / →      箭头（本任务的方向刺激）
        +          注视点
        正确 / 未反应 / 停止失败   反馈文字

判定思路（纯几何，无模板）：
    1. 只截屏幕中央 ROI（默认 30% 宽 × 30% 高），整屏二值化取墨迹
    2. 算墨迹包围盒，做列投影：
         - 单个字形（箭头 / 注视点）→ 只有 1 个连通列簇
         - 多字文本（正确 / 未反应 / 停止失败 / 说明文字）→ 2 个以上列簇
         这一步就把反馈文字和说明文字全挡掉了，不依赖字号，换分辨率也稳
    3. 宽高比 1.4 ~ 3.9：箭头约 2.6（横向长），注视点 "+" 约 1.0（被挡掉）
    4. 墨迹质心相对包围盒中心的水平偏移 dx：dx < 0 → ←，dx > 0 → →
       箭头头部墨多，质心偏向箭头指向的那一侧；"+" 质心居中，dx≈0

识别到就 POST 一条 type="stim" 给 server.py；
阶段切换、F/J 决策、sound 覆盖、auto 真实按键，全部复用服务端逻辑。

用法：
    python watcher.py                      # 正式跑
    python watcher.py --dry                 # 只打印不发送
    python watcher.py --calibrate           # 采 8 秒画面临界值，用来校准阈值
    python watcher.py --monitor 2           # 任务在第二个显示器
    python watcher.py --cx 0.5 --cy 0.45 --roi-w 0.35 --roi-h 0.30
"""

from __future__ import annotations

import argparse
import json
import socket
import time
import urllib.request

import mss
import numpy as np

CONFIG = {
    "url": "http://127.0.0.1:8765/evt",
    "monitor": "primary",      # "primary" 自动取主屏；也可以写 0(全部拼接) / 1 / 2
    "fps": 12.0,
    "cx": 0.5,
    "cy": 0.5,
    "roi_w": 0.30,
    "roi_h": 0.30,
    "thresh": 110,
    "aspect_min": 1.1,
    "aspect_max": 4.5,
    "dx_min": 0.02,
    "min_pixels": 400,
    "min_width": 40,
    "min_gap": 0.25,
    "dry": False,
    "verbose": False,
    "calibrate": False,
}


_INSTANCE_LOCK = None


def acquire_single_instance() -> bool:
    """同一个时间只允许一个 watcher，否则会重复发 stim、重复按键。"""
    global _INSTANCE_LOCK
    if _INSTANCE_LOCK is not None:
        return True
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 8799))
        s.listen(1)
    except OSError:
        s.close()
        return False
    _INSTANCE_LOCK = s
    return True


def _sct():
    """mss 10.x 开始弃用 mss.mss()，优先用 mss.MSS。"""
    cls = getattr(mss, "MSS", None)
    return cls() if cls else mss.mss()


def _monitor(sct, spec):
    if isinstance(spec, str) and spec.strip().lower() in ("primary", "auto", ""):
        for m in sct.monitors[1:]:
            if m.get("is_primary"):
                return m
        return sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
    return sct.monitors[int(spec)]


def _crop(img: np.ndarray, cfg: dict) -> np.ndarray:
    h, w = img.shape[:2]
    x0 = int(w * cfg["cx"] - w * cfg["roi_w"] / 2)
    x1 = int(w * cfg["cx"] + w * cfg["roi_w"] / 2)
    y0 = int(h * cfg["cy"] - h * cfg["roi_h"] / 2)
    y1 = int(h * cfg["cy"] + h * cfg["roi_h"] / 2)
    return img[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]


def _metrics(mask: np.ndarray, screen_h: int) -> dict | None:
    n = int(mask.sum())
    if n < 50:
        return None
    ys, xs = np.nonzero(mask)
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    if bw <= 0 or bh <= 0:
        return None

    # 列投影：数“连通的列簇”。单字 = 1 簇；多字文本 = 多簇。
    cols = mask[y0:y1 + 1, x0:x1 + 1].sum(axis=0) > 0
    runs = 0
    i = 0
    length = int(cols.shape[0])
    while i < length:
        if not cols[i]:
            i += 1
            continue
        runs += 1
        gap = 0
        while i < length:
            if cols[i]:
                gap = 0
            else:
                gap += 1
                if gap > 1:      # 允许 1px 抗锯齿断开，超过就算两个字之间
                    break
            i += 1

    cx_ink = float(xs.mean())
    dx = (cx_ink - (x0 + x1) / 2.0) / float(bw)
    return {
        "bw": bw,
        "bh": bh,
        "px": n,
        "runs": runs,
        "aspect": round(bw / float(bh), 2),
        "dx": round(dx, 3),
        "h_ratio": round(bh / float(screen_h), 3),
        "bbox": [x0, y0, bw, bh],
    }


def classify(img: np.ndarray, cfg: dict) -> dict | None:
    """img: 整屏 BGRA。命中返回 {'text': '←'/'→', ...}，否则 None。"""
    roi = _crop(img, cfg)
    h = img.shape[0]

    b = roi[..., 0].astype(np.int32)
    g = roi[..., 1].astype(np.int32)
    r = roi[..., 2].astype(np.int32)
    gray = (b * 114 + g * 587 + r * 299) // 1000
    mask = gray < cfg["thresh"]

    m = _metrics(mask, h)
    if m is None:
        return None
    if m["px"] < cfg["min_pixels"] or m["bw"] < cfg["min_width"]:
        return None
    if m["runs"] != 1:                        # 多字 = 文本，不是刺激
        return None
    if not (cfg["aspect_min"] <= m["aspect"] <= cfg["aspect_max"]):
        return None                            # "+" 等竖长/方形字形
    if abs(m["dx"]) < cfg["dx_min"]:
        return None                            # 质心居中 = 对称字形

    return dict(m, text="←" if m["dx"] < 0 else "→")


def post_stim(cfg: dict, hit: dict) -> None:
    payload = {
        "t": int(time.time() * 1000),
        "type": "stim",
        "detail": {
            "kind": "pixel",
            "text": hit["text"],
            "dx": hit["dx"],
            "aspect": hit["aspect"],
            "h_ratio": hit["h_ratio"],
            "src": "watcher",
        },
    }
    if cfg["dry"]:
        return
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(cfg["url"], data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=1.0).read()
    except Exception as exc:
        print(f"  ! post failed: {exc}")


def calibrate(cfg: dict, seconds: float) -> None:
    """不做判定，只把每帧最大的墨迹指标打出来，用来核对阈值。"""
    print(f"calibrate for {seconds:.0f}s, roi={cfg['roi_w']:.0%}x{cfg['roi_h']:.0%} ...")
    print("  time      bw   bh   aspect  runs  dx      h_ratio  px")
    end = time.time() + seconds
    last_print = 0.0
    with _sct() as sct:
        mon = _monitor(sct, cfg["monitor"])
        print(f"  target: {mon.get('width')}x{mon.get('height')} @ ({mon.get('left')},{mon.get('top')}) {mon.get('name') or ''}")
        while time.time() < end:
            img = np.asarray(sct.grab(mon))
            roi = _crop(img, cfg)
            b = roi[..., 0].astype(np.int32)
            g = roi[..., 1].astype(np.int32)
            r = roi[..., 2].astype(np.int32)
            gray = (b * 114 + g * 587 + r * 299) // 1000
            mask = gray < cfg["thresh"]
            m = _metrics(mask, img.shape[0])
            if m and time.time() - last_print > 0.3:
                last_print = time.time()
                print(f"  {time.strftime('%H:%M:%S')}  {m['bw']:4d} {m['bh']:4d}  "
                      f"{m['aspect']:6.2f}  {m['runs']:4d}  {m['dx']:+.3f}  "
                      f"{m['h_ratio']:.3f}    {m['px']}")
            time.sleep(0.1)
    print("done. 把这段贴回来，就能按你的实际字号把阈值调准。")


def run(cfg: dict) -> None:
    if not acquire_single_instance():
        print("  已经有另一个 watcher 在跑了（127.0.0.1:8799 被占用），这个窗口退出。")
        print("  想要两个就先把旧的那个关掉。")
        return
    state = {"last": None, "last_emit": 0.0, "absent": 0}
    interval = 1.0 / max(1.0, cfg["fps"])

    with _sct() as sct:
        mon = _monitor(sct, cfg["monitor"])
        print(f"watcher  ->  {cfg['url']}   fps={cfg['fps']}  "
              f"roi={cfg['roi_w']:.0%}x{cfg['roi_h']:.0%}  monitor={cfg['monitor']} "
              f"({mon['width']}x{mon['height']})  dry={cfg['dry']}")
        print("  识别到箭头就发 stim；Ctrl+C 退出")

        frames = 0
        hits = 0
        report_at = time.time()

        while True:
            t0 = time.perf_counter()
            try:
                img = np.asarray(sct.grab(mon))
            except Exception as exc:
                print(f"screen grab failed: {exc}")
                time.sleep(0.5)
                continue

            hit = classify(img, cfg)
            now = time.time()

            if hit is None:
                state["absent"] += 1
                if state["absent"] >= 2:
                    state["last"] = None
            else:
                state["absent"] = 0
                if state["last"] is None and (now - state["last_emit"]) > cfg["min_gap"]:
                    state["last"] = hit["text"]
                    state["last_emit"] = now
                    hits += 1
                    print(f"[{time.strftime('%H:%M:%S')}] stim {hit['text']}  "
                          f"dx={hit['dx']:+.2f} aspect={hit['aspect']:.2f} h={hit['h_ratio']:.2f}")
                    post_stim(cfg, hit)

            frames += 1
            if now - report_at > 10:
                el = now - report_at
                print(f"  .. {frames / el:.1f} fps, hits={hits}")
                frames = 0
                report_at = now

            elapsed = time.perf_counter() - t0
            if elapsed < interval:
                time.sleep(interval - elapsed)


def main() -> None:
    ap = argparse.ArgumentParser(description="stim 像素识别（白底黑字箭头）")
    ap.add_argument("--url", default=CONFIG["url"])
    ap.add_argument("--monitor", default=CONFIG["monitor"],
                    help='主屏用 "primary"，或 mss 索引 0/1/2')
    ap.add_argument("--fps", type=float, default=CONFIG["fps"])
    ap.add_argument("--cx", type=float, default=CONFIG["cx"])
    ap.add_argument("--cy", type=float, default=CONFIG["cy"])
    ap.add_argument("--roi-w", type=float, default=CONFIG["roi_w"])
    ap.add_argument("--roi-h", type=float, default=CONFIG["roi_h"])
    ap.add_argument("--thresh", type=int, default=CONFIG["thresh"])
    ap.add_argument("--dx-min", type=float, default=CONFIG["dx_min"])
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--calibrate", action="store_true", help="采 8 秒指标用于校准阈值")
    ap.add_argument("--calibrate-secs", type=float, default=8.0)
    args = ap.parse_args()

    CONFIG.update({
        "url": args.url,
        "monitor": args.monitor,
        "fps": args.fps,
        "cx": args.cx,
        "cy": args.cy,
        "roi_w": args.roi_w,
        "roi_h": args.roi_h,
        "thresh": args.thresh,
        "dx_min": args.dx_min,
        "dry": args.dry,
        "verbose": args.verbose,
        "calibrate": args.calibrate,
    })

    try:
        if CONFIG["calibrate"]:
            calibrate(CONFIG, args.calibrate_secs)
        else:
            run(CONFIG)
    except KeyboardInterrupt:
        print("\nwatcher stopped")


if __name__ == "__main__":
    main()
