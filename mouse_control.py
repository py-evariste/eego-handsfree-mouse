"""
免手鼠标主程序：眼动移动光标，咬牙/挑眉点击。先运行 calibrate.py 生成 calibration.json。

    python mouse_control.py                    # 摇杆模式（默认）
    python mouse_control.py --mode absolute    # 绝对模式：看哪儿光标去哪儿
    python mouse_control.py --dry-run          # 只打印动作，不真的动鼠标（第一次上机建议先这样试）
    python mouse_control.py --sim              # 模拟器（方向键=看方向，J=咬牙，B=挑眉）

启动时先看屏幕中间 2 秒（建立基线）。
    咬牙短     左键单击（快速两下 = 双击）
    咬牙长 1 s 暂停 / 继续（会“嘀”一声）
    挑眉短     右键单击
    挑眉长 1 s 重新居中（先看屏幕中间）
    键盘 F8    暂停 / 继续（给旁边的人用）
    键盘 Esc   立即退出
"""

import argparse
import ctypes
import sys
import threading
import time

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

import calibration
import config
from controller import Controller
from dsp import RealtimeProcessor
from source import make_source

user32 = ctypes.windll.user32
VK_ESCAPE, VK_F8 = 0x1B, 0x77
LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP = 0x02, 0x04, 0x08, 0x10


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def move_mouse(dx, dy, W, H):
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    user32.SetCursorPos(int(np.clip(p.x + dx, 0, W - 1)), int(np.clip(p.y + dy, 0, H - 1)))


def click(button):
    down, up = (LEFTDOWN, LEFTUP) if button == "left" else (RIGHTDOWN, RIGHTUP)
    user32.mouse_event(down, 0, 0, 0, 0)
    user32.mouse_event(up, 0, 0, 0, 0)


def beep(freq, ms=120):
    import winsound
    threading.Thread(target=winsound.Beep, args=(freq, ms), daemon=True).start()


def key_pressed(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x0001)   # 自上次查询以来按过


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--mode", choices=["joystick", "absolute"], default=config.CONTROL["mode"])
    ap.add_argument("--dry-run", action="store_true", help="不动鼠标，只打印动作")
    ap.add_argument("--speed", type=float, default=config.CONTROL["max_speed"], help="摇杆模式最大速度 像素/秒")
    ap.add_argument("--dead-zone", type=float, default=config.CONTROL["dead_zone"])
    ap.add_argument("--calib", default=str(config.CALIB_FILE))
    for d, cn in (("right", "右"), ("left", "左"), ("up", "上"), ("down", "下")):
        ap.add_argument(f"--{d}-uv", type=float, default=None,
                        help=f"手动指定看{cn}时眼电变化（µV，带正负号），覆盖校准值；"
                             f"超过 死区×该值 就算往{cn}")
    args = ap.parse_args()

    try:
        calib = calibration.load(args.calib)
    except FileNotFoundError:
        print("❌ 没有 calibration.json，先运行 python calibrate.py")
        return
    for d in ("right", "left", "up", "down"):
        v = getattr(args, f"{d}_uv")
        if v is not None:
            calib["eog"][d] = v
    e = calib["eog"]
    print(f"眼电幅度（µV）：右 {e['right']:+.0f}  左 {e['left']:+.0f}  上 {e['up']:+.0f}  下 {e['down']:+.0f}"
          f"   → 死区 {args.dead_zone:.2f}，即偏移超过约 {args.dead_zone * abs(e['up']):.0f} µV 才算上")
    W, H = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    ctl = Controller(calib, screen=(W, H),
                     cfg={"mode": args.mode, "max_speed": args.speed, "dead_zone": args.dead_zone})
    proc = RealtimeProcessor()
    key_pressed(VK_ESCAPE), key_pressed(VK_F8)        # 清掉之前的按键记录

    with make_source(args.sim) as src:
        print(f"数据源：{src.info}")
        print(f"模式：{args.mode}{'（演习：不动鼠标）' if args.dry_run else ''}    Esc 退出，F8 暂停")
        # ---- 建立基线：看屏幕中间 2 秒 ----
        print("请看屏幕中间，2 秒 ...")
        src.read()
        t_end, base = time.time() + 2.0, []
        while time.time() < t_end:
            time.sleep(0.05)
            y = proc.process(src.read())
            if len(y):
                base.append(y[:, :2])
        if base:
            b = np.concatenate(base)
            b = b[len(b) // 3:]
            ctl.base = np.median(b, axis=0)
            ctl.recent.extend(b[-ctl.recent.maxlen:])
        beep(880)
        print("开始控制。")

        last_print = 0.0
        while True:
            time.sleep(config.CONTROL["loop_dt"])
            if key_pressed(VK_ESCAPE):
                print("\nEsc：退出")
                break
            if key_pressed(VK_F8):
                ctl.paused = not ctl.paused
                print(f"\nF8：{'暂停' if ctl.paused else '继续'}")
                beep(440 if ctl.paused else 880)
            y = proc.process(src.read())
            for act in ctl.step(y):
                kind = act[0]
                if kind == "move":
                    if not args.dry_run:
                        move_mouse(act[1], act[2], W, H)
                else:
                    print(f"\n动作：{act}")
                    if kind == "click" and not args.dry_run:
                        click(act[1])
                    elif kind == "pause":
                        beep(440 if act[1] else 880, 250)
                    elif kind == "recenter":
                        beep(1200)
            now = time.time()
            if now - last_print > 0.2 and len(y):
                last_print = now
                gx, gy = ctl.gaze
                j = y[-1, 2] / ctl.jaw.hi
                br = y[-1, 3] / ctl.brow.hi
                bar = lambda v: "#" * int(min(v, 2) * 10)
                state = "暂停" if ctl.paused else "控制"
                drop = f" 丢样 {src.dropped}" if src.dropped else ""
                print(f"\r[{state}] 眼 x {gx:+.2f} y {gy:+.2f} | 咬牙 {bar(j):<20s} | 挑眉 {bar(br):<20s}{drop}   ",
                      end="", flush=True)


if __name__ == "__main__":
    main()
