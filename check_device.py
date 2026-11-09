"""
Autotest de l'eego EE-410 : à lancer en premier quand on branche l'amplificateur.

    python check_device.py                    # infos appareil + impédance + 10 s de données
    python check_device.py --seconds 30 --save test.npz
    python check_device.py --dll D:\\eego-sdk\\windows\\64bit\\eego-SDK.dll

Affiche :
  1. version du SDK, modèle / numéro de série / firmware de l'amplificateur
     (le SDK 1.3.19 ne gère que les firmwares EE-410 011700, 011800, 012000–020000)
  2. liste des voies (vérifier si les 8 voies sont de type bipolar ou reference), fréquences et gammes disponibles
  3. impédances (attention : la première lecture peut être fausse, le script attend quelques secondes)
  4. RMS / crête à crête de chaque voie (µV), continuité de sample_counter (échantillons perdus)
"""

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from eego import EegoSDK, EegoError

HERE = Path(__file__).resolve().parent
from config import DLL_PATH as DEFAULT_DLL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dll", default=DEFAULT_DLL)
    ap.add_argument("--rate", type=int, default=500)
    ap.add_argument("--range", type=float, default=1.5, help="gamme bipolaire (V) : 4 / 1.5 / 0.7 / 0.35")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--no-impedance", action="store_true")
    ap.add_argument("--save", default=None, help="enregistrer les données en .npz")
    args = ap.parse_args()

    with EegoSDK(args.dll) as sdk:
        print(f"Version du SDK : {sdk.version}")
        amp = sdk.get_amplifier()
        with amp:
            print(f"Modèle : {amp.type}   N° de série : {amp.serial}   Firmware : {amp.firmware}")
            print(f"Fréquences d'échantillonnage : {amp.sampling_rates}")
            print(f"Gammes référentielles : {amp.reference_ranges}   Gammes bipolaires : {amp.bipolar_ranges}")
            chans = amp.channels
            print(f"Voies de l'amplificateur ({len(chans)}) : {chans}")

            if not args.no_impedance:
                with amp.open_impedance_stream() as imp:
                    print(f"Voies du flux d'impédance : {imp.channels}")
                    time.sleep(3)          # la première série d'impédances est fausse (problème connu, voir release notes)
                    z = imp.get_data()
                    if len(z):
                        for (idx, typ), val in zip(imp.channels, z[-1]):
                            print(f"  {typ:>20s} {idx:2d}: {val / 1e3:8.1f} kΩ")

            with amp.open_eeg_stream(rate=args.rate, bipolar_range=args.range) as st:
                print(f"Voies du flux EEG ({st.n_channels}) : {st.channels}")
                chunks, t_end = [], time.time() + args.seconds
                while time.time() < t_end:
                    time.sleep(0.1)        # lire toutes les < 1 s, sinon perte d'échantillons
                    d = st.get_data()
                    if len(d):
                        chunks.append(d)
                        print(f"\r{sum(len(c) for c in chunks)} échantillons reçus", end="")
                print()
                data = np.concatenate(chunks) if chunks else np.empty((0, st.n_channels))
                types = [t for _, t in st.channels]

    report(data, types, args.rate)
    if args.save:
        np.savez(args.save, data=data, channels=np.array(types), rate=args.rate)
        print(f"Enregistré dans {args.save}")


def report(data, types, rate):
    if not len(data):
        print("Aucune donnée reçue")
        return
    print(f"{len(data)} échantillons au total ≈ {len(data) / rate:.1f} s")
    for k, t in enumerate(types):
        x = data[:, k]
        if t in ("bipolar", "reference"):
            uv = (x - x.mean()) * 1e6
            print(f"  Voie {k:2d} ({t}) : RMS {uv.std():8.1f} µV   crête à crête {np.ptp(uv):8.1f} µV")
        elif t == "sample_counter":
            gaps = np.diff(x)
            bad = int(np.sum(gaps != 1))
            print(f"  sample_counter : {x[0]:.0f} → {x[-1]:.0f}, {bad} discontinuité(s)" + ("  ✅" if bad == 0 else "  ⚠ échantillons perdus"))
        elif t == "trigger":
            print(f"  Valeurs de trigger observées : {sorted(set(x.astype(int).tolist()))}")


if __name__ == "__main__":
    try:
        main()
    except EegoError as e:
        print(f"❌ {e}")
        sys.exit(1)
