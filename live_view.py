"""
Visualisation en temps réel : pour vérifier le signal en posant les électrodes (10 dernières secondes).

    python live_view.py                  # amplificateur réel
    python live_view.py --sim            # simulateur (flèches / J / B)
    python live_view.py --save rec.npz   # enregistre aussi les données brutes (sauvegardées à la fermeture)

Les quatre lignes sont : EOG horizontal, EOG vertical (µV, passe-bas 10 Hz), enveloppe EMG des dents,
enveloppe EMG des sourcils (µV).
Aspect normal :
    regard à droite → l'EOG horizontal monte nettement (quelques dizaines à centaines de µV), à gauche → il descend ;
    en haut / en bas → l'EOG vertical monte / descend ; clignement → pic sur l'EOG vertical
    serrer les dents → la 3e ligne monte nettement (plus de 5 fois le repos) ; lever les sourcils → la 4e ligne monte
Si une ligne est une large bande de bruit ou reste collée en haut ou en bas, l'électrode est probablement mal collée.
"""

import argparse
import sys

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
plt.rcParams["font.sans-serif"] = ["Segoe UI", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import config
from dsp import RealtimeProcessor
from source import make_source

TITLES = ["EOG horizontal HEOG (µV)", "EOG vertical VEOG (µV)", "Enveloppe EMG dents (µV)", "Enveloppe EMG sourcils (µV)"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--save", default=None)
    args = ap.parse_args()

    rate = config.RATE
    n = int(args.seconds * rate)
    buf = np.full((n, 4), np.nan)
    raw_log = []
    proc = RealtimeProcessor(rate)
    t = np.arange(n) / rate - args.seconds

    src = make_source(args.sim).start()
    print(f"Source de données : {src.info}")
    fig, axes = plt.subplots(4, 1, sharex=True, figsize=(12, 8))
    lines = []
    for ax, title in zip(axes, TITLES):
        (ln,) = ax.plot(t, buf[:, 0], lw=0.8)
        ax.set_title(title, fontsize=10, loc="left")
        ax.grid(alpha=0.3)
        lines.append(ln)
    axes[-1].set_xlabel("Temps (s)")
    axes[-1].set_xlim(-args.seconds, 0)
    fig.tight_layout()
    status = fig.text(0.99, 0.995, "", ha="right", va="top", fontsize=9)

    def update(_):
        nonlocal buf
        raw = src.read()
        if len(raw):
            if args.save:
                raw_log.append(raw)
            y = proc.process(raw)
            k = min(len(y), n)
            buf = np.vstack([buf[k:], y[-k:]])
        for i, (ln, ax) in enumerate(zip(lines, axes)):
            ln.set_ydata(buf[:, i])
            v = buf[:, i][~np.isnan(buf[:, i])]
            if len(v):
                if i < 2:     # EOG : affiché autour de la médiane, plage d'au moins ±100 µV
                    c = np.median(v)
                    r = max(100, np.percentile(np.abs(v - c), 99) * 1.2)
                    ax.set_ylim(c - r, c + r)
                else:
                    ax.set_ylim(0, max(20, np.percentile(v, 99.5) * 1.2))
        last = buf[-rate:]
        if not np.isnan(last).any():
            status.set_text(f"Dernière seconde : HEOG continu {last[:, 0].mean() / 1000:.0f} mV   VEOG continu {last[:, 1].mean() / 1000:.0f} mV   "
                            f"env. dents {np.median(last[:, 2]):.0f} µV   env. sourcils {np.median(last[:, 3]):.0f} µV   "
                            f"{src.total} échantillons reçus   {src.dropped} perdus")
        else:
            status.set_text(f"{src.total} échantillons reçus   {src.dropped} perdus")
        return lines

    anim = FuncAnimation(fig, update, interval=50, cache_frame_data=False)  # noqa: F841
    try:
        plt.show()
    finally:
        src.stop()
        if args.save and raw_log:
            raw = np.concatenate(raw_log)
            np.savez(args.save, raw=raw, signals=config.SIGNALS, rate=rate)
            print(f"{len(raw)} échantillons enregistrés dans {args.save}")


if __name__ == "__main__":
    main()
