"""
Modèle regard caméra → coordonnées écran, et filtre de lissage.

GazeModel : régression ridge sur des caractéristiques standardisées
    entrées : hx, vy (iris dans l'œil), pose de la tête (yaw, pitch, roll, tx, ty, tz),
              plus les termes quadratiques hx², vy², hx·vy (l'œil est une sphère : la relation n'est pas linéaire)
    sorties : x, y écran en pixels
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


class GazeModel:
    def __init__(self, alpha=1.0):
        self.alpha = alpha
        self.mu = self.sd = self.W = None
        self.screen = None

    def fit(self, F, XY, screen=None):
        X = design(F)
        self.mu = X.mean(axis=0)
        self.sd = X.std(axis=0) + 1e-9
        Z = np.column_stack([np.ones(len(X)), (X - self.mu) / self.sd])
        reg = self.alpha * np.eye(Z.shape[1])
        reg[0, 0] = 0.0                       # pas de pénalité sur la constante
        self.W = np.linalg.solve(Z.T @ Z + reg, Z.T @ np.asarray(XY, dtype=float))
        self.screen = screen
        return self

    def predict(self, F):
        X = design(F)
        Z = np.column_stack([np.ones(len(X)), (X - self.mu) / self.sd])
        return Z @ self.W

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
