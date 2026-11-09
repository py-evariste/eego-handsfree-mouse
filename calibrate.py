"""
引导式校准（全屏界面，约 1 分 30 秒）。电极贴好、live_view.py 里信号正常之后运行：

    python calibrate.py            # 真实放大器
    python calibrate.py --sim      # 模拟器（用方向键/J/B 键配合屏幕提示）

按屏幕提示：只转眼睛看红点（头不动）→ 咬牙 3 次 → 挑眉 3 次。
结果存到 calibration.json，原始数据存到 calib_raw.npz（出问题时发给我分析）。
任何时候按 Esc 退出。
"""

import argparse
import sys
import tkinter as tk

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

import calibration
import config
from source import make_source


class CalibApp:
    def __init__(self, source, protocol):
        self.src, self.protocol = source, protocol
        self.root = tk.Tk()
        self.root.attributes("-fullscreen", True)
        self.root.configure(bg="black")
        self.root.bind("<Escape>", lambda e: self.abort())
        self.W = self.root.winfo_screenwidth()
        self.H = self.root.winfo_screenheight()
        self.cv = tk.Canvas(self.root, bg="black", highlightthickness=0, width=self.W, height=self.H)
        self.cv.pack(fill="both", expand=True)
        self.chunks, self.n = [], 0
        self.marks = []                 # (阶段名, 起始样本)
        self.step = -1
        self.aborted = False
        self.result = None

    def xy(self, pos):
        mx, my = self.W * 0.45, self.H * 0.42
        return self.W / 2 + pos[0] * mx, self.H / 2 - pos[1] * my

    def draw(self, text, pos, color="red", sub=""):
        self.cv.delete("all")
        x, y = self.xy(pos)
        r = 18
        self.cv.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="white", width=2)
        self.cv.create_text(self.W / 2, self.H * 0.08, text=text, fill="white", font=("Microsoft YaHei", 32))
        if sub:
            self.cv.create_text(self.W / 2, self.H * 0.94, text=sub, fill="gray", font=("Microsoft YaHei", 16))

    def poll(self):
        if self.aborted:
            return
        try:
            d = self.src.read()
        except Exception as e:
            self.finish_error(str(e))
            return
        if len(d):
            self.chunks.append(d)
            self.n += len(d)
        self.root.after(20, self.poll)

    def next_step(self):
        if self.aborted:
            return
        self.step += 1
        if self.step >= len(self.protocol):
            self.marks.append(("end", self.n))
            self.root.after(300, self.analyze)
            return
        name, dur, text, pos = self.protocol[self.step]
        self.marks.append((name, self.n))
        color = {"jaw": "orange", "brow": "deepskyblue"}.get(name, "red")
        self.draw(text, pos, color, sub=f"第 {self.step + 1}/{len(self.protocol)} 步    Esc 退出")
        self.root.after(int(dur * 1000), self.next_step)

    def analyze(self):
        raw = np.concatenate(self.chunks) if self.chunks else np.empty((0, 4))
        segs = [(self.marks[k][0], self.marks[k][1], self.marks[k + 1][1]) for k in range(len(self.marks) - 1)]
        np.savez(config.HERE / "calib_raw.npz", raw=raw, seg_names=[s[0] for s in segs],
                 seg_bounds=np.array([s[1:] for s in segs]), rate=config.RATE)
        try:
            calib, report = calibration.analyze(raw, segs, config.RATE)
            calibration.save(calib)
            self.result = calib
            report.append(f"已保存到 {config.CALIB_FILE.name}")
        except Exception as e:
            report = [f"❌ 分析失败：{e}"]
        print("\n".join(report))
        self.cv.delete("all")
        self.cv.create_text(self.W / 2, self.H / 2, text="\n".join(report) + "\n\n按任意键关闭",
                            fill="white", font=("Microsoft YaHei", 18), justify="left")
        self.root.bind("<Key>", lambda e: self.root.destroy())

    def finish_error(self, msg):
        print("❌", msg)
        self.aborted = True
        self.root.destroy()

    def abort(self):
        self.aborted = True
        print("已取消校准")
        self.root.destroy()

    def run(self):
        self.draw("校准马上开始：只转眼睛看红点，头保持不动", (0, 0), sub="3 秒后开始")
        self.root.after(200, self.poll)
        self.root.after(3000, self.start)
        self.root.mainloop()
        return self.result

    def start(self):
        self.src.read()                # 丢掉开始前的数据
        self.chunks, self.n = [], 0
        self.next_step()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="用模拟器（方向键=看方向，J=咬牙，B=挑眉）")
    ap.add_argument("--reps", type=int, default=2, help="每个方向看几次")
    args = ap.parse_args()
    protocol = calibration.build_protocol(reps_eye=args.reps)
    with make_source(args.sim) as src:
        print(f"数据源：{src.info}")
        CalibApp(src, protocol).run()


if __name__ == "__main__":
    main()
