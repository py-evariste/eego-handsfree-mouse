"""
eemagine eego SDK 1.3.19 的 Python 封装（ctypes 直接调 eego-SDK.dll 的 C 接口）

官方只提供 C++ 头文件；C++ 封装（wrapper.cc）内部调用的是 DLL 导出的一组
eemagine_sdk_* C 函数。这里按 wrapper.cc 的调用顺序在 Python 里复刻同样的流程：
    setup(NULL) → init() → get_amplifiers_info() → open_eeg_stream()
    → 循环 prefetch() + get_data() → close_stream() → close_amplifier() → exit()

注意：官方手册说 C 层“不保证稳定”，但 1.3.19 已经停止更新，对这台 EE-410 来说是固定的。

用法：
    from eego import EegoSDK
    with EegoSDK(r"C:\\path\\to\\eego-SDK.dll") as sdk:
        amp = sdk.get_amplifier()
        print(amp.serial, amp.type, amp.firmware)
        with amp.open_eeg_stream(rate=500, bipolar_range=1.5) as st:
            data = st.get_data()   # (样本数, 通道数) 的 numpy 数组，单位 V
"""

import ctypes as C
from pathlib import Path

import numpy as np

# 和 wrapper.h 里的 eemagine_sdk_channel_type 顺序一致（注意：和 channel.h 的 C++ 枚举顺序不同）
CHANNEL_TYPES = {
    0: "reference",
    1: "bipolar",
    2: "accelerometer",
    3: "gyroscope",
    4: "magnetometer",
    5: "trigger",
    6: "sample_counter",
    7: "impedance_reference",
    8: "impedance_ground",
}

ERRORS = {
    -1: "NOT_CONNECTED（放大器被拔掉了）",
    -2: "ALREADY_EXISTS",
    -3: "NOT_FOUND（找不到放大器）",
    -4: "INCORRECT_VALUE（参数错误，或者丢了数据：getData 间隔超过 1 秒 / USB 中断）",
    -5: "UNKNOWN",
}

ALL_CHANNELS = 0xFFFFFFFFFFFFFFFF


class EegoError(RuntimeError):
    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(f"eego SDK 错误 {code} {ERRORS.get(code, '')} {detail}".strip())


class _AmpInfo(C.Structure):
    _fields_ = [("id", C.c_int), ("serial", C.c_char * 64)]


class _ChanInfo(C.Structure):
    _fields_ = [("index", C.c_int), ("type", C.c_int)]


class EegoSDK:
    """加载 DLL 并初始化 SDK。整个程序里只建一个，用完调用 close()（或用 with）。"""

    def __init__(self, dll_path):
        dll_path = Path(dll_path)
        if not dll_path.is_file():
            raise FileNotFoundError(f"找不到 {dll_path}")
        if dll_path.stat().st_size < 600_000:
            # E 盘里那份解压出来的 64 位 DLL 只有 352 KB，是坏的；zip 里的完整版是 639 KB
            raise RuntimeError(f"{dll_path} 只有 {dll_path.stat().st_size} 字节，文件不完整，请从 zip 重新解压")
        self.dll = C.CDLL(str(dll_path))
        self._declare()
        self.dll.eemagine_sdk_setup(None)
        self.dll.eemagine_sdk_init()
        self._open = True

    def _declare(self):
        d = self.dll
        i, dbl, p = C.c_int, C.c_double, C.POINTER
        sig = {
            "eemagine_sdk_setup": ([C.c_void_p], None),
            "eemagine_sdk_init": ([], None),
            "eemagine_sdk_exit": ([], None),
            "eemagine_sdk_get_version": ([], i),
            "eemagine_sdk_get_amplifiers_info": ([p(_AmpInfo), i], i),
            "eemagine_sdk_close_amplifier": ([i], i),
            "eemagine_sdk_get_amplifier_serial": ([i, C.c_char_p, i], i),
            "eemagine_sdk_get_amplifier_version": ([i], i),
            "eemagine_sdk_get_amplifier_type": ([i, C.c_char_p, i], i),
            "eemagine_sdk_get_amplifier_channel_list": ([i, p(_ChanInfo), i], i),
            "eemagine_sdk_get_amplifier_sampling_rates_available": ([i, p(i), i], i),
            "eemagine_sdk_get_amplifier_reference_ranges_available": ([i, p(dbl), i], i),
            "eemagine_sdk_get_amplifier_bipolar_ranges_available": ([i, p(dbl), i], i),
            "eemagine_sdk_open_eeg_stream": ([i, i, dbl, dbl, C.c_ulonglong, C.c_ulonglong], i),
            "eemagine_sdk_open_impedance_stream": ([i, C.c_ulonglong], i),
            "eemagine_sdk_close_stream": ([i], i),
            "eemagine_sdk_get_stream_channel_list": ([i, p(_ChanInfo), i], i),
            "eemagine_sdk_get_stream_channel_count": ([i], i),
            "eemagine_sdk_prefetch": ([i], i),
            "eemagine_sdk_get_data": ([i, p(dbl), i], i),
            "eemagine_sdk_get_error_string": ([C.c_char_p, i], i),
        }
        for name, (args, res) in sig.items():
            f = getattr(d, name)
            f.argtypes, f.restype = args, res

    def check(self, rv):
        """C 函数返回负数表示出错，转成异常（附上 SDK 自己的错误说明）。"""
        if rv < 0:
            buf = C.create_string_buffer(1024)
            self.dll.eemagine_sdk_get_error_string(buf, 1024)
            raise EegoError(rv, buf.value.decode(errors="replace"))
        return rv

    @property
    def version(self):
        return self.dll.eemagine_sdk_get_version()

    def list_amplifiers(self):
        arr = (_AmpInfo * 64)()
        n = self.check(self.dll.eemagine_sdk_get_amplifiers_info(arr, 64))
        return [Amplifier(self, arr[k].id, arr[k].serial.decode(errors="replace")) for k in range(n)]

    def get_amplifier(self):
        amps = self.list_amplifiers()
        if not amps:
            raise EegoError(-3, "没有检测到放大器：检查 USB 线、驱动（设备管理器）、放大器 LED")
        for extra in amps[1:]:
            extra.close()
        return amps[0]

    def close(self):
        if self._open:
            self.dll.eemagine_sdk_exit()
            self._open = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _channels(arr, n):
    return [(arr[k].index, CHANNEL_TYPES.get(arr[k].type, f"?{arr[k].type}")) for k in range(n)]


