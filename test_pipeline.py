"""
无硬件的端到端测试：模拟器按脚本生成 EOG/EMG → 校准分析 → 控制逻辑，检查输出动作。

    python test_pipeline.py
"""

import sys

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

import calibration
import config
from controller import Controller
from dsp import RealtimeProcessor
from source import SimSource

RATE = config.RATE
REST = {"x": 0, "y": 0, "jaw": False, "brow": False, "blink": False}


def timeline_script(events):
    """events: [(t0, t1, dict), ...] → script(t)"""
    def script(t):
        s = dict(REST)
        for t0, t1, st in events:
            if t0 <= t < t1:
                s.update(st)
        return s
    return script


def run_calibration():
    protocol = calibration.build_protocol()
    segs = calibration.segments(protocol, RATE)
    events, t = [], 0.0
    for name, dur, *_ in protocol:
        st = {}
        if name in calibration.DIRS:
            # 人的反应：0.3 s 后眼睛才转过去
            x, y = calibration.DIRS[name]
            events.append((t + 0.3, t + dur, {"x": x, "y": y}))
        elif name in ("jaw", "brow"):
            events.append((t + 0.3, t + dur, {name: True}))
        t += dur
    events.append((7.0, 7.01, {"blink": True}))      # 中途眨一次眼
    sim = SimSource(script=timeline_script(events), seed=1)
    raw = sim.generate(segs[-1][2])
    calib, report = calibration.analyze(raw, segs, RATE)
    print("\n".join(report))
    e = calib["eog"]
    assert e["right"] > 150 and e["left"] < -150, e
    assert e["up"] > 120 and e["down"] < -120, e
    assert calib["ok"], "模拟数据校准应当通过"
    return calib


def run_control(calib, mode):
    ev = [
        (2.0, 4.0, {"x": 1}),              # 看右 2 s → 光标右移
        (5.0, 5.01, {"blink": True}),      # 眨眼 → 不应垂直移动
        (6.0, 6.3, {"jaw": True}),         # 咬牙短 → 左键
        (7.5, 7.8, {"brow": True}),        # 挑眉短 → 右键
        (9.0, 10.3, {"jaw": True}),        # 咬牙长 → 暂停
        (11.0, 12.5, {"x": -1}),           # 暂停中看左 → 不动
        (13.0, 14.3, {"jaw": True}),       # 咬牙长 → 继续
        (15.0, 17.0, {"y": 1}),            # 看上 → 上移（屏幕 y 减小）
        (18.0, 19.5, {"x": -1}),           # 看左 → 左移
    ]
    T = 21.0
    sim = SimSource(script=timeline_script(ev), seed=2)
    proc = RealtimeProcessor(RATE)
    ctl = Controller(calib, screen=(1920, 1080), cfg={"mode": mode})
    # 先 2 s 基线（和 mouse_control 一样）
    first = proc.process(sim.generate(int(1.0 * RATE)))
    ctl.base = np.median(first[RATE // 3:, :2], axis=0)
    ctl.t = sim.t
    ctl.recent.extend(first[-ctl.recent.maxlen:, :2])
    blk = int(0.02 * RATE)
    pos = np.array([960.0, 540.0])
    trace, acts = [], []
    while sim.t < T:
        y = proc.process(sim.generate(blk))
        for a in ctl.step(y):
            if a[0] == "move":
                pos += a[1:]
            else:
                acts.append((round(sim.t, 2), a))
        trace.append((sim.t, *pos))
    tr = np.array(trace)

    def at(t):
        return tr[np.searchsorted(tr[:, 0], t)][1:]

    print(f"\n[{mode}] 非移动动作：{acts}")
    for t in (2, 3.9, 5.8, 11, 12.6, 15, 16.9, 18, 19.4):
        print(f"  t={t:5.1f}s 光标 {at(t).round()}")
    kinds = [a for _, a in acts]
    assert ("click", "left") in kinds, "咬牙短应当左键单击"
    assert ("click", "right") in kinds, "挑眉短应当右键单击"
    assert kinds.count(("pause", True)) == 1 and kinds.count(("pause", False)) == 1, kinds
    assert at(3.9)[0] - at(2.0)[0] > 300, "看右应当右移"
    assert abs(at(5.8)[1] - at(4.6)[1]) < 15, "眨眼不应造成垂直移动"
    assert abs(at(12.6)[0] - at(11.0)[0]) < 5, "暂停时不应移动"
    assert at(15.0)[1] - at(16.9)[1] > 150, "看上应当上移"
    assert at(18.0)[0] - at(19.4)[0] > 200, "看左应当左移"
    # 点击时光标基本不动
    assert np.linalg.norm(at(6.6) - at(5.9)) < 10


def test_filter_chunking():
    """分块实时处理和一次性处理结果一致"""
    sim = SimSource(script=timeline_script([(1, 2, {"x": 1, "jaw": True})]), seed=3)
    raw = sim.generate(3 * RATE)
    a = RealtimeProcessor(RATE).process(raw)
    p = RealtimeProcessor(RATE)
    b = np.vstack([p.process(raw[i:i + 7]) for i in range(0, len(raw), 7)])
    assert np.allclose(a, b, atol=1e-6), np.abs(a - b).max()


if __name__ == "__main__":
    test_filter_chunking()
    print("✅ 分块滤波一致")
    calib = run_calibration()
    print("✅ 校准分析")
    run_control(calib, "joystick")
    print("✅ 摇杆模式")
    run_control(calib, "absolute")
    print("✅ 绝对模式")
