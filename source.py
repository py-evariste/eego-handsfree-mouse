"""
Sources de données : un thread d'arrière-plan lit les données en continu ; le programme principal
appelle read() à tout moment pour obtenir les nouveaux échantillons (n, 4), en µV,
dans l'ordre de config.SIGNALS (heog, veog, jaw, brow).

    EegoSource  amplificateur réel (eego EE-410)
    SimSource   simulateur, pour tester l'ensemble sans matériel :
                  flèches ← → ↑ ↓   regarder dans cette direction
                  touche J (maintenue)  serrer les dents
                  touche B (maintenue)  lever les sourcils
                  plus des clignements aléatoires, une dérive de la ligne de base, du bruit et du 50 Hz secteur
                On peut aussi passer une fonction script(t) pour générer les données selon un scénario (tests automatiques)

make_source(sim=False) choisit l'une ou l'autre.
"""

import threading
import time

import numpy as np

import config


class _ThreadedSource:
    def __init__(self):
        self._buf = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.error = None
        self.total = 0          # nombre d'échantillons reçus
        self.dropped = 0        # nombre d'échantillons perdus détectés

    def start(self):
        self._thread = threading.Thread(target=self._run_safe, daemon=True)
        self._thread.start()
        return self

    def _run_safe(self):
        try:
            self._run()
        except Exception as e:      # transmettre l'erreur du thread d'arrière-plan au thread principal
            self.error = e

    def _push(self, block):
        with self._lock:
            self._buf.append(block)
            self.total += len(block)

    def read(self):
        if self.error:
            raise RuntimeError(f"Erreur de la source de données : {self.error}") from self.error
        with self._lock:
            blocks, self._buf = self._buf, []
        if not blocks:
            return np.empty((0, 4))
        return np.concatenate(blocks)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


class EegoSource(_ThreadedSource):
    def __init__(self, dll_path=config.DLL_PATH, rate=config.RATE, bipolar_range=config.BIPOLAR_RANGE,
                 channels=config.CHANNELS):
        super().__init__()
        self.dll_path, self.rate, self.range = dll_path, rate, bipolar_range
        self.cols = [channels[s] for s in config.SIGNALS]
        self.info = {}
        self._ready = threading.Event()

    def start(self):
        super().start()
        # attendre l'ouverture de l'amplificateur (ou une erreur), pour que l'appelant sache tout de suite si le matériel fonctionne
        self._ready.wait(timeout=15)
        if self.error:
            raise RuntimeError(f"Échec de l'ouverture de l'amplificateur : {self.error}") from self.error
        if not self._ready.is_set():
            raise RuntimeError("Délai dépassé à l'ouverture de l'amplificateur")
        return self

    def _run(self):
        from eego import EegoSDK
        try:
            with EegoSDK(self.dll_path) as sdk:
                with sdk.get_amplifier() as amp:
                    self.info = {"type": amp.type, "serial": amp.serial, "firmware": amp.firmware}
                    with amp.open_eeg_stream(rate=self.rate, bipolar_range=self.range) as st:
                        types = [t for _, t in st.channels]
                        data_cols = [k for k, t in enumerate(types) if t in ("bipolar", "reference")]
                        if len(data_cols) < max(self.cols) + 1:
                            raise RuntimeError(f"Pas assez de voies : {st.channels}")
                        cols = [data_cols[c] for c in self.cols]
                        cnt_col = types.index("sample_counter") if "sample_counter" in types else None
                        self.info["stream_channels"] = st.channels
                        self._ready.set()
                        last = None
                        while not self._stop.is_set():
                            time.sleep(0.03)          # le SDK exige une lecture toutes les < 1 s
                            d = st.get_data()
                            if not len(d):
                                continue
                            if cnt_col is not None:
                                c = d[:, cnt_col]
                                if last is not None:
                                    self.dropped += int(max(0, c[0] - last - 1))
                                self.dropped += int(np.sum(np.clip(np.diff(c) - 1, 0, None)))
                                last = c[-1]
                            self._push(d[:, cols] * 1e6)     # V → µV
        finally:
            self._ready.set()


class SimSource(_ThreadedSource):
    """Simulation EOG/EMG. Amplitude EOG : horizontal ±1 → ±250 µV, vertical ±1 → ±200 µV (ordre de grandeur réaliste)."""

    H_GAIN, V_GAIN = 250.0, 200.0

    def __init__(self, rate=config.RATE, script=None, seed=0, realtime=True):
        super().__init__()
        self.rate = rate
        self.script = script          # script(t) -> dict(x, y, jaw, brow, blink)
        self.rng = np.random.default_rng(seed)
        self.realtime = realtime
        self.t = 0.0
        self.gx = self.gy = 0.0
        self.drift = np.zeros(2)
        self.next_blink = 3.0
        self.blink_t0 = -10.0
        self.info = {"type": "simulator", "serial": "SIM", "firmware": 0}

    # ---- Entrée clavier (état global des touches Windows) ----
    @staticmethod
    def _keys():
        import ctypes
        g = ctypes.windll.user32.GetAsyncKeyState
        down = lambda vk: bool(g(vk) & 0x8000)
        x = (1 if down(0x27) else 0) - (1 if down(0x25) else 0)      # → ←
        y = (1 if down(0x26) else 0) - (1 if down(0x28) else 0)      # ↑ ↓
        return {"x": x, "y": y, "jaw": down(ord("J")), "brow": down(ord("B")), "blink": None}

    def generate(self, n):
        """Génère n échantillons (µV)."""
        dt = 1.0 / self.rate
        out = np.empty((n, 4))
        state = self.script(self.t) if self.script else self._keys()
        for k in range(n):
            t = self.t
            if self.script and k % 10 == 0:
                state = self.script(t)
            # saccade : rejoint la cible avec une constante de temps d'environ 30 ms
            a = dt / 0.03
            self.gx += (state["x"] - self.gx) * min(1, a)
            self.gy += (state["y"] - self.gy) * min(1, a)
            # clignement : demi-sinusoïde de +350 µV sur la voie verticale, durée 200 ms
            blink_now = state.get("blink")
            if blink_now is None:            # mode clavier : clignements aléatoires
                if t >= self.next_blink:
                    self.blink_t0 = t
                    self.next_blink = t + self.rng.uniform(3, 7)
            elif blink_now and t - self.blink_t0 > 0.3:
                self.blink_t0 = t
            tb = t - self.blink_t0
            blink = 350 * np.sin(np.pi * tb / 0.2) if 0 <= tb < 0.2 else 0.0
            self.drift += self.rng.normal(0, 0.15, 2)          # marche aléatoire de la ligne de base
            hum = 8 * np.sin(2 * np.pi * 50 * t)
            jaw = state["jaw"] * self.rng.normal(0, 80)
            brow = state["brow"] * self.rng.normal(0, 60)
            out[k, 0] = self.H_GAIN * self.gx + self.drift[0] + 0.05 * jaw + hum
            out[k, 1] = self.V_GAIN * self.gy + blink + self.drift[1] + 0.15 * brow + hum
            out[k, 2] = jaw + 0.1 * brow + hum
            out[k, 3] = brow + 0.1 * jaw + hum
            self.t += dt
        out += self.rng.normal(0, 4, out.shape)
        return out

    def _run(self):
        t0 = time.perf_counter()
        sent = 0
        while not self._stop.is_set():
            time.sleep(0.02)
            due = int((time.perf_counter() - t0) * self.rate)
            if due > sent:
                self._push(self.generate(due - sent))
                sent = due


def make_source(sim=False, **kw):
    return SimSource(**kw) if sim else EegoSource(**kw)