class Amplifier:
    def __init__(self, sdk, amp_id, serial):
        self.sdk, self.id, self.serial = sdk, amp_id, serial
        self._open = True

    def _call(self, name, *args):
        return self.sdk.check(getattr(self.sdk.dll, name)(self.id, *args))

    @property
    def type(self):
        buf = C.create_string_buffer(1024)
        self._call("eemagine_sdk_get_amplifier_type", buf, 1024)
        return buf.value.decode(errors="replace")

    @property
    def firmware(self):
        return self._call("eemagine_sdk_get_amplifier_version")

    @property
    def channels(self):
        arr = (_ChanInfo * 1024)()
        return _channels(arr, self._call("eemagine_sdk_get_amplifier_channel_list", arr, 1024))

    def _array(self, name, ctype):
        arr = (ctype * 1024)()
        n = self._call(name, arr, 1024)
        return [arr[k] for k in range(n)]

    @property
    def sampling_rates(self):
        return self._array("eemagine_sdk_get_amplifier_sampling_rates_available", C.c_int)

    @property
    def reference_ranges(self):
        return self._array("eemagine_sdk_get_amplifier_reference_ranges_available", C.c_double)

    @property
    def bipolar_ranges(self):
        return self._array("eemagine_sdk_get_amplifier_bipolar_ranges_available", C.c_double)

    def open_eeg_stream(self, rate=500, reference_range=1.0, bipolar_range=1.5,
                        ref_mask=ALL_CHANNELS, bip_mask=ALL_CHANNELS):
        """数据单位 V。同一时间只能有一个 stream，用完必须 close。"""
        sid = self._call("eemagine_sdk_open_eeg_stream", rate, reference_range, bipolar_range,
                         ref_mask, bip_mask)
        return Stream(self.sdk, sid, rate)

    def open_impedance_stream(self, ref_mask=ALL_CHANNELS):
        """数据单位 Ω。get_data() 返回最新一次的阻抗值（1 个样本）。"""
        sid = self._call("eemagine_sdk_open_impedance_stream", ref_mask)
        return Stream(self.sdk, sid, None)

    def close(self):
        if self._open:
            self.sdk.dll.eemagine_sdk_close_amplifier(self.id)
            self._open = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Stream:
    def __init__(self, sdk, stream_id, rate):
        self.sdk, self.id, self.rate = sdk, stream_id, rate
        arr = (_ChanInfo * 1024)()
        n = sdk.check(sdk.dll.eemagine_sdk_get_stream_channel_list(stream_id, arr, 1024))
        self.channels = _channels(arr, n)
        self.n_channels = n
        self._open = True

    def get_data(self):
        """取出自上次调用以来的所有样本 → (样本数, 通道数)。两次调用间隔必须 < 1 秒。

        最后两列通常是 trigger 和 sample_counter（以 self.channels 为准）。
        """
        nbytes = self.sdk.check(self.sdk.dll.eemagine_sdk_prefetch(self.id))
        n_values = nbytes // 8
        if n_values == 0:
            return np.empty((0, self.n_channels))
        buf = np.empty(n_values, dtype=np.float64)
        self.sdk.check(self.sdk.dll.eemagine_sdk_get_data(
            self.id, buf.ctypes.data_as(C.POINTER(C.c_double)), nbytes))
        return buf.reshape(-1, self.n_channels)   # 按样本交错：[s0ch0, s0ch1, ..., s1ch0, ...]

    def close(self):
        if self._open:
            self.sdk.dll.eemagine_sdk_close_stream(self.id)
            self._open = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
