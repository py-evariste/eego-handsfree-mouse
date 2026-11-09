"""
实时波形：贴电极时用来检查信号（最近 10 秒）。

    python live_view.py                  # 真实放大器
    python live_view.py --sim            # 模拟器（方向键/J/B）
    python live_view.py --save rec.npz   # 同时把原始数据录下来（关窗口时保存）

四行分别是：水平眼电、垂直眼电（µV，低通 10 Hz）、咬牙肌电包络、挑眉肌电包络（µV）。
正常的样子：
    看右 → 水平眼电明显上升（几十到几百 µV），看左 → 下降；看上/下 → 垂直眼电升/降；眨眼 → 垂直眼电尖峰
    咬牙 → 第三行明显抬高（静息的 5 倍以上）；挑眉 → 第四行抬高
如果某行是一条很粗的噪声带或一直贴着上下边，多半是电极没贴牢。
"""

import argparse
import sys

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import config
from dsp import RealtimeProcessor
from source import make_source

TITLES = ["水平眼电 HEOG (µV)", "垂直眼电 VEOG (µV)", "咬牙肌电包络 (µV)", "挑眉肌电包络 (µV)"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--save", default=None)
    args = ap.parse_args()

    rate = config.RATE
    n = int(args.seconds * rate)
    buf = np.full((n, 4), np.nan)
    raw_log = []
    proc = RealtimeProcessor(rate)
    t = np.arange(n) / rate - args.seconds

    src = make_source(args.sim).start()
    print(f"数据源：{src.info}")
    fig, axes = plt.subplots(4, 1, sharex=True, figsize=(12, 8))
    lines = []
    for ax, title in zip(axes, TITLES):
        (ln,) = ax.plot(t, buf[:, 0], lw=0.8)
        ax.set_title(title, fontsize=10, loc="left")
        ax.grid(alpha=0.3)
        lines.append(ln)
    axes[-1].set_xlabel("时间 (s)")
    fig.tight_layout()
    status = fig.text(0.99, 0.995, "", ha="right", va="top", fontsize=9)

    def update(_):
        nonlocal buf
        raw = src.read()
        if len(raw):
            if args.save:
                raw_log.append(raw)
            y = proc.process(raw)
            k = min(len(y), n)
            buf = np.vstack([buf[k:], y[-k:]])
        for i, (ln, ax) in enumerate(zip(lines, axes)):
            ln.set_ydata(buf[:, i])
            v = buf[:, i][~np.isnan(buf[:, i])]
            if len(v):
                if i < 2:     # 眼电：去掉均值后显示，范围至少 ±100 µV
                    c = np.median(v)
                    r = max(100, np.percentile(np.abs(v - c), 99) * 1.2)
                    ax.set_ylim(c - r, c + r)
                else:
                    ax.set_ylim(0, max(20, np.percentile(v, 99.5) * 1.2))
        status.set_text(f"已收 {src.total} 样本   丢样 {src.dropped}")
        return lines

    anim = FuncAnimation(fig, update, interval=50, cache_frame_data=False)  # noqa: F841
    try:
        plt.show()
    finally:
        src.stop()
        if args.save and raw_log:
            raw = np.concatenate(raw_log)
            np.savez(args.save, raw=raw, signals=config.SIGNALS, rate=rate)
            print(f"已保存 {len(raw)} 个样本到 {args.save}")


if __name__ == "__main__":
    main()
