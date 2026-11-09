"""
校准流程定义 + 分析（和界面分开，方便用模拟数据测试）。

流程：休息看中间 → 右/左/上/下各看 2 次（每次先回中间）→ 咬牙 3 次 → 挑眉 3 次
分析：
    眼电：每个方向的幅度 = 看向目标时的中值 − 前一段看中间时的中值（取多次的中值）
    肌电：休息包络水平 rest、用力时包络 active，
          阈值 hi = rest + 0.35 (active − rest)，lo = rest + 0.20 (active − rest)
"""

import json
import time

import numpy as np

import config
from dsp import RealtimeProcessor

# (阶段名, 时长 s, 屏幕提示, 注视点位置 (x, y) 归一化 -1..1 或 None)
DIRS = {"right": (1, 0), "left": (-1, 0), "up": (0, 1), "down": (0, -1)}
DIR_CN = {"right": "右", "left": "左", "up": "上", "down": "下"}


def build_protocol(reps_eye=2, reps_emg=3):
    p = [("rest", 5.0, "放松，看着中间的点，尽量少眨眼", (0, 0))]
    for _ in range(reps_eye):
        for d, xy in DIRS.items():
            p.append(("center", 2.0, "看中间的点", (0, 0)))
            p.append((d, 2.5, f"只转眼睛，看{DIR_CN[d]}边的点（头不要动）", xy))
    p.append(("center", 2.0, "看中间的点", (0, 0)))
    for i in range(reps_emg):
        p.append(("emg_rest", 2.5, f"准备咬牙（{i + 1}/{reps_emg}），脸部放松", (0, 0)))
        p.append(("jaw", 1.5, "用力咬紧牙齿！", (0, 0)))
    for i in range(reps_emg):
        p.append(("emg_rest", 2.5, f"准备挑眉（{i + 1}/{reps_emg}），脸部放松", (0, 0)))
        p.append(("brow", 1.5, "用力挑起眉毛！", (0, 0)))
    p.append(("emg_rest", 2.0, "放松，马上结束", (0, 0)))
    return p


def segments(protocol, rate):
    """每个阶段对应的样本区间 [(name, start, end), ...]"""
    out, s = [], 0
    for name, dur, *_ in protocol:
        n = int(round(dur * rate))
        out.append((name, s, s + n))
        s += n
    return out


def _core(a, s, e, frac=0.6):
    """取一段的后 frac 部分（跳过反应时间和扫视过程）"""
    n = e - s
    return a[e - int(n * frac):e]


def analyze(raw, segs, rate=config.RATE):
    """raw: (n, 4) µV 原始数据；segs: [(阶段名, 起始样本, 结束样本), ...]
    → (calib dict, 报告文本列表)"""
    proc = RealtimeProcessor(rate)
    y = proc.process(raw)
    segs = [(n, s, min(e, len(y))) for n, s, e in segs if s < len(y) - 10]
    report = []

    # ---------- 眼电 ----------
    amps = {d: [] for d in DIRS}
    prev_center = None
    for name, s, e in segs:
        if name in ("center", "rest"):
            prev_center = np.median(_core(y[:, :2], s, e), axis=0)
        elif name in DIRS and prev_center is not None:
            m = np.median(_core(y[:, :2], s, e), axis=0)
            ch = 0 if name in ("right", "left") else 1
            amps[name].append(m[ch] - prev_center[ch])
    eog = {}
    for d in DIRS:
        if not amps[d]:
            raise ValueError(f"校准数据里没有“{DIR_CN[d]}”这一段")
        eog[d] = float(np.median(amps[d]))
    rest = [(s, e) for n, s, e in segs if n == "rest"][0]
    noise = np.std(_core(y[:, :2], *rest, frac=0.8), axis=0)
    last_center = [(s, e) for n, s, e in segs if n == "center"][-1]
    base = np.median(_core(y[:, :2], *last_center), axis=0)
    eog.update(base_h=float(base[0]), base_v=float(base[1]),
               noise_h=float(noise[0]), noise_v=float(noise[1]))

    ok = True
    for axis, (a, b), nz in (("水平", ("right", "left"), noise[0]), ("垂直", ("up", "down"), noise[1])):
        va, vb = eog[a], eog[b]
        report.append(f"{axis}眼电：{DIR_CN[a]} {va:+.0f} µV，{DIR_CN[b]} {vb:+.0f} µV，静息噪声 {nz:.1f} µV")
        if np.sign(va) == np.sign(vb):
            ok = False
            report.append(f"  ❌ {DIR_CN[a]}和{DIR_CN[b]}的符号相同：电极位置不对，或者校准时眼睛没有跟着点走")
        for d, v in ((a, va), (b, vb)):
            if abs(v) < 5 * nz or abs(v) < 20:
                ok = False
                report.append(f"  ⚠ 看{DIR_CN[d]}的幅度太小（{v:+.0f} µV）：检查电极是否贴牢、是否只转了眼睛")

    # ---------- 肌电 ----------
    emg = {}
    rest_idx = [k for k, (n, s, e) in enumerate(segs) if n in ("rest", "center", "emg_rest")]
    for ch, key in ((2, "jaw"), (3, "brow")):
        rest_env = np.concatenate([_core(y[:, ch], segs[k][1], segs[k][2], 0.8) for k in rest_idx])
        r = float(np.median(rest_env))
        r99 = float(np.percentile(rest_env, 99))
        act = [float(np.median(_core(y[:, ch], s, e, 0.7))) for n, s, e in segs if n == key]
        if not act:
            raise ValueError(f"校准数据里没有 {key} 段")
        a = float(np.median(act))
        hi = max(r + 0.35 * (a - r), r99 * 1.3)
        lo = max(r + 0.20 * (a - r), r99 * 1.1)
        lo = min(lo, hi * 0.8)
        emg.update({f"{key}_rest": r, f"{key}_active": a, f"{key}_hi": hi, f"{key}_lo": lo})
        cn = "咬牙" if key == "jaw" else "挑眉"
        report.append(f"{cn}肌电：静息 {r:.1f} µV，用力 {a:.1f} µV（{a / max(r, 1e-9):.1f} 倍），阈值 {hi:.1f} µV")
        if a < 3 * r:
            if key == "jaw":
                ok = False
                report.append(f"  ⚠ {cn}信号和静息差别不大：检查电极位置，{cn}时用力一点")
            else:
                # 挑眉识别不出来时直接禁用，避免低阈值误触发；右键改用“咬牙 0.6–1 秒”
                emg.update(brow_hi=1e9, brow_lo=1e9, brow_disabled=True)
                report.append("  ⚠ 挑眉信号太弱，已禁用挑眉；右键改为“咬牙 0.6–1 秒”，重新居中改为暂停后再继续")

    # 串扰：咬牙时挑眉通道会不会超过阈值（反之亦然）
    for key, other, och in (("jaw", "brow", 3), ("brow", "jaw", 2)):
        cross = [float(np.median(_core(y[:, och], s, e, 0.7))) for n, s, e in segs if n == key]
        if cross and np.median(cross) > emg[f"{other}_hi"]:
            report.append(f"  ⚠ 做“{key}”时“{other}”通道也超过了阈值（串扰），程序会只认更强的那个，但最好把两组电极分开一点")

    calib = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rate": rate, "eog": eog, "emg": emg, "ok": ok}
    report.append("✅ 校准通过" if ok else "⚠ 校准有问题，看上面的提示，调整电极后再校准一次（也可以先试用）")
    return calib, report


def save(calib, path=config.CALIB_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(calib, f, ensure_ascii=False, indent=2)


def load(path=config.CALIB_FILE):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
