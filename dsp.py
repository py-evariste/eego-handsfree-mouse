"""
实时滤波：输入原始 4 路信号（µV），输出
    heog、veog：10 Hz 低通 + 50 Hz 陷波后的眼电（保留直流，µV）
    jaw、brow ：30–200 Hz 带通 + 陷波后的肌电包络（滑动 RMS，µV）
滤波器带状态，可以一块一块地喂数据（实时），结果和一次性喂完全一样（离线分析用同一个类）。
"""

import numpy as np
from scipy import signal

import config


class RealtimeProcessor:
    def __init__(self, rate=config.RATE):
        self.rate = rate
        nyq = rate / 2
        self.sos_eog = signal.butter(4, config.EOG_LOWPASS / nyq, "low", output="sos")
        hi = min(config.EMG_BAND[1], nyq * 0.9)
        self.sos_emg = signal.butter(4, [config.EMG_BAND[0] / nyq, hi / nyq], "band", output="sos")
        # 工频及谐波（50/100/150/200 Hz）都陷掉：实测垂直眼电通道 100、150 Hz 谐波明显，会抬高肌电包络的静息值
        secs = []
        f = config.NOTCH
        while f < nyq * 0.95:
            b, a = signal.iirnotch(f, 30, rate)
            secs.append(signal.tf2sos(b, a))
            f += config.NOTCH
        self.sos_notch = np.vstack(secs)
        self.env_len = max(1, int(config.EMG_ENV_WIN * rate))
        self.reset()

    def reset(self):
        self.z_eog = None
        self.z_emg = None
        self.z_notch = None
        self.sq_hist = np.zeros((0, 2))

    def _init_states(self, first):
        # 用第一个样本初始化低通状态，避免开机时眼电从 0 爬到直流电平的长瞬态
        zi_eog = signal.sosfilt_zi(self.sos_eog)
        self.z_eog = zi_eog[:, :, None] * first[None, None, :2]
        self.z_notch = signal.sosfilt_zi(self.sos_notch)[:, :, None] * first[None, None, :]
        self.z_emg = np.zeros((self.sos_emg.shape[0], 2, 2))

    def process(self, raw):
        """raw: (n, 4) µV，顺序为 config.SIGNALS → (n, 4) 处理后信号。"""
        raw = np.asarray(raw, dtype=float)
        if raw.size == 0:
            return np.empty((0, 4))
        if self.z_eog is None:
            self._init_states(raw[0])
        x, self.z_notch = signal.sosfilt(self.sos_notch, raw, axis=0, zi=self.z_notch)
        eog, self.z_eog = signal.sosfilt(self.sos_eog, x[:, :2], axis=0, zi=self.z_eog)
        emg, self.z_emg = signal.sosfilt(self.sos_emg, x[:, 2:], axis=0, zi=self.z_emg)
        # 滑动 RMS 包络
        sq = np.vstack([self.sq_hist, emg ** 2])
        c = np.cumsum(np.vstack([np.zeros((1, 2)), sq]), axis=0)
        L = self.env_len
        n = len(raw)
        start = len(sq) - n
        idx = np.arange(start, len(sq))
        lo = np.maximum(idx + 1 - L, 0)
        env = np.sqrt((c[idx + 1] - c[lo]) / (idx + 1 - lo)[:, None])
        self.sq_hist = sq[-(L - 1):] if L > 1 else np.zeros((0, 2))
        return np.hstack([eog, env])
