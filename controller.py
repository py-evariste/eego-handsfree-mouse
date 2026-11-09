"""
Logique de contrôle (calcul pur, ne touche pas la souris, facile à tester).

Entrée : blocs (n, 4) produits par dsp.RealtimeProcessor [heog, veog, enveloppe jaw, enveloppe brow]
Sortie : liste d'actions, par exemple [("move", dx, dy), ("click", "left"), ("pause", True), ("recenter",)]

EOG → curseur
    joystick (défaut) : quand le regard dépasse la zone morte vers la gauche/droite/haut/bas, le curseur avance
                        en continu dans cette direction, d'autant plus vite que l'écart est grand ; il s'arrête
                        au retour au centre. Quand les yeux sont au centre, la ligne de base suit lentement la dérive.
    absolute          : position du curseur = position du regard d'après la calibration (à recentrer souvent).
EMG → boutons
    dents, court (0.08–0.6 s)   clic gauche (deux fois rapidement = double-clic)
    dents, long (≥ 1 s)         pause / reprise (en pause, seul ce geste est reconnu)
    sourcils, court             clic droit
    sourcils, long (≥ 1 s)      recentrage (regarder le centre de l'écran puis lever les sourcils)
Le curseur est figé pendant un geste, pour que les artefacts EOG dus aux dents/sourcils ne le fassent pas bouger.
"""

from collections import deque

import numpy as np

import config


class EmgGesture:
    """Reconnaissance de gestes sur une voie EMG : seuil à hystérésis + classement selon la durée."""

    def __init__(self, name, thr_hi, thr_lo, cfg, rest=None, rate=config.RATE):
        self.name, self.hi, self.lo, self.cfg = name, thr_hi, thr_lo, cfg
        # Plancher de bruit adaptatif : l'enveloppe au repos varie avec le contact des électrodes (mesuré : de 3 à 10 µV) ;
        # le seuil vaut au moins 3 × le plancher, pour éviter les déclenchements en rafale quand le bruit augmente
        self.hi0, self.lo0 = thr_hi, thr_lo
        self.floor = rest if rest is not None else thr_lo / 2
        self.alpha = 1.0 / (cfg.get("emg_floor_tau", 5.0) * rate)
        self.active = False
        self.t_on = 0.0
        self.peak = 0.0
        self.long_fired = False
        self.cool_until = -1.0
        self.t_off = -1.0

    def ratio(self, env):
        return env / self.hi

    def update(self, t, env):
        """Renvoie un événement : None / "short" / "medium" / "long" ("long" est émis dès que la durée est atteinte, pas au relâchement)."""
        ev = None
        if not self.active and env < self.hi:
            self.floor += (env - self.floor) * self.alpha
            self.hi = max(self.hi0, 3.0 * self.floor)
            self.lo = max(self.lo0, 2.0 * self.floor)
        if not self.active:
            if env > self.hi and t >= self.cool_until:
                self.active, self.t_on, self.peak, self.long_fired = True, t, env, False
        else:
            self.peak = max(self.peak, env)
            dur = t - self.t_on
            if not self.long_fired and dur >= self.cfg["long_press"]:
                self.long_fired = True
                ev = "long"
            if env < self.lo:
                self.active = False
                self.cool_until = t + self.cfg["refractory"] if (dur >= self.cfg["click_min"]) else t
                self.t_off = t
                if not self.long_fired and self.cfg["click_min"] <= dur <= self.cfg["click_max"]:
                    ev = "short"
                elif not self.long_fired and dur > self.cfg["click_max"]:
                    ev = "medium"
        return ev


