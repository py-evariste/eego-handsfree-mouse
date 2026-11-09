"""
Entraînement du modèle de regard caméra (plein écran, environ 1 minute).

    python camera_calibrate.py               # 9 points × 2 tours + 5 points de validation
    python camera_calibrate.py --rounds 3    # plus de répétitions = modèle plus stable
    python camera_calibrate.py --index 1     # autre caméra
    python camera_calibrate.py --refit       # réajuste le modèle à partir du dernier entraînement enregistré

Consignes : asseyez-vous à votre distance habituelle, visage bien éclairé et face à la caméra.
Suivez le point avec les yeux (vous pouvez bouger un peu la tête naturellement, mais restez à la même place).
Le modèle est enregistré dans gaze_model.json ; les données brutes dans camera_calib_raw.npz.
Échap pour quitter.
"""

import argparse
import random
import sys
import time
import tkinter as tk

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from camera import CameraGaze, FEATURES, vector
from config import HERE
from gaze_model import GazeModel, choose_alpha

MODEL_FILE = HERE / "gaze_model.json"
GRID = [(x, y) for y in (0.08, 0.5, 0.92) for x in (0.08, 0.5, 0.92)]
SETTLE, COLLECT = 0.7, 1.2          # s : temps pour que le regard se pose, puis temps de mesure


class CamCalibApp:
    def __init__(self, cam, rounds, n_valid):
        self.cam = cam
        self.root = tk.Tk()
        self.root.attributes("-fullscreen", True)
        self.root.configure(bg="black")
        self.root.bind("<Escape>", lambda e: self.abort())
        self.W, self.H = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.cv = tk.Canvas(self.root, bg="black", highlightthickness=0, width=self.W, height=self.H)
        self.cv.pack(fill="both", expand=True)
        pts = []
        for _ in range(rounds):
            r = GRID[:]
            random.shuffle(r)
            pts += [("train", p) for p in r]
        valid = [(random.uniform(0.15, 0.85), random.uniform(0.15, 0.85)) for _ in range(n_valid)]
        pts += [("valid", p) for p in valid]
        self.targets = pts
        self.k = -1
        self.samples = []            # (phase, n° de point, x, y, dict de caractéristiques)
        self.aborted = False
        self.no_face = 0

    def px(self, p):
        return p[0] * self.W, p[1] * self.H

    def dot(self, p, r, color="red", text=""):
        self.cv.delete("all")
        x, y = self.px(p)
        self.cv.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="white", width=2)
        self.cv.create_oval(x - 3, y - 3, x + 3, y + 3, fill="white", outline="")
        if text:
            self.cv.create_text(self.W / 2, self.H * 0.97, text=text, fill="gray", font=("Segoe UI", 14))

    def next_point(self):
        if self.aborted:
            return
        self.k += 1
        if self.k >= len(self.targets):
            self.root.after(100, self.finish)
            return
        phase, p = self.targets[self.k]
        label = "validation" if phase == "valid" else "entraînement"
        self.dot(p, 22, "red" if phase == "train" else "deepskyblue",
                 f"Point {self.k + 1}/{len(self.targets)} ({label}) — suivez le point des yeux    Échap pour quitter")
        self.cam.read()                                   # jeter les mesures pendant le déplacement du regard
        self.root.after(int(SETTLE * 1000), self.collect_start)

    def collect_start(self):
        if self.aborted:
            return
        phase, p = self.targets[self.k]
        self.dot(p, 12, "red" if phase == "train" else "deepskyblue")
        self.cam.read()
        self.root.after(int(COLLECT * 1000), self.collect_end)

    def collect_end(self):
        if self.aborted:
            return
        phase, p = self.targets[self.k]
        got = self.cam.read()
        if not got:
            self.no_face += 1
        x, y = self.px(p)
        for f in got:
            self.samples.append((phase, self.k, x, y, f))
        self.next_point()

    def finish(self):
        analyze.pairs = []
        lines = analyze(self.samples, (self.W, self.H))
        self.cv.delete("all")
        if self.no_face:
            lines.append(f"⚠ Visage non détecté sur {self.no_face} point(s) : vérifier l'éclairage et le cadrage")
        for (tx, ty), (px_, py_) in analyze.pairs:
            self.cv.create_oval(tx - 6, ty - 6, tx + 6, ty + 6, outline="white", width=2)
            self.cv.create_line(tx, ty, px_, py_, fill="gray")
            self.cv.create_oval(px_ - 5, py_ - 5, px_ + 5, py_ + 5, fill="lime", outline="")
        self.cv.create_text(self.W / 2, self.H / 2, text="\n".join(lines) + "\n\nAppuyez sur une touche pour fermer",
                            fill="white", font=("Segoe UI", 18), justify="left")
        print("\n".join(lines))
        self.root.bind("<Key>", lambda e: self.root.destroy())

    def abort(self):
        self.aborted = True
        print("Entraînement annulé")
        self.root.destroy()

    def run(self):
        self.dot((0.5, 0.5), 22, "red", "Suivez le point rouge des yeux. Début dans 3 s")
        self.root.after(3000, self.next_point)
        self.root.mainloop()


