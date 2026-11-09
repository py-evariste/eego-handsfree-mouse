"""
数据源：后台线程不停地取数据，主程序随时调用 read() 拿到新样本 (n, 4)，单位 µV，
顺序为 config.SIGNALS（heog, veog, jaw, brow）。

    EegoSource  真实放大器（eego EE-410）
    SimSource   模拟器，没有硬件时用来测试整套程序：
                  方向键 ← → ↑ ↓   眼睛往那个方向看
                  J 键（按住）      咬牙
                  B 键（按住）      挑眉
                  另外会随机眨眼、有基线漂移、噪声和 50 Hz 工频干扰
                也可以传入 script(t) 函数，按脚本生成数据（自动测试用）

make_source(sim=False) 按参数选一个。
"""

import threading
import time

import numpy as np

import config


class _ThreadedSource:
    def __init__(self):
        self._buf = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.error = None
        self.total = 0          # 已收到的样本数
        self.dropped = 0        # 检测到的丢样数

    def start(self):
        self._thread = threading.Thread(target=self._run_safe, daemon=True)
        self._thread.start()
        return self

    def _run_safe(self):
        try:
            self._run()
        except Exception as e:      # 把后台线程的错误交给主线程
            self.error = e

    def _push(self, block):
        with self._lock:
            self._buf.append(block)
            self.total += len(block)

    def read(self):
        if self.error:
            raise RuntimeError(f"数据源出错：{self.error}") from self.error
        with self._lock:
            blocks, self._buf = self._buf, []
        if not blocks:
            return np.empty((0, 4))
        return np.concatenate(blocks)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


class EegoSource(_ThreadedSource):
    def __init__(self, dll_path=config.DLL_PATH, rate=config.RATE, bipolar_range=config.BIPOLAR_RANGE,
                 channels=config.CHANNELS):
        super().__init__()
        self.dll_path, self.rate, self.range = dll_path, rate, bipolar_range
        self.cols = [channels[s] for s in config.SIGNALS]
        self.info = {}
        self._ready = threading.Event()

    def start(self):
        super().start()
        # 等放大器打开（或者报错），这样调用方马上就知道硬件是否正常
        self._ready.wait(timeout=15)
        if self.error:
            raise RuntimeError(f"打开放大器失败：{self.error}") from self.error
        if not self._ready.is_set():
            raise RuntimeError("打开放大器超时")
        return self

    def _run(self):
        from eego import EegoSDK
        try:
            with EegoSDK(self.dll_path) as sdk:
                with sdk.get_amplifier() as amp:
                    self.info = {"type": amp.type, "serial": amp.serial, "firmware": amp.firmware}
                    with amp.open_eeg_stream(rate=self.rate, bipolar_range=self.range) as st:
                        types = [t for _, t in st.channels]
                        data_cols = [k for k, t in enumerate(types) if t in ("bipolar", "reference")]
                        if len(data_cols) < max(self.cols) + 1:
                            raise RuntimeError(f"通道不够：{st.channels}")
                        cols = [data_cols[c] for c in self.cols]
                        cnt_col = types.index("sample_counter") if "sample_counter" in types else None
                        self.info["stream_channels"] = st.channels
                        self._ready.set()
                        last = None
                        while not self._stop.is_set():
                            time.sleep(0.03)          # SDK 要求 < 1 s 取一次
                            d = st.get_data()
                            if not len(d):
                                continue
                            if cnt_col is not None:
                                c = d[:, cnt_col]
                                if last is not None:
                                    self.dropped += int(max(0, c[0] - last - 1))
                                self.dropped += int(np.sum(np.clip(np.diff(c) - 1, 0, None)))
                                last = c[-1]
                            self._push(d[:, cols] * 1e6)     # V → µV
        finally:
            self._ready.set()


class SimSource(_ThreadedSource):
    """模拟 EOG/EMG。眼电幅度：水平 ±1 对应 ±250 µV，垂直 ±1 对应 ±200 µV（接近真实数量级）。"""

    H_GAIN, V_GAIN = 250.0, 200.0

    def __init__(self, rate=config.RATE, script=None, seed=0, realtime=True):
        super().__init__()
        self.rate = rate
        self.script = script          # script(t) -> dict(x, y, jaw, brow, blink)
        self.rng = np.random.default_rng(seed)
        self.realtime = realtime
        self.t = 0.0
        self.gx = self.gy = 0.0
        self.drift = np.zeros(2)
        self.next_blink = 3.0
        self.blink_t0 = -10.0
        self.info = {"type": "simulator", "serial": "SIM", "firmware": 0}

    # ---- 键盘输入（Windows 全局按键状态）----
    @staticmethod
    def _keys():
        import ctypes
        g = ctypes.windll.user32.GetAsyncKeyState
        down = lambda vk: bool(g(vk) & 0x8000)
        x = (1 if down(0x27) else 0) - (1 if down(0x25) else 0)      # → ←
        y = (1 if down(0x26) else 0) - (1 if down(0x28) else 0)      # ↑ ↓
        return {"x": x, "y": y, "jaw": down(ord("J")), "brow": down(ord("B")), "blink": None}

    def generate(self, n):
        """生成 n 个样本（µV）。"""
        dt = 1.0 / self.rate
        out = np.empty((n, 4))
        state = self.script(self.t) if self.script else self._keys()
        for k in range(n):
            t = self.t
            if self.script and k % 10 == 0:
                state = self.script(t)
            # 眼球扫视：时间常数约 30 ms 趋近目标
            a = dt / 0.03
            self.gx += (state["x"] - self.gx) * min(1, a)
            self.gy += (state["y"] - self.gy) * min(1, a)
            # 眨眼：垂直通道 +350 µV 的半正弦，持续 200 ms
            blink_now = state.get("blink")
            if blink_now is None:            # 键盘模式：随机眨眼
                if t >= self.next_blink:
                    self.blink_t0 = t
                    self.next_blink = t + self.rng.uniform(3, 7)
            elif blink_now and t - self.blink_t0 > 0.3:
                self.blink_t0 = t
            tb = t - self.blink_t0
            blink = 350 * np.sin(np.pi * tb / 0.2) if 0 <= tb < 0.2 else 0.0
            self.drift += self.rng.normal(0, 0.15, 2)          # 基线随机游走
            hum = 8 * np.sin(2 * np.pi * 50 * t)
            jaw = state["jaw"] * self.rng.normal(0, 80)
            brow = state["brow"] * self.rng.normal(0, 60)
            out[k, 0] = self.H_GAIN * self.gx + self.drift[0] + 0.05 * jaw + hum
            out[k, 1] = self.V_GAIN * self.gy + blink + self.drift[1] + 0.15 * brow + hum
            out[k, 2] = jaw + 0.1 * brow + hum
            out[k, 3] = brow + 0.1 * jaw + hum
            self.t += dt
        out += self.rng.normal(0, 4, out.shape)
        return out

    def _run(self):
        t0 = time.perf_counter()
        sent = 0
        while not self._stop.is_set():
            time.sleep(0.02)
            due = int((time.perf_counter() - t0) * self.rate)
            if due > sent:
                self._push(self.generate(due - sent))
                sent = due


def make_source(sim=False, **kw):
    return SimSource(**kw) if sim else EegoSource(**kw)
