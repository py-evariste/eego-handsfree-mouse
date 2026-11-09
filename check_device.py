"""
eego EE-410 上机自检：第一次接上放大器时先跑这个。

    python check_device.py                    # 设备信息 + 阻抗 + 10 秒实时数据
    python check_device.py --seconds 30 --save test.npz
    python check_device.py --dll D:\\eego-sdk\\windows\\64bit\\eego-SDK.dll

会打印：
  1. SDK 版本、放大器型号/序列号/固件版本（SDK 1.3.19 只支持 EE-410 固件 011700、011800、012000–020000）
  2. 通道列表（确认 8 个通道是 bipolar 还是 reference 类型）、可用采样率和量程
  3. 阻抗（注意：第一次读数可能不准，脚本会等几秒再读）
  4. 每个通道的 RMS / 峰峰值（µV），sample_counter 是否连续（判断丢样）
"""

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from eego import EegoSDK, EegoError

HERE = Path(__file__).resolve().parent
from config import DLL_PATH as DEFAULT_DLL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dll", default=DEFAULT_DLL)
    ap.add_argument("--rate", type=int, default=500)
    ap.add_argument("--range", type=float, default=1.5, help="双极量程（V）：4 / 1.5 / 0.7 / 0.35")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--no-impedance", action="store_true")
    ap.add_argument("--save", default=None, help="把数据存成 .npz")
    args = ap.parse_args()

    with EegoSDK(args.dll) as sdk:
        print(f"SDK 版本号: {sdk.version}")
        amp = sdk.get_amplifier()
        with amp:
            print(f"型号: {amp.type}   序列号: {amp.serial}   固件: {amp.firmware}")
            print(f"可用采样率: {amp.sampling_rates}")
            print(f"参考量程: {amp.reference_ranges}   双极量程: {amp.bipolar_ranges}")
            chans = amp.channels
            print(f"放大器通道 ({len(chans)}): {chans}")

            if not args.no_impedance:
                with amp.open_impedance_stream() as imp:
                    print(f"阻抗流通道: {imp.channels}")
                    time.sleep(3)          # 第一轮阻抗值不准（release notes 已知问题）
                    z = imp.get_data()
                    if len(z):
                        for (idx, typ), val in zip(imp.channels, z[-1]):
                            print(f"  {typ:>20s} {idx:2d}: {val / 1e3:8.1f} kΩ")

            with amp.open_eeg_stream(rate=args.rate, bipolar_range=args.range) as st:
                print(f"EEG 流通道 ({st.n_channels}): {st.channels}")
                chunks, t_end = [], time.time() + args.seconds
                while time.time() < t_end:
                    time.sleep(0.1)        # 必须 < 1 s 取一次，否则丢样
                    d = st.get_data()
                    if len(d):
                        chunks.append(d)
                        print(f"\r已收到 {sum(len(c) for c in chunks)} 个样本", end="")
                print()
                data = np.concatenate(chunks) if chunks else np.empty((0, st.n_channels))
                types = [t for _, t in st.channels]

    report(data, types, args.rate)
    if args.save:
        np.savez(args.save, data=data, channels=np.array(types), rate=args.rate)
        print(f"已保存到 {args.save}")


def report(data, types, rate):
    if not len(data):
        print("没有收到数据")
        return
    print(f"共 {len(data)} 个样本 ≈ {len(data) / rate:.1f} 秒")
    for k, t in enumerate(types):
        x = data[:, k]
        if t in ("bipolar", "reference"):
            uv = (x - x.mean()) * 1e6
            print(f"  通道 {k:2d} ({t}): RMS {uv.std():8.1f} µV   峰峰 {np.ptp(uv):8.1f} µV")
        elif t == "sample_counter":
            gaps = np.diff(x)
            bad = int(np.sum(gaps != 1))
            print(f"  sample_counter: {x[0]:.0f} → {x[-1]:.0f}，不连续 {bad} 处" + ("  ✅" if bad == 0 else "  ⚠ 丢样"))
        elif t == "trigger":
            print(f"  trigger 出现过的值: {sorted(set(x.astype(int).tolist()))}")


if __name__ == "__main__":
    try:
        main()
    except EegoError as e:
        print(f"❌ {e}")
        sys.exit(1)
