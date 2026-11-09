"""
Tests sans caméra : extraction des caractéristiques sur des repères synthétiques, modèle de regard,
filtre 1 €, et analyse complète d'un entraînement simulé.

    python test_camera.py
"""

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

import camera
import camera_calibrate
from camera import EYE_A, EYE_B, frame_features
from gaze_model import GazeModel, OneEuro, choose_alpha

W, H = 1920, 1080


def fake_landmarks(gx, gy, img=(640, 480)):
    """Repères normalisés d'un visage de face ; (gx, gy) déplace les iris (fraction de la largeur de l'œil)."""
    pts = np.zeros((478, 2))
    eye_w = 30.0
    for eye, cx in ((EYE_A, 270.0), (EYE_B, 370.0)):
        left, right = cx - eye_w / 2, cx + eye_w / 2
        # œil A : externe à gauche de l'image ; œil B : externe à droite
        outer, inner = (left, right) if eye is EYE_A else (right, left)
        pts[eye["outer"]] = (outer, 240)
        pts[eye["inner"]] = (inner, 240)
        pts[eye["up"]] = (cx, 233)
        pts[eye["down"]] = (cx, 247)
        pts[eye["iris"]] = (cx + gx * eye_w, 240 + gy * eye_w)
    return [SimpleNamespace(x=x / img[0], y=y / img[1]) for x, y in pts]


def test_features():
    f0 = frame_features(fake_landmarks(0, 0), None, 640, 480)
    fr = frame_features(fake_landmarks(0.15, 0), None, 640, 480)
    fd = frame_features(fake_landmarks(0, 0.1), None, 640, 480)
    assert abs(f0["hx"] - 0.5) < 1e-6 and abs(f0["vy"]) < 1e-6, f0
    assert fr["hx"] > f0["hx"] + 0.1, "iris vers la droite de l'image → hx augmente (les deux yeux concordent)"
    assert fd["vy"] > 0.05, "iris vers le bas → vy positif"
    assert abs(f0["open"] - 14 / 30) < 1e-6


def simulate(points, n_per=30, rng=None, noise=0.004):
    """Caractéristiques simulées pour des cibles écran : modèle « œil = sphère » + petits mouvements de tête."""
    rng = rng or np.random.default_rng(0)
    F, XY, G = [], [], []
    for g, (x, y) in enumerate(points):
        ax, ay = (x / W - 0.5) * 50, (y / H - 0.5) * 30              # angle du regard (degrés)
        for _ in range(n_per):
            yaw, pitch = rng.normal(0, 2), rng.normal(0, 2)          # la tête bouge un peu
            ex, ey = ax - yaw, ay - pitch                            # rotation de l'œil dans l'orbite
            hx = 0.5 + 0.25 * np.sin(np.radians(ex)) + rng.normal(0, noise)
            vy = 0.20 * np.sin(np.radians(ey)) + rng.normal(0, noise)
            F.append([hx, vy, yaw, pitch, 0, rng.normal(0, 0.5), rng.normal(0, 0.5), 60])
            XY.append([x, y])
            G.append(g)
    return np.array(F), np.array(XY, float), np.array(G)


def test_model():
    grid = [(x * W, y * H) for y in (0.08, 0.5, 0.92) for x in (0.08, 0.5, 0.92)]
    F, XY, G = simulate(grid * 2)
    G = G % 9
    alpha, loo = choose_alpha(F, XY, G)
    m = GazeModel(alpha).fit(F, XY)
    rng = np.random.default_rng(1)
    test = [(rng.uniform(0.15, 0.85) * W, rng.uniform(0.15, 0.85) * H) for _ in range(20)]
    Ft, XYt, Gt = simulate(test, rng=rng)
    err = [np.hypot(*(m.predict(Ft[Gt == g]).mean(axis=0) - XYt[Gt == g][0])) for g in range(len(test))]
    print(f"  alpha {alpha}, erreur validation croisée {loo:.0f} px, erreur points tests {np.median(err):.0f} px")
    assert np.median(err) < 60, err
    # sauvegarde / rechargement
    with tempfile.TemporaryDirectory() as d:
        m.save(Path(d) / "m.json")
        m2 = GazeModel.load(Path(d) / "m.json")
        assert np.allclose(m.predict(Ft[:5]), m2.predict(Ft[:5]))


def test_one_euro():
    rng = np.random.default_rng(2)
    f = OneEuro(0.8, 0.002)
    out = [f(np.array([500.0, 500.0]) + rng.normal(0, 40, 2), k / 30) for k in range(90)]
    jitter = np.std(np.array(out[30:]), axis=0).mean()
    assert jitter < 18, f"le filtre doit réduire fortement le tremblement ({jitter:.1f} px)"
    t0 = 3.0
    out = [f(np.array([1400.0, 500.0]) + rng.normal(0, 40, 2), t0 + k / 30) for k in range(1, 16)]
    assert out[-1][0] > 1200, f"le filtre doit suivre un saut en moins de 0,5 s ({out[-1][0]:.0f})"


def test_calibration_analysis():
    grid = [(x * W, y * H) for y in (0.08, 0.5, 0.92) for x in (0.08, 0.5, 0.92)]
    rng = np.random.default_rng(3)
    valid = [(rng.uniform(0.15, 0.85) * W, rng.uniform(0.15, 0.85) * H) for _ in range(5)]
    F, XY, G = simulate(grid * 2 + valid, rng=rng)
    samples = []
    for f, (x, y), g in zip(F, XY, G):
        feat = dict(zip(camera.FEATURES, f))
        feat["open"] = 0.45 if rng.random() > 0.03 else 0.1      # quelques clignements
        samples.append(("train" if g < 18 else "valid", g, x, y, feat))
    with tempfile.TemporaryDirectory() as d:
        camera_calibrate.MODEL_FILE = Path(d) / "gaze_model.json"
        camera_calibrate.HERE = Path(d)
        camera_calibrate.analyze.pairs = []
        lines = camera_calibrate.analyze(samples, (W, H))
        print("  " + "\n  ".join(lines))
        assert camera_calibrate.MODEL_FILE.exists()
        assert len(camera_calibrate.analyze.pairs) == 5


if __name__ == "__main__":
    test_features()
    print("✅ caractéristiques des yeux")
    test_model()
    print("✅ modèle de regard")
    test_one_euro()
    print("✅ filtre 1 €")
    test_calibration_analysis()
    print("✅ analyse d'un entraînement simulé")
