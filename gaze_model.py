"""
Modèle regard caméra → coordonnées écran, et filtre de lissage.

GazeModel : régression ridge
    entrées : hx, vy (iris dans l'œil), pose de la tête (yaw, pitch, roll, tx, ty, tz),
              plus les termes quadratiques hx², vy², hx·vy (l'œil est une sphère : la relation n'est pas linéaire)
    sorties : x, y écran en pixels
    Les termes de l'œil sont standardisés sur les données. Les termes de la tête sont mis à une échelle
    physique fixe (10°, 5 cm), fortement pénalisés et bornés à ±3 : pendant l'entraînement la tête bouge
    très peu, et une standardisation sur les données amplifierait énormément le moindre changement de posture
    (constaté au premier essai : la prédiction partait dans un coin de l'écran).
OneEuro : filtre « 1 € » (Casiez et al.) — lisse fortement quand le regard est stable,
          suit vite quand il bouge : bon compromis tremblement / latence pour le regard.
"""

import json
import math

import numpy as np

from camera import FEATURES


def design(F):
    """F : (n, 8) dans l'ordre de camera.FEATURES → matrice de régression (n, 11)."""
    F = np.atleast_2d(np.asarray(F, dtype=float))
    hx, vy = F[:, 0], F[:, 1]
    return np.column_stack([F, hx ** 2, vy ** 2, hx * vy])


EYE_COLS = [0, 1, 8, 9, 10]                       # hx, vy, hx², vy², hx·vy
HEAD_COLS = [2, 3, 4, 5, 6, 7]                    # yaw, pitch, roll, tx, ty, tz
HEAD_SCALE = np.array([10.0, 10.0, 10.0, 5.0, 5.0, 5.0])   # degrés, degrés, degrés, cm, cm, cm
HEAD_PENALTY = 30.0                               # pénalité relative des termes de la tête
HEAD_CLIP = 3.0                                   # écart maximal pris en compte (en unités d'échelle)


class GazeModel:
    def __init__(self, alpha=1.0):
        self.alpha = alpha
        self.mu = self.sd = self.W = None
        self.screen = None

    def _z(self, X):
        Z = (X - self.mu) / self.sd
        Z[:, HEAD_COLS] = np.clip(Z[:, HEAD_COLS], -HEAD_CLIP, HEAD_CLIP)
        return np.column_stack([np.ones(len(X)), Z])

    def fit(self, F, XY, screen=None):
        X = design(F)
        self.mu = X.mean(axis=0)
        self.sd = X.std(axis=0) + 1e-9
        self.sd[HEAD_COLS] = HEAD_SCALE
        Z = self._z(X)
        pen = np.full(Z.shape[1], float(self.alpha))
        pen[0] = 0.0                                          # pas de pénalité sur la constante
        pen[1 + np.array(HEAD_COLS)] *= HEAD_PENALTY
        self.W = np.linalg.solve(Z.T @ Z + np.diag(pen), Z.T @ np.asarray(XY, dtype=float))
        self.screen = screen
        return self

    def predict(self, F):
        return self._z(design(F)) @ self.W

    def to_dict(self):
        return {"alpha": self.alpha, "mu": self.mu.tolist(), "sd": self.sd.tolist(),
                "W": self.W.tolist(), "screen": self.screen, "features": FEATURES}

    @classmethod
    def from_dict(cls, d):
        m = cls(d["alpha"])
        m.mu, m.sd, m.W = np.array(d["mu"]), np.array(d["sd"]), np.array(d["W"])
        m.screen = d.get("screen")
        return m

    def save(self, path, extra=None):
        d = self.to_dict()
        if extra:
            d.update(extra)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


def choose_alpha(F, XY, groups, alphas=(0.1, 0.3, 1, 3, 10, 30)):
    """Choisit alpha par validation croisée « un point de calibration laissé de côté ».
    groups : identifiant du point cible de chaque échantillon. Renvoie (alpha, erreur médiane en px)."""
    F, XY, groups = np.asarray(F), np.asarray(XY), np.asarray(groups)
    best = (None, np.inf)
    for a in alphas:
        errs = []
        for g in np.unique(groups):
            tr, te = groups != g, groups == g
            m = GazeModel(a).fit(F[tr], XY[tr])
            p = m.predict(F[te]).mean(axis=0)
            errs.append(np.hypot(*(p - XY[te][0])))
        e = float(np.median(errs))
        if e < best[1]:
            best = (a, e)
    return best


class OneEuro:
    """Filtre 1 € pour un signal vectoriel. min_cutoff (Hz) : lissage au repos ; beta : réactivité au mouvement."""

    def __init__(self, min_cutoff=0.8, beta=0.002, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        x = np.asarray(x, dtype=float)
        if self.x is None:
            self.x, self.dx, self.t = x, np.zeros_like(x), t
            return x
        dt = max(t - self.t, 1e-3)
        dx = (x - self.x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        cutoff = self.min_cutoff + self.beta * float(np.linalg.norm(self.dx))
        a = self._alpha(cutoff, dt)
        self.x = a * x + (1 - a) * self.x
        self.t = t
        return self.x

    def reset(self):
        self.x = self.dx = self.t = None
