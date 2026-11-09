"""
Suivi du regard par la webcam : capture vidéo + repères du visage MediaPipe (iris inclus) + pose de la tête.

Un thread d'arrière-plan lit la caméra et calcule, pour chaque image, un vecteur de caractéristiques :
    hx, vy   position de l'iris dans l'œil (moyenne des deux yeux), normalisée par la largeur de l'œil
             hx : ~0 = coin externe, ~1 = coin interne (vu dans l'image) ;
             vy : par rapport à la ligne des coins de l'œil (qui ne bougent pas), positif vers le bas
    open     ouverture des yeux : sert aussi d'indice vertical (les paupières suivent le regard :
             l'œil se ferme un peu quand on regarde en bas) et à rejeter les clignements
    yaw, pitch, roll   rotation de la tête (degrés), d'après la matrice de transformation faciale
    tx, ty, tz         translation de la tête (cm)

    python camera.py              # aperçu : visage, repères des yeux et caractéristiques en direct (Échap pour quitter)
    python camera.py --index 1    # autre caméra

Le modèle face_landmarker.task est téléchargé automatiquement dans models/ au premier lancement
(source officielle MediaPipe).
"""

import argparse
import math
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "models" / "face_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")

# Indices des repères (maillage MediaPipe à 478 points)
# œil « A » (côté gauche de l'image non inversée = œil droit de la personne)
EYE_A = {"outer": 33, "inner": 133, "up": 159, "down": 145, "iris": 468}
# œil « B » (œil gauche de la personne)
EYE_B = {"outer": 263, "inner": 362, "up": 386, "down": 374, "iris": 473}

FEATURES = ["hx", "vy", "open", "yaw", "pitch", "roll", "tx", "ty", "tz"]


def ensure_model():
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(exist_ok=True)
        print(f"Téléchargement du modèle MediaPipe → {MODEL_PATH} ...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


def eye_features(pts, eye):
    """pts : (478, 2) en pixels. Renvoie (hx, vy, ouverture) pour un œil."""
    p_out, p_in = pts[eye["outer"]], pts[eye["inner"]]
    p_up, p_down, iris = pts[eye["up"]], pts[eye["down"]], pts[eye["iris"]]
    axis = p_in - p_out
    w = float(np.hypot(*axis))
    if w < 1e-6:
        return None
    u = axis / w                       # axe horizontal de l'œil
    v = np.array([-u[1], u[0]])        # axe vertical de l'œil
    if v[1] < 0:                       # toujours orienté vers le bas de l'image (sinon les deux yeux s'annulent)
        v = -v
    hx = float(np.dot(iris - p_out, u) / w)
    # référence verticale = milieu des coins de l'œil (fixes) et non milieu des paupières :
    # les paupières suivent le regard et annuleraient presque tout le signal vertical
    mid = (p_out + p_in) / 2
    vy = float(np.dot(iris - mid, v) / w)
    opening = float(np.hypot(*(p_down - p_up)) / w)
    return hx, vy, opening


def head_pose(matrix):
    """Matrice 4×4 de transformation faciale → (yaw, pitch, roll) en degrés, (tx, ty, tz) en cm."""
    m = np.asarray(matrix, dtype=float).reshape(4, 4)
    r = m[:3, :3]
    yaw = math.degrees(math.atan2(r[0, 2], r[2, 2]))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, -r[1, 2]))))
    roll = math.degrees(math.atan2(r[1, 0], r[1, 1]))
    tx, ty, tz = m[:3, 3]
    return yaw, pitch, roll, float(tx), float(ty), float(tz)


def frame_features(landmarks, matrix, width, height):
    """Repères normalisés (liste d'objets avec .x .y) + matrice → dict de caractéristiques, ou None."""
    pts = np.array([[p.x * width, p.y * height] for p in landmarks])
    if len(pts) < 478:
        return None
    a, b = eye_features(pts, EYE_A), eye_features(pts, EYE_B)
    if a is None or b is None:
        return None
    # Les deux yeux sont symétriques : pour l'œil B, « externe → interne » va dans l'autre sens dans l'image.
    # On ramène les deux à la même convention : hx croissant = regard vers la droite de l'image.
    hx = (a[0] + (1.0 - b[0])) / 2
    vy = (a[1] + b[1]) / 2
    opening = (a[2] + b[2]) / 2
    yaw, pitch, roll, tx, ty, tz = head_pose(matrix) if matrix is not None else (0, 0, 0, 0, 0, 0)
    return {"hx": hx, "vy": vy, "yaw": yaw, "pitch": pitch, "roll": roll,
            "tx": tx, "ty": ty, "tz": tz, "open": opening, "pts": pts}


