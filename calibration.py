"""
Définition du protocole de calibration + analyse (séparées de l'interface, pour pouvoir tester avec des données simulées).

Protocole : repos en regardant le centre → regarder 2 fois à droite / à gauche / en haut / en bas
            (en revenant au centre à chaque fois) → serrer les dents 3 fois → lever les sourcils 3 fois
Analyse :
    EOG : amplitude de chaque direction = médiane en regardant la cible − médiane de la phase « centre »
          précédente (médiane sur les répétitions)
    EMG : niveau d'enveloppe au repos rest, enveloppe pendant l'effort active,
          seuils hi = rest + 0.35 (active − rest), lo = rest + 0.20 (active − rest)
"""

import json
import time

import numpy as np

import config
from dsp import RealtimeProcessor

# (nom de la phase, durée s, consigne à l'écran, position du point (x, y) normalisée -1..1 ou None)
DIRS = {"right": (1, 0), "left": (-1, 0), "up": (0, 1), "down": (0, -1)}
DIR_FR = {"right": "à droite", "left": "à gauche", "up": "en haut", "down": "en bas"}


def build_protocol(reps_eye=2, reps_emg=3):
    p = [("rest", 5.0, "Détendez-vous, regardez le point central, clignez le moins possible", (0, 0))]
    for _ in range(reps_eye):
        for d, xy in DIRS.items():
            p.append(("center", 2.0, "Regardez le point central", (0, 0)))
            p.append((d, 2.5, f"Bougez seulement les yeux : regardez le point {DIR_FR[d]} (ne bougez pas la tête)", xy))
    p.append(("center", 2.0, "Regardez le point central", (0, 0)))
    for i in range(reps_emg):
        p.append(("emg_rest", 2.5, f"Préparez-vous à serrer les dents ({i + 1}/{reps_emg}), visage détendu", (0, 0)))
        p.append(("jaw", 1.5, "Serrez fort les dents !", (0, 0)))
    for i in range(reps_emg):
        p.append(("emg_rest", 2.5, f"Préparez-vous à lever les sourcils ({i + 1}/{reps_emg}), visage détendu", (0, 0)))
        p.append(("brow", 1.5, "Levez fort les sourcils !", (0, 0)))
    p.append(("emg_rest", 2.0, "Détendez-vous, c'est presque fini", (0, 0)))
    return p


def segments(protocol, rate):
    """Intervalle d'échantillons de chaque phase [(name, start, end), ...]"""
    out, s = [], 0
    for name, dur, *_ in protocol:
        n = int(round(dur * rate))
        out.append((name, s, s + n))
        s += n
    return out


def _core(a, s, e, frac=0.6):
    """Garde la dernière fraction frac d'un segment (ignore le temps de réaction et la saccade)"""
    n = e - s
    return a[e - int(n * frac):e]


