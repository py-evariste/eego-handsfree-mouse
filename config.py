"""
统一参数。上机时一般只需要改 CHANNELS（如果电极插到了别的通道上）。

电极接法（XE-293，红 = +，黑 = −，通道号就是电极线上的数字标签）：
    通道 1  水平眼电 HEOG   红：右眼外眼角外侧 1 cm   黑：左眼外眼角外侧 1 cm
    通道 2  垂直眼电 VEOG   红：左眼眉毛正上方 1 cm   黑：左眼下眼睑正下方 1 cm
    通道 3  咬肌 EMG        红、黑都贴在左侧咬肌上（耳垂前下方，咬牙时鼓起的地方），相距 2 cm
    通道 4  额肌 EMG        红、黑都贴在右眉上方 2–3 cm 的额头上，水平相距 2 cm
    GND                     额头正中（或乳突）
    通道 5–8 不用，但数据表要求所有电极都要贴在人身上：每对红黑并排贴在耳后乳突附近即可。
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------- 硬件 ----------
import os
DLL_PATH = os.environ.get("EEGO_DLL", str(HERE / "sdk" / "windows" / "64bit" / "eego-SDK.dll"))
RATE = 500                 # Hz，EE-410 可选 500/512/1000/1024/2000/2048
BIPOLAR_RANGE = 1.5        # V，可选 4 / 1.5 / 0.7 / 0.35；眼电+肌电用 1.5 足够且不容易饱和

# 功能 → 放大器通道（0 起算：XE-293 的“通道 1”= 0）
# 电极片少的时候用 "5pads"：只贴通道 1、2 和 GND（5 片），
#   咬牙肌电从通道 1 的高频部分取（外眼角电极就在颞肌上，咬牙时颞肌收缩），
#   挑眉肌电从通道 2 的高频部分取（眉上电极就在额肌上）。
# 电极片够的时候用 "9pads"：通道 3、4 单独贴在咬肌和额肌上，肌电更干净。
LAYOUT = os.environ.get("EEGO_LAYOUT", "5pads")
CHANNELS = {
    "9pads": {"heog": 0, "veog": 1, "jaw": 2, "brow": 3},
    "5pads": {"heog": 0, "veog": 1, "jaw": 0, "brow": 1},
}[LAYOUT]
SIGNALS = ["heog", "veog", "jaw", "brow"]   # 内部统一的信号顺序

# ---------- 滤波 ----------
EOG_LOWPASS = 10.0         # Hz，眼电只保留慢变化（保留直流，不做高通）
EMG_BAND = (30.0, 200.0)   # Hz，肌电带通
NOTCH = 50.0               # Hz，工频（中国/欧洲 50 Hz）
EMG_ENV_WIN = 0.10         # s，肌电包络（滑动 RMS）窗口

# ---------- 校准文件 ----------
CALIB_FILE = HERE / "calibration.json"

# ---------- 控制 ----------
CONTROL = {
    "mode": "joystick",        # joystick：眼睛往某个方向偏，光标就往那个方向持续移动；absolute：看哪儿光标去哪儿
    "dead_zone": 0.35,         # 摇杆模式死区（相对校准幅度，0~1）
    "max_speed": 900,          # 摇杆模式最大速度，像素/秒
    "speed_exponent": 1.6,     # 速度曲线，越大小偏移越慢、越容易精细定位
    "vertical_hold": 0.30,     # s，垂直偏移要持续这么久才算（过滤眨眼）
    "absolute_smoothing": 0.15,  # s，绝对模式光标平滑时间常数
    "baseline_tau": 3.0,       # s，眼睛在中间时基线跟随漂移的时间常数
    "baseline_tau_out": 15.0,  # s，眼睛偏在一边时基线跟随的时间常数（更慢）
    "loop_dt": 0.02,           # s，控制循环周期

    # 肌电手势（时长单位 s）
    "click_min": 0.08,         # 咬牙短于这个当噪声
    "click_max": 0.60,         # 咬牙短于这个算“点击”
    "long_press": 1.00,        # 咬牙长于这个算“暂停/继续”
    "double_click_gap": 0.40,  # 两次短咬牙间隔小于这个算双击
    "refractory": 0.30,        # 手势结束后的冷却时间
}