class CameraGaze:
    """Thread de capture : expose la dernière mesure via latest() et toutes les mesures via read()."""

    def __init__(self, index=0, width=640, height=480):
        self.index, self.width, self.height = index, width, height
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._buf = []
        self._latest = None
        self._frame = None
        self.error = None
        self.fps = 0.0
        self._ready = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run_safe, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=20)
        if self.error:
            raise RuntimeError(f"Caméra : {self.error}") from self.error
        if not self._ready.is_set():
            raise RuntimeError("Caméra : délai dépassé à l'ouverture")
        return self

    def _run_safe(self):
        try:
            self._run()
        except Exception as e:
            self.error = e
        finally:
            self._ready.set()

    def _run(self):
        import cv2
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(ensure_model())),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_facial_transformation_matrixes=True,
        )
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if not cap.isOpened():
            raise RuntimeError(f"impossible d'ouvrir la caméra n° {self.index}")
        t0 = time.perf_counter()
        n, t_fps = 0, t0
        try:
            with vision.FaceLandmarker.create_from_options(opts) as lm:
                self._ready.set()
                while not self._stop.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        time.sleep(0.01)
                        continue
                    t = time.perf_counter()
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                    res = lm.detect_for_video(img, int((t - t0) * 1000))
                    feat = None
                    if res.face_landmarks:
                        mat = res.facial_transformation_matrixes[0] if res.facial_transformation_matrixes else None
                        h, w = frame.shape[:2]
                        feat = frame_features(res.face_landmarks[0], mat, w, h)
                        if feat is not None:
                            feat["t"] = t
                    with self._lock:
                        self._frame = frame
                        self._latest = feat
                        if feat is not None:
                            self._buf.append(feat)
                    n += 1
                    if t - t_fps >= 1.0:
                        self.fps, n, t_fps = n / (t - t_fps), 0, t
        finally:
            cap.release()

    def read(self):
        """Toutes les mesures depuis l'appel précédent (liste de dicts)."""
        if self.error:
            raise RuntimeError(f"Caméra : {self.error}") from self.error
        with self._lock:
            out, self._buf = self._buf, []
        return out

    def latest(self):
        with self._lock:
            return self._latest, self._frame

    def stop(self):
        self._stop.set()
        if getattr(self, "_thread", None):
            self._thread.join(timeout=3)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def vector(feat, names=FEATURES):
    return np.array([feat[k] for k in names], dtype=float)


def main():
    import cv2
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, default=0, help="numéro de la caméra")
    args = ap.parse_args()
    print("Aperçu caméra : Échap pour quitter")
    with CameraGaze(args.index) as cam:
        while True:
            feat, frame = cam.latest()
            if frame is not None:
                img = frame.copy()
                if feat is not None:
                    for eye in (EYE_A, EYE_B):
                        for k, idx in eye.items():
                            x, y = feat["pts"][idx]
                            cv2.circle(img, (int(x), int(y)), 3 if k == "iris" else 2,
                                       (0, 255, 0) if k == "iris" else (0, 200, 255), -1)
                    txt = (f"hx {feat['hx']:.3f}  vy {feat['vy']:+.3f}  ouv {feat['open']:.2f}  "
                           f"yaw {feat['yaw']:+.0f}  pitch {feat['pitch']:+.0f}  {cam.fps:.0f} img/s")
                    color = (0, 255, 0)
                else:
                    txt, color = "Visage non détecté", (0, 0, 255)
                cv2.putText(img, txt, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
                cv2.imshow("Camera - regard", img)
            if cv2.waitKey(15) & 0xFF == 27:
                break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
