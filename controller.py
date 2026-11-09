"""
控制逻辑（纯计算，不碰鼠标，方便测试）。

输入：dsp.RealtimeProcessor 输出的 (n, 4) 块 [heog, veog, jaw 包络, brow 包络]
输出：动作列表，例如 [("move", dx, dy), ("click", "left"), ("pause", True), ("recenter",)]

眼电 → 光标
    joystick（默认）：眼睛往左/右/上/下偏过死区，光标就往那个方向持续移动，偏得越多越快；
                      回到中间就停。眼睛在中间时基线会慢慢跟上漂移。
    absolute        ：光标位置 = 校准映射出来的注视位置（需要经常“重新居中”）。
肌电 → 按键
    咬牙短（0.08–0.6 s）  左键单击（快速咬两次 = 双击）
    咬牙长（≥ 1 s）       暂停 / 继续（暂停时只认这个手势）
    挑眉短                右键单击
    挑眉长（≥ 1 s）       重新居中（先看屏幕中间再挑眉）
做手势时光标冻结，避免咬牙/挑眉带来的眼电伪迹让光标乱跑。
"""

from collections import deque

import numpy as np

import config


class EmgGesture:
    """单个肌电通道的手势识别：带滞回的阈值 + 按持续时间分类。"""

    def __init__(self, name, thr_hi, thr_lo, cfg):
        self.name, self.hi, self.lo, self.cfg = name, thr_hi, thr_lo, cfg
        self.active = False
        self.t_on = 0.0
        self.peak = 0.0
        self.long_fired = False
        self.cool_until = -1.0

    def ratio(self, env):
        return env / self.hi

    def update(self, t, env):
        """返回事件：None / "short" / "long"（长按在达到时长时立即触发，松开不再触发）。"""
        ev = None
        if not self.active:
            if env > self.hi and t >= self.cool_until:
                self.active, self.t_on, self.peak, self.long_fired = True, t, env, False
        else:
            self.peak = max(self.peak, env)
            dur = t - self.t_on
            if not self.long_fired and dur >= self.cfg["long_press"]:
                self.long_fired = True
                ev = "long"
            if env < self.lo:
                self.active = False
                self.cool_until = t + self.cfg["refractory"] if (dur >= self.cfg["click_min"]) else t
                if not self.long_fired and self.cfg["click_min"] <= dur <= self.cfg["click_max"]:
                    ev = "short"
        return ev