def analyze(samples, screen):
    """Ajuste le modèle, l'enregistre et renvoie les lignes du rapport.
    analyze.pairs reçoit les couples (cible, estimation) des points de validation, pour l'affichage."""
    if not samples:
        return ["❌ Aucune mesure : visage non détecté. Vérifier la caméra avec python camera.py"]
    phase = np.array([s[0] for s in samples])
    grp = np.array([s[1] for s in samples])
    XY = np.array([[s[2], s[3]] for s in samples])
    F = np.array([vector(s[4]) for s in samples])
    opening = np.array([s[4]["open"] for s in samples])
    np.savez(HERE / "camera_calib_raw.npz", phase=phase, group=grp, XY=XY, F=F, open=opening,
             features=np.array(FEATURES), screen=np.array(screen))
    # rejeter les clignements (yeux nettement moins ouverts que d'habitude)
    keep = opening > 0.7 * np.median(opening)
    tr = keep & (phase == "train")
    va = keep & (phase == "valid")
    if len(np.unique(grp[tr])) < 6:
        return ["❌ Trop peu de points d'entraînement exploitables : recommencer face à la caméra, bien éclairé"]
    alpha, loo = choose_alpha(F[tr], XY[tr], grp[tr])
    model = GazeModel(alpha).fit(F[tr], XY[tr], screen=list(screen))
    lines = [f"Échantillons : {tr.sum()} entraînement, {va.sum()} validation ({(~keep).sum()} clignements rejetés)",
             f"Erreur médiane en validation croisée : {loo:.0f} px (alpha = {alpha})"]
    pairs = []
    if va.any():
        errs = []
        for g in np.unique(grp[va]):
            m = va & (grp == g)
            p = model.predict(F[m]).mean(axis=0)
            t = XY[m][0]
            errs.append(np.hypot(*(p - t)))
            pairs.append((tuple(t), tuple(p)))
        diag = float(np.hypot(*screen))
        e = float(np.median(errs))
        lines.append(f"Erreur médiane sur les points de validation : {e:.0f} px ({100 * e / diag:.1f} % de la diagonale)")
        jitter = float(np.median([np.linalg.norm(model.predict(F[va & (grp == g)]).std(axis=0))
                                  for g in np.unique(grp[va])]))
        lines.append(f"Tremblement (avant lissage) : {jitter:.0f} px")
        extra = {"valid_error_px": e, "loo_error_px": loo, "jitter_px": jitter}
    else:
        extra = {"loo_error_px": loo}
    model.save(MODEL_FILE, extra=extra)
    lines.append(f"Modèle enregistré dans {MODEL_FILE.name}")
    lines.append("Blanc = cible, vert = estimation")
    analyze.pairs = pairs
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, default=0, help="numéro de la caméra")
    ap.add_argument("--rounds", type=int, default=2, help="nombre de tours sur la grille de 9 points")
    ap.add_argument("--valid", type=int, default=5, help="nombre de points de validation")
    ap.add_argument("--refit", action="store_true", help="réajuster à partir de camera_calib_raw.npz, sans caméra")
    args = ap.parse_args()
    if args.refit:
        d = np.load(HERE / "camera_calib_raw.npz")
        names = [str(n) for n in d["features"]]
        if names != FEATURES:
            print("❌ Ces données d'entraînement viennent d'une ancienne version (caractéristiques différentes) : "
                  "relancer python camera_calibrate.py")
            return
        samples = [(str(ph), int(g), float(xy[0]), float(xy[1]), dict(zip(names, f), open=float(o)))
                   for ph, g, xy, f, o in zip(d["phase"], d["group"], d["XY"], d["F"], d["open"])]
        analyze.pairs = []
        print("\n".join(analyze(samples, tuple(int(v) for v in d["screen"]))))
        return
    with CameraGaze(args.index) as cam:
        t_end = time.time() + 5
        while cam.latest()[0] is None and time.time() < t_end:
            time.sleep(0.1)
        if cam.latest()[0] is None:
            print("⚠ Visage non détecté pour l'instant ; vérifier avec python camera.py")
        CamCalibApp(cam, args.rounds, args.valid).run()

if __name__ == "__main__":
    main()
