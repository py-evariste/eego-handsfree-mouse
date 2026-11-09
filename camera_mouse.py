"""
Contrôle du curseur par la webcam : le curseur va là où l'on regarde (lancer d'abord camera_calibrate.py).

    python camera_mouse.py --dry-run      # essai : affiche la position estimée sans bouger la souris
    python camera_mouse.py                # contrôle réel (caméra seule, pas de clic)
    python camera_mouse.py --clicks       # caméra pour déplacer + eego (EMG des dents) pour cliquer
    python camera_mouse.py --smooth 0.5   # plus de lissage (curseur plus stable mais plus lent)

Avec --clicks (calibration.json nécessaire) :
    dents, court (0,08–0,6 s)   clic gauche
    dents, moyen (0,6–1 s)      clic droit
    dents, long (≥ 1 s)         pause / reprise
Le curseur est figé pendant un serrage de dents (le visage bouge et fausse l'estimation du regard).
Touches : F8 = pause / reprise, F9 = recentrage (regarder le centre de l'écran puis appuyer), Échap = quitter.
Si l'estimation est décalée en permanence (par exemple trop haut), F9 corrige ce décalage ;
on peut aussi le fixer au lancement avec --offset-x / --offset-y (px, positif = vers la droite / le bas).
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time
from collections import deque

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

import config
from camera import FEATURES, CameraGaze, vector
from camera_calibrate import MODEL_FILE
from gaze_model import GazeModel, OneEuro

user32 = ctypes.windll.user32
VK_ESCAPE, VK_F8, VK_F9 = 0x1B, 0x77, 0x78
LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP = 0x02, 0x04, 0x08, 0x10


def key_pressed(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x0001)


def click(button):
    down, up = (LEFTDOWN, LEFTUP) if button == "left" else (RIGHTDOWN, RIGHTUP)
    user32.mouse_event(down, 0, 0, 0, 0)
    user32.mouse_event(up, 0, 0, 0, 0)


def beep(freq, ms=120):
    import winsound
    threading.Thread(target=winsound.Beep, args=(freq, ms), daemon=True).start()


class JawClicks:
    """Clics par EMG des dents (eego), réutilise la calibration et la détection de gestes existantes."""

    WARMUP = 2.0          # s ignorées au démarrage

    def __init__(self):
        import calibration
        from controller import EmgGesture
        from dsp import RealtimeProcessor
        from source import EegoSource
        calib = calibration.load()
        m = calib["emg"]
        self.cfg = dict(config.CONTROL)
        self.jaw = EmgGesture("jaw", m["jaw_hi"], m["jaw_lo"], self.cfg, m.get("jaw_rest"))
        self.proc = RealtimeProcessor()
        self.src = EegoSource().start()
        self.t = 0.0

    def step(self):
        """→ liste d'événements ("short" / "medium" / "long"), et True si les dents sont serrées en ce moment."""
        y = self.proc.process(self.src.read())
        events = []
        for v in y[:, 2]:
            self.t += 1.0 / config.RATE
            if self.t < self.WARMUP:
                continue          # régime transitoire des filtres au démarrage : sinon faux « long » → pause immédiate
            ev = self.jaw.update(self.t, v)
            if ev:
                events.append(ev)
        return events, self.jaw.active

    def stop(self):
        self.src.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, default=0, help="numéro de la caméra")
    ap.add_argument("--dry-run", action="store_true", help="ne bouge pas la souris, affiche seulement la position")
    ap.add_argument("--clicks", action="store_true", help="clics par serrage des dents (amplificateur eego)")
    ap.add_argument("--smooth", type=float, default=0.8,
                    help="lissage au repos (Hz du filtre 1 €) : plus petit = plus stable, plus grand = plus réactif")
    ap.add_argument("--beta", type=float, default=0.002, help="réactivité du filtre aux mouvements rapides")
    ap.add_argument("--dead-px", type=float, default=15, help="ne bouge pas le curseur pour un écart inférieur (px)")
    ap.add_argument("--offset-x", type=float, default=0, help="correction horizontale (px, positif = vers la droite)")
    ap.add_argument("--offset-y", type=float, default=0, help="correction verticale (px, positif = vers le bas)")
    args = ap.parse_args()

    try:
        model = GazeModel.load(MODEL_FILE)
    except FileNotFoundError:
        print("❌ gaze_model.json introuvable : lancer d'abord python camera_calibrate.py")
        return
    if model.features != FEATURES:
        print("❌ gaze_model.json vient d'une ancienne version : relancer python camera_calibrate.py")
        return
    W, H = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    if model.screen and list(model.screen) != [W, H]:
        print(f"⚠ Modèle entraîné pour un écran {model.screen}, écran actuel {[W, H]} : refaire l'entraînement")
    filt = OneEuro(args.smooth, args.beta)
    clicks = JawClicks() if args.clicks else None
    paused = False
    freeze_until = 0.0
    opening = deque(maxlen=300)
    recent = deque(maxlen=60)                       # prédictions brutes récentes (pour F9)
    offset = np.array([args.offset_x, args.offset_y], dtype=float)
    cur = None
    key_pressed(VK_ESCAPE), key_pressed(VK_F8), key_pressed(VK_F9)

    print(f"Contrôle par caméra{' (essai : la souris ne bouge pas)' if args.dry_run else ''}"
          f"{' + clics eego' if clicks else ''}    Échap = quitter, F8 = pause, F9 = recentrage")
    last_print = 0.0
    try:
        with CameraGaze(args.index) as cam:
            while True:
                time.sleep(0.01)
                if key_pressed(VK_ESCAPE):
                    print("\nÉchap : sortie")
                    break
                if key_pressed(VK_F9) and recent:
                    t_last = recent[-1][0]
                    pts = np.array([q for t, q in recent if t_last - t < 0.5])
                    offset = np.array([W / 2, H / 2]) - np.median(pts, axis=0)
                    filt.reset()
                    beep(1200)
                    print(f"\nF9 : recentrage, correction x {offset[0]:+.0f} px, y {offset[1]:+.0f} px "
                          f"(pour la garder : --offset-x {offset[0]:.0f} --offset-y {offset[1]:.0f})")
                if key_pressed(VK_F8):
                    paused = not paused
                    beep(440 if paused else 880)
                    print(f"\nF8 : {'pause' if paused else 'reprise'}")
                now = time.perf_counter()
                if clicks:
                    events, clenched = clicks.step()
                    if clenched:
                        freeze_until = now + 0.3
                    for ev in events:
                        if ev == "long":
                            paused = not paused
                            beep(440 if paused else 880, 250)
                            print(f"\nDents (long) : {'pause' if paused else 'reprise'}")
                        elif not paused and ev in ("short", "medium"):
                            btn = "left" if ev == "short" else "right"
                            print(f"\nClic {'gauche' if btn == 'left' else 'droit'}")
                            if not args.dry_run:
                                click(btn)
                for f in cam.read():
                    opening.append(f["open"])
                    if f["open"] < 0.7 * np.median(opening):      # clignement : on ignore l'image
                        continue
                    p = model.predict(vector(f))[0]
                    recent.append((f["t"], p))
                    p = np.clip(p + offset, [0, 0], [W - 1, H - 1])
                    cur = filt(p, f["t"])
                if cur is None or paused or now < freeze_until:
                    continue
                if not args.dry_run:
                    pos = wt.POINT()
                    user32.GetCursorPos(ctypes.byref(pos))
                    if np.hypot(cur[0] - pos.x, cur[1] - pos.y) > args.dead_px:
                        user32.SetCursorPos(int(cur[0]), int(cur[1]))
                if now - last_print > 0.2:
                    last_print = now
                    state = "PAUSE" if paused else "ACTIF"
                    print(f"\r[{state}] regard estimé x {cur[0]:6.0f} y {cur[1]:6.0f}   caméra {cam.fps:4.1f} img/s   ",
                          end="", flush=True)
    finally:
        if clicks:
            clicks.stop()


if __name__ == "__main__":
    main()