class Controller:
    def __init__(self, calib, screen=(1920, 1080), rate=config.RATE, cfg=None):
        self.c = dict(config.CONTROL, **(cfg or {}))
        self.calib = calib
        self.rate = rate
        self.dt = 1.0 / rate
        self.W, self.H = screen
        self.t = 0.0
        e = calib["eog"]
        self.base = np.array([e["base_h"], e["base_v"]], float)
        self.amp = {k: float(e[k]) for k in ("right", "left", "up", "down")}
        m = calib["emg"]
        self.jaw = EmgGesture("jaw", m["jaw_hi"], m["jaw_lo"], self.c)
        self.brow = EmgGesture("brow", m["brow_hi"], m["brow_lo"], self.c)
        self.paused = False
        self.freeze_until = 0.0
        self.v_hold = 0.0                # 垂直偏移已持续时间
        self.frac = np.zeros(2)          # 未满 1 像素的移动量
        self.recent = deque(maxlen=int(0.4 * rate))   # 最近 0.4 s 的眼电，用来求中值（去眨眼）
        self.abs_pos = None
        self.gaze = (0.0, 0.0)

    # ---------- 眼电 → 归一化注视方向 (x 右为正, y 上为正) ----------
    def normalize(self, h, v):
        dh, dv = h - self.base[0], v - self.base[1]
        x = dh / self.amp["right"] if dh / self.amp["right"] > 0 else -dh / self.amp["left"]
        y = dv / self.amp["up"] if dv / self.amp["up"] > 0 else -dv / self.amp["down"]
        return x, y

    def recenter(self):
        if self.recent:
            r = np.array(self.recent)
            self.base = np.median(r, axis=0)
        self.abs_pos = None

    def step(self, block):
        """处理一块数据，返回动作列表。"""
        actions = []
        if len(block) == 0:
            return actions
        # --- 肌电手势（逐样本，时间精确）---
        jaw_on_any = brow_on_any = False
        for k in range(len(block)):
            self.t += self.dt
            ej = self.jaw.update(self.t, block[k, 2])
            eb = self.brow.update(self.t, block[k, 3])
            jaw_on_any |= self.jaw.active
            brow_on_any |= self.brow.active
            # 两个通道同时激活时（串扰），只认相对阈值更强的那个
            if ej and eb:
                if self.jaw.peak / self.jaw.hi >= self.brow.peak / self.brow.hi:
                    eb = None
                else:
                    ej = None
            if ej and self.brow.active and self.brow.peak / self.brow.hi > self.jaw.peak / self.jaw.hi:
                ej = None
            if eb and self.jaw.active and self.jaw.peak / self.jaw.hi > self.brow.peak / self.brow.hi:
                eb = None
            if ej == "long":
                self.paused = not self.paused
                actions.append(("pause", self.paused))
            elif ej == "short" and not self.paused:
                actions.append(("click", "left"))
            elif eb == "short" and not self.paused:
                actions.append(("click", "right"))
            elif eb == "long" and not self.paused:
                self.recenter()
                actions.append(("recenter",))
            self.recent.append(block[k, :2])

        emg_busy = jaw_on_any or brow_on_any or self.jaw.active or self.brow.active
        if emg_busy:
            self.freeze_until = self.t + 0.25

        dt_block = len(block) * self.dt
        # 用 0.4 s 中值：眨眼（~0.2 s 的尖峰）基本不影响
        h_med, v_med = np.median(np.array(self.recent), axis=0)
        h_now, v_now = block[-max(1, int(0.04 * self.rate)):, :2].mean(axis=0)
        x, y = self.normalize(h_now, v_now)
        xm, ym = self.normalize(h_med, v_med)
        self.gaze = (x, y)

        if self.paused or self.t < self.freeze_until:
            self.v_hold = 0.0
            return actions

        dz = self.c["dead_zone"]
        if self.c["mode"] == "joystick":
            # 垂直方向要持续 vertical_hold 秒才算，过滤眨眼
            self.v_hold = self.v_hold + dt_block if abs(y) > dz else 0.0
            yy = ym if self.v_hold >= self.c["vertical_hold"] else 0.0
            vx = self._speed(x)
            vy = self._speed(yy)
            if abs(x) < dz * 0.7 and abs(y) < dz * 0.7:
                # 眼睛在中间：基线慢慢跟随漂移
                a = dt_block / self.c["baseline_tau"]
                self.base += (np.array([h_now, v_now]) - self.base) * a
            move = np.array([vx, -vy]) * dt_block + self.frac
        else:
            # 绝对模式：注视点 → 屏幕坐标，水平用当前值，垂直用中值（去眨眼）
            tx = self.W / 2 + np.clip(x, -1.1, 1.1) * self.W * 0.45
            ty = self.H / 2 - np.clip(ym, -1.1, 1.1) * self.H * 0.45
            target = np.array([tx, ty])
            if self.abs_pos is None:
                self.abs_pos = target
            a = min(1.0, dt_block / self.c["absolute_smoothing"])
            new = self.abs_pos + (target - self.abs_pos) * a
            move = new - self.abs_pos + self.frac
            self.abs_pos = new
        step = np.trunc(move)
        self.frac = move - step
        if step[0] or step[1]:
            actions.append(("move", int(step[0]), int(step[1])))
        return actions

    def _speed(self, v):
        dz = self.c["dead_zone"]
        a = abs(v)
        if a <= dz:
            return 0.0
        s = min(1.0, (a - dz) / (1 - dz)) ** self.c["speed_exponent"]
        return np.sign(v) * s * self.c["max_speed"]