class Controller:
    def __init__(self, calib, screen=(1920, 1080), rate=config.RATE, cfg=None):
        self.c = dict(config.CONTROL, **(cfg or {}))
        self.calib = calib
        self.rate = rate
        self.dt = 1.0 / rate
        self.W, self.H = screen
        self.t = 0.0
        e = calib["eog"]
        self.base = np.array([e["base_h"], e["base_v"]], float)
        self.amp = {k: float(e[k]) for k in ("right", "left", "up", "down")}
        m = calib["emg"]
        self.jaw = EmgGesture("jaw", m["jaw_hi"], m["jaw_lo"], self.c, m.get("jaw_rest"), rate)
        self.brow = EmgGesture("brow", m["brow_hi"], m["brow_lo"], self.c, m.get("brow_rest"), rate)
        # Si les sourcils sont désactivés : dents 0.6–1 s = clic droit ; recentrage automatique à la reprise après pause
        # (regarder le centre de l'écran en reprenant)
        self.brow_disabled = bool(m.get("brow_disabled", False))
        self.recenter_pending = False
        self.paused = False
        self.freeze_until = 0.0
        self.v_hold = 0.0                # durée de l'écart vertical en cours
        self.frac = np.zeros(2)          # déplacement résiduel inférieur à 1 pixel
        self.recent = deque(maxlen=int(0.4 * rate))   # EOG des 0.4 dernières s, pour la médiane (supprime les clignements)
        self.abs_pos = None
        self.gaze = (0.0, 0.0)

    # ---------- EOG → direction du regard normalisée (x positif à droite, y positif en haut) ----------
    def normalize(self, h, v):
        dh, dv = h - self.base[0], v - self.base[1]
        x = dh / self.amp["right"] if dh / self.amp["right"] > 0 else -dh / self.amp["left"]
        y = dv / self.amp["up"] if dv / self.amp["up"] > 0 else -dv / self.amp["down"]
        return x, y

    def recenter(self):
        if self.recent:
            r = np.array(self.recent)
            self.base = np.median(r, axis=0)
        self.abs_pos = None

    def step(self, block):
        """Traite un bloc de données et renvoie la liste d'actions."""
        actions = []
        if len(block) == 0:
            return actions
        # --- Gestes EMG (échantillon par échantillon, timing précis) ---
        jaw_on_any = brow_on_any = False
        for k in range(len(block)):
            self.t += self.dt
            ej = self.jaw.update(self.t, block[k, 2])
            eb = self.brow.update(self.t, block[k, 3])
            jaw_on_any |= self.jaw.active
            brow_on_any |= self.brow.active
            # si les deux voies sont actives en même temps (diaphonie), on ne retient que la plus forte par rapport à son seuil
            if ej and eb:
                if self.jaw.peak / self.jaw.hi >= self.brow.peak / self.brow.hi:
                    eb = None
                else:
                    ej = None
            if ej and self.brow.active and self.brow.peak / self.brow.hi > self.jaw.peak / self.jaw.hi:
                ej = None
            if eb and self.jaw.active and self.jaw.peak / self.jaw.hi > self.brow.peak / self.brow.hi:
                eb = None
            if (ej or eb) and self.c["mode"] == "joystick":
                # recentrage automatique après tout geste facial :
                # en mode 5 électrodes, serrer les dents / lever les sourcils tire la peau sous les électrodes EOG
                # et fait sauter le potentiel continu de plusieurs mV, bien plus que le signal des yeux
                self.recenter_pending = True
            if ej == "long":
                self.paused = not self.paused
                actions.append(("pause", self.paused))
                if not self.paused and self.brow_disabled:
                    self.recenter_pending = True
            elif ej == "short" and not self.paused:
                actions.append(("click", "left"))
            elif ej == "medium" and not self.paused and self.brow_disabled:
                actions.append(("click", "right"))
            elif eb == "short" and not self.paused:
                actions.append(("click", "right"))
            elif eb == "long" and not self.paused:
                self.recenter()
                actions.append(("recenter",))
            self.recent.append(block[k, :2])
            if (self.recenter_pending and not self.jaw.active and not self.brow.active
                    and self.t > max(self.jaw.t_off, self.brow.t_off) + 0.6):
                self.recenter_pending = False
                self.recenter()
                actions.append(("recenter",))

        emg_busy = jaw_on_any or brow_on_any or self.jaw.active or self.brow.active
        if emg_busy:
            self.freeze_until = self.t + 0.25

        dt_block = len(block) * self.dt
        # médiane sur 0.4 s : un clignement (pic de ~0.2 s) n'a presque pas d'effet
        h_med, v_med = np.median(np.array(self.recent), axis=0)
        h_now, v_now = block[-max(1, int(0.04 * self.rate)):, :2].mean(axis=0)
        x, y = self.normalize(h_now, v_now)
        xm, ym = self.normalize(h_med, v_med)
        self.gaze = (x, y)

        if self.paused or self.t < self.freeze_until:
            self.v_hold = 0.0
            return actions

        dz = self.c["dead_zone"]
        if self.c["mode"] == "joystick":
            # un écart vertical doit durer vertical_hold s pour compter (filtre les clignements)
            self.v_hold = self.v_hold + dt_block if abs(y) > dz else 0.0
            yy = ym if self.v_hold >= self.c["vertical_hold"] else 0.0
            vx = self._speed(x)
            vy = self._speed(yy)
            # la ligne de base suit la dérive : plus vite quand les yeux sont au centre, très lentement quand ils sont décalés
            # (évite de ne plus jamais revenir dans la zone morte après une longue dérive)
            centered = abs(x) < dz * 0.7 and abs(y) < dz * 0.7
            tau = self.c["baseline_tau"] if centered else self.c["baseline_tau_out"]
            self.base += (np.array([h_med, v_med]) - self.base) * min(1.0, dt_block / tau)
            move = np.array([vx, -vy]) * dt_block + self.frac
        else:
            # mode absolu : point de regard → coordonnées écran ; horizontal = valeur actuelle, vertical = médiane (sans clignements)
            tx = self.W / 2 + np.clip(x, -1.1, 1.1) * self.W * 0.45
            ty = self.H / 2 - np.clip(ym, -1.1, 1.1) * self.H * 0.45
            target = np.array([tx, ty])
            if self.abs_pos is None:
                self.abs_pos = target
            a = min(1.0, dt_block / self.c["absolute_smoothing"])
            new = self.abs_pos + (target - self.abs_pos) * a
            move = new - self.abs_pos + self.frac
            self.abs_pos = new
        step = np.trunc(move)
        self.frac = move - step
        if step[0] or step[1]:
            actions.append(("move", int(step[0]), int(step[1])))
        return actions

    def _speed(self, v):
        dz = self.c["dead_zone"]
        a = abs(v)
        if a <= dz:
            return 0.0
        s = min(1.0, (a - dz) / (1 - dz)) ** self.c["speed_exponent"]
        return np.sign(v) * s * self.c["max_speed"]
