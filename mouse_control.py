"""
Programme principal de la souris mains libres : les yeux déplacent le curseur, dents/sourcils cliquent.
Lancer d'abord calibrate.py pour générer calibration.json.

    python mouse_control.py                    # mode joystick (défaut)
    python mouse_control.py --mode absolute    # mode absolu : le curseur va là où l'on regarde
    python mouse_control.py --dry-run          # affiche seulement les actions sans bouger la souris (conseillé au premier essai)
    python mouse_control.py --sim              # simulateur (flèches = regard, J = dents, B = sourcils)

Au démarrage, regarder le centre de l'écran pendant 2 s (mesure de la ligne de base).
    dents, court        clic gauche (deux fois rapidement = double-clic)
    dents, long 1 s     pause / reprise (avec un bip)
    sourcils, court     clic droit
    sourcils, long 1 s  recentrage (regarder d'abord le centre de l'écran)
    touche F8           pause / reprise (pour la personne à côté)
    touche Échap        quitter immédiatement
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
    return bool(user32.GetAsyncKeyState(vk) & 0x0001)   # pressée depuis la dernière interrogation


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--mode", choices=["joystick", "absolute"], default=config.CONTROL["mode"])
    ap.add_argument("--dry-run", action="store_true", help="ne bouge pas la souris, affiche seulement les actions")
    ap.add_argument("--speed", type=float, default=config.CONTROL["max_speed"], help="vitesse max du mode joystick, pixels/s")
    ap.add_argument("--dead-zone", type=float, default=config.CONTROL["dead_zone"])
    ap.add_argument("--calib", default=str(config.CALIB_FILE))
    ap.add_argument("--no-brow", action="store_true", help="désactive les sourcils (clic droit = serrer les dents 0,6–1 s)")
    for d, fr in (("right", "à droite"), ("left", "à gauche"), ("up", "en haut"), ("down", "en bas")):
        ap.add_argument(f"--{d}-uv", type=float, default=None,
                        help=f"variation EOG en regardant {fr} (µV, avec le signe), remplace la valeur calibrée ; "
                             f"au-delà de zone morte × cette valeur, le regard compte comme {fr}")
    args = ap.parse_args()

    try:
        calib = calibration.load(args.calib)
    except FileNotFoundError:
        print("❌ calibration.json introuvable : lancer d'abord python calibrate.py")
        return
    for d in ("right", "left", "up", "down"):
        v = getattr(args, f"{d}_uv")
        if v is not None:
            calib["eog"][d] = v
    if args.no_brow:
        calib["emg"].update(brow_hi=1e9, brow_lo=1e9, brow_disabled=True)
    e = calib["eog"]
    print(f"Amplitudes EOG (µV) : droite {e['right']:+.0f}  gauche {e['left']:+.0f}  haut {e['up']:+.0f}  bas {e['down']:+.0f}"
          f"   → zone morte {args.dead_zone:.2f}, soit un écart d'environ {args.dead_zone * abs(e['up']):.0f} µV pour « haut »")
    W, H = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    ctl = Controller(calib, screen=(W, H),
                     cfg={"mode": args.mode, "max_speed": args.speed, "dead_zone": args.dead_zone})
    proc = RealtimeProcessor()
    key_pressed(VK_ESCAPE), key_pressed(VK_F8)        # efface l'historique des touches

    with make_source(args.sim) as src:
        print(f"Source de données : {src.info}")
        print(f"Mode : {args.mode}{' (essai : la souris ne bouge pas)' if args.dry_run else ''}    Échap = quitter, F8 = pause")
        # ---- Ligne de base : regarder le centre de l'écran 2 s ----
        print("Regardez le centre de l'écran, 2 s ...")
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
        print("Contrôle actif.")

        last_print = 0.0
        moved = np.zeros(2)
        while True:
            time.sleep(config.CONTROL["loop_dt"])
            if key_pressed(VK_ESCAPE):
                print("\nÉchap : sortie")
                break
            if key_pressed(VK_F8):
                ctl.paused = not ctl.paused
                print(f"\nF8 : {'pause' if ctl.paused else 'reprise'}")
                beep(440 if ctl.paused else 880)
            y = proc.process(src.read())
            for act in ctl.step(y):
                kind = act[0]
                if kind == "move":
                    moved += act[1:]
                    if not args.dry_run:
                        move_mouse(act[1], act[2], W, H)
                else:
                    print(f"\nAction : {act}")
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
                state = "PAUSE" if ctl.paused else "ACTIF"
                arrow = ("←" if moved[0] < -3 else "→" if moved[0] > 3 else " ") +                         ("↑" if moved[1] < -3 else "↓" if moved[1] > 3 else " ")
                moved[:] = 0
                drop = f" perdus {src.dropped}" if src.dropped else ""
                print(f"\r[{state}] curseur {arrow} | yeux x {gx:+.2f} y {gy:+.2f} | dents {bar(j):<20s} | sourcils {bar(br):<20s}{drop}   ",
                      end="", flush=True)


if __name__ == "__main__":
    main()
