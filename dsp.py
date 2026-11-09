"""
Filtrage en temps réel : entrée = 4 signaux bruts (µV), sortie =
    heog, veog : EOG après passe-bas 10 Hz + coupe-bande secteur (composante continue conservée, µV)
    jaw, brow  : enveloppe EMG après passe-bande 30–200 Hz + coupe-bande (RMS glissante, µV)
Les filtres gardent leur état : on peut envoyer les données bloc par bloc (temps réel) et le résultat
est identique à un traitement d'un seul coup (la même classe sert à l'analyse hors ligne).
"""

import numpy as np
from scipy import signal

import config


class RealtimeProcessor:
    def __init__(self, rate=config.RATE):
        self.rate = rate
        nyq = rate / 2
        self.sos_eog = signal.butter(4, config.EOG_LOWPASS / nyq, "low", output="sos")
        hi = min(config.EMG_BAND[1], nyq * 0.9)
        self.sos_emg = signal.butter(4, [config.EMG_BAND[0] / nyq, hi / nyq], "band", output="sos")
        # Coupe le secteur et ses harmoniques (50/100/150/200 Hz) : sur l'appareil, la voie EOG verticale montre
        # des harmoniques à 100 et 150 Hz qui relèvent le niveau de repos de l'enveloppe EMG
        secs = []
        f = config.NOTCH
        while f < nyq * 0.95:
            b, a = signal.iirnotch(f, 30, rate)
            secs.append(signal.tf2sos(b, a))
            f += config.NOTCH
        self.sos_notch = np.vstack(secs)
        self.env_len = max(1, int(config.EMG_ENV_WIN * rate))
        self.reset()

    def reset(self):
        self.z_eog = None
        self.z_emg = None
        self.z_notch = None
        self.sq_hist = np.zeros((0, 2))

    def _init_states(self, first):
        # Initialise l'état du passe-bas avec le premier échantillon, pour éviter la longue montée de 0 jusqu'au niveau continu
        zi_eog = signal.sosfilt_zi(self.sos_eog)
        self.z_eog = zi_eog[:, :, None] * first[None, None, :2]
        self.z_notch = signal.sosfilt_zi(self.sos_notch)[:, :, None] * first[None, None, :]
        self.z_emg = np.zeros((self.sos_emg.shape[0], 2, 2))

    def process(self, raw):
        """raw : (n, 4) en µV, dans l'ordre de config.SIGNALS → (n, 4) signaux traités."""
        raw = np.asarray(raw, dtype=float)
        if raw.size == 0:
            return np.empty((0, 4))
        if self.z_eog is None:
            self._init_states(raw[0])
        x, self.z_notch = signal.sosfilt(self.sos_notch, raw, axis=0, zi=self.z_notch)
        eog, self.z_eog = signal.sosfilt(self.sos_eog, x[:, :2], axis=0, zi=self.z_eog)
        emg, self.z_emg = signal.sosfilt(self.sos_emg, x[:, 2:], axis=0, zi=self.z_emg)
        # Enveloppe RMS glissante
        sq = np.vstack([self.sq_hist, emg ** 2])
        c = np.cumsum(np.vstack([np.zeros((1, 2)), sq]), axis=0)
        L = self.env_len
        n = len(raw)
        start = len(sq) - n
        idx = np.arange(start, len(sq))
        lo = np.maximum(idx + 1 - L, 0)
        env = np.sqrt((c[idx + 1] - c[lo]) / (idx + 1 - lo)[:, None])
        self.sq_hist = sq[-(L - 1):] if L > 1 else np.zeros((0, 2))
        return np.hstack([eog, env])