def analyze(raw, segs, rate=config.RATE):
    """raw : données brutes (n, 4) en µV ; segs : [(nom de phase, échantillon de début, échantillon de fin), ...]
    → (dict de calibration, liste de lignes du rapport)"""
    proc = RealtimeProcessor(rate)
    y = proc.process(raw)
    segs = [(n, s, min(e, len(y))) for n, s, e in segs if s < len(y) - 10]
    report = []

    # ---------- EOG ----------
    amps = {d: [] for d in DIRS}
    prev_center = None
    for name, s, e in segs:
        if name in ("center", "rest"):
            prev_center = np.median(_core(y[:, :2], s, e), axis=0)
        elif name in DIRS and prev_center is not None:
            m = np.median(_core(y[:, :2], s, e), axis=0)
            ch = 0 if name in ("right", "left") else 1
            amps[name].append(m[ch] - prev_center[ch])
    eog = {}
    for d in DIRS:
        if not amps[d]:
            raise ValueError(f"Les données de calibration ne contiennent pas de phase « {DIR_FR[d]} »")
        eog[d] = float(np.median(amps[d]))
    rest = [(s, e) for n, s, e in segs if n == "rest"][0]
    # Estimation robuste du bruit (écart absolu médian), pour que quelques clignements au repos ne gonflent pas le bruit
    r_seg = _core(y[:, :2], *rest, frac=0.8)
    noise = 1.4826 * np.median(np.abs(r_seg - np.median(r_seg, axis=0)), axis=0)
    last_center = [(s, e) for n, s, e in segs if n == "center"][-1]
    base = np.median(_core(y[:, :2], *last_center), axis=0)
    eog.update(base_h=float(base[0]), base_v=float(base[1]),
               noise_h=float(noise[0]), noise_v=float(noise[1]))

    ok = True
    for axis, (a, b), nz in (("horizontal", ("right", "left"), noise[0]), ("vertical", ("up", "down"), noise[1])):
        va, vb = eog[a], eog[b]
        report.append(f"EOG {axis} : {DIR_FR[a]} {va:+.0f} µV, {DIR_FR[b]} {vb:+.0f} µV, bruit au repos {nz:.1f} µV")
        good = {d: abs(v) >= max(3 * nz, 20) for d, v in ((a, va), (b, vb))}
        if good[a] and good[b] and np.sign(va) != np.sign(vb):
            continue
        ok = False
        if good[a] and good[b]:
            # même signe : on garde la plus grande amplitude, l'autre est prise symétrique
            keep, other = (a, b) if abs(va) >= abs(vb) else (b, a)
        elif good[a] or good[b]:
            keep, other = (a, b) if good[a] else (b, a)
        else:
            keep = other = None
        if keep:
            eog[other] = -eog[keep]
            report.append(f"  ⚠ Regard {DIR_FR[other]} mal mesuré, valeur symétrique utilisée : "
                          f"{DIR_FR[other]} = {eog[other]:+.0f} µV (d'après {DIR_FR[keep]})")
        else:
            default = 200.0
            eog[a], eog[b] = default, -default
            report.append(f"  ❌ Les deux directions {axis}es sont mal mesurées, valeur par défaut ±{default:.0f} µV ; "
                          f"on peut fixer l'amplitude à la main avec --{a}-uv / --{b}-uv de mouse_control.py")
        eog[f"{axis}_fallback"] = True

    # ---------- EMG ----------
    emg = {}
    rest_idx = [k for k, (n, s, e) in enumerate(segs) if n in ("rest", "center", "emg_rest")]
    for ch, key in ((2, "jaw"), (3, "brow")):
        rest_env = np.concatenate([_core(y[:, ch], segs[k][1], segs[k][2], 0.8) for k in rest_idx])
        r = float(np.median(rest_env))
        r99 = float(np.percentile(rest_env, 99))
        act = [float(np.median(_core(y[:, ch], s, e, 0.7))) for n, s, e in segs if n == key]
        if not act:
            raise ValueError(f"Les données de calibration ne contiennent pas de phase {key}")
        a = float(np.median(act))
        hi = max(r + 0.35 * (a - r), r99 * 1.3)
        lo = max(r + 0.20 * (a - r), r99 * 1.1)
        lo = min(lo, hi * 0.8)
        emg.update({f"{key}_rest": r, f"{key}_active": a, f"{key}_hi": hi, f"{key}_lo": lo})
        fr = "serrage des dents" if key == "jaw" else "lever de sourcils"
        report.append(f"EMG {fr} : repos {r:.1f} µV, effort {a:.1f} µV ({a / max(r, 1e-9):.1f} fois), seuil {hi:.1f} µV")
        if a < 3 * r:
            if key == "jaw":
                ok = False
                report.append(f"  ⚠ Le signal de {fr} est peu différent du repos : vérifier la position des électrodes "
                              f"et serrer plus fort")
            else:
                # Si le lever de sourcils n'est pas détectable, on le désactive pour éviter les déclenchements
                # intempestifs ; le clic droit devient « serrer les dents 0,6–1 s »
                emg.update(brow_hi=1e9, brow_lo=1e9, brow_disabled=True)
                report.append("  ⚠ Signal des sourcils trop faible, geste désactivé ; clic droit = « serrer les dents 0,6–1 s », "
                              "recentrage = pause puis reprise")

    # Diaphonie : pendant un serrage de dents, la voie sourcils dépasse-t-elle son seuil (et inversement) ?
    for key, other, och in (("jaw", "brow", 3), ("brow", "jaw", 2)):
        cross = [float(np.median(_core(y[:, och], s, e, 0.7))) for n, s, e in segs if n == key]
        if cross and np.median(cross) > emg[f"{other}_hi"]:
            report.append(f"  ⚠ Pendant « {key} », la voie « {other} » dépasse aussi son seuil (diaphonie) ; le programme "
                          f"ne retient que le plus fort, mais il vaut mieux éloigner les deux groupes d'électrodes")

    calib = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rate": rate, "eog": eog, "emg": emg, "ok": ok}
    report.append("✅ Calibration réussie" if ok else
                  "⚠ Calibration imparfaite : voir les remarques ci-dessus, ajuster les électrodes et recalibrer "
                  "(on peut aussi essayer tel quel)")
    return calib, report


def save(calib, path=config.CALIB_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(calib, f, ensure_ascii=False, indent=2)


def load(path=config.CALIB_FILE):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
