"""
Test de bout en bout sans matériel : le simulateur génère EOG/EMG selon un scénario → analyse de calibration
→ logique de contrôle, puis vérification des actions produites.

    python test_pipeline.py
"""

import sys

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

import calibration
import config
from controller import Controller
from dsp import RealtimeProcessor
from source import SimSource

RATE = config.RATE
REST = {"x": 0, "y": 0, "jaw": False, "brow": False, "blink": False}


def timeline_script(events):
    """events: [(t0, t1, dict), ...] → script(t)"""
    def script(t):
        s = dict(REST)
        for t0, t1, st in events:
            if t0 <= t < t1:
                s.update(st)
        return s
    return script


def run_calibration():
    protocol = calibration.build_protocol()
    segs = calibration.segments(protocol, RATE)
    events, t = [], 0.0
    for name, dur, *_ in protocol:
        st = {}
        if name in calibration.DIRS:
            # temps de réaction humain : les yeux bougent après 0.3 s
            x, y = calibration.DIRS[name]
            events.append((t + 0.3, t + dur, {"x": x, "y": y}))
        elif name in ("jaw", "brow"):
            events.append((t + 0.3, t + dur, {name: True}))
        t += dur
    events.append((7.0, 7.01, {"blink": True}))      # un clignement au milieu
    sim = SimSource(script=timeline_script(events), seed=1)
    raw = sim.generate(segs[-1][2])
    calib, report = calibration.analyze(raw, segs, RATE)
    print("\n".join(report))
    e = calib["eog"]
    assert e["right"] > 150 and e["left"] < -150, e
    assert e["up"] > 120 and e["down"] < -120, e
    assert calib["ok"], "la calibration sur données simulées doit réussir"
    return calib


def run_control(calib, mode):
    ev = [
        (2.0, 4.0, {"x": 1}),              # regard à droite 2 s → curseur vers la droite
        (5.0, 5.01, {"blink": True}),      # clignement → pas de mouvement vertical
        (6.0, 6.3, {"jaw": True}),         # dents, court → clic gauche
        (7.5, 7.8, {"brow": True}),        # sourcils, court → clic droit
        (9.0, 10.3, {"jaw": True}),        # dents, long → pause
        (11.0, 12.5, {"x": -1}),           # regard à gauche en pause → immobile
        (13.0, 14.3, {"jaw": True}),       # dents, long → reprise
        (15.0, 17.0, {"y": 1}),            # regard en haut → vers le haut (y écran diminue)
        (18.0, 19.5, {"x": -1}),           # regard à gauche → vers la gauche
    ]
    T = 21.0
    sim = SimSource(script=timeline_script(ev), seed=2)
    proc = RealtimeProcessor(RATE)
    ctl = Controller(calib, screen=(1920, 1080), cfg={"mode": mode})
    # d'abord la ligne de base (comme dans mouse_control)
    first = proc.process(sim.generate(int(1.0 * RATE)))
    ctl.base = np.median(first[RATE // 3:, :2], axis=0)
    ctl.t = sim.t
    ctl.recent.extend(first[-ctl.recent.maxlen:, :2])
    blk = int(0.02 * RATE)
    pos = np.array([960.0, 540.0])
    trace, acts = [], []
    while sim.t < T:
        y = proc.process(sim.generate(blk))
        for a in ctl.step(y):
            if a[0] == "move":
                pos += a[1:]
            else:
                acts.append((round(sim.t, 2), a))
        trace.append((sim.t, *pos))
    tr = np.array(trace)

    def at(t):
        return tr[np.searchsorted(tr[:, 0], t)][1:]

    print(f"\n[{mode}] actions hors déplacement : {acts}")
    for t in (2, 3.9, 5.8, 11, 12.6, 15, 16.9, 18, 19.4):
        print(f"  t={t:5.1f}s curseur {at(t).round()}")
    kinds = [a for _, a in acts]
    assert ("click", "left") in kinds, "dents, court → clic gauche attendu"
    assert ("click", "right") in kinds, "sourcils, court → clic droit attendu"
    assert kinds.count(("pause", True)) == 1 and kinds.count(("pause", False)) == 1, kinds
    assert at(3.9)[0] - at(2.0)[0] > 300, "regard à droite → déplacement à droite attendu"
    assert abs(at(5.8)[1] - at(4.6)[1]) < 15, "un clignement ne doit pas provoquer de mouvement vertical"
    assert abs(at(12.6)[0] - at(11.0)[0]) < 5, "pas de mouvement en pause"
    assert at(15.0)[1] - at(16.9)[1] > 150, "regard en haut → déplacement vers le haut attendu"
    assert at(18.0)[0] - at(19.4)[0] > 200, "regard à gauche → déplacement à gauche attendu"
    # le curseur reste quasi immobile pendant un clic
    assert np.linalg.norm(at(6.6) - at(5.9)) < 10


def test_filter_chunking():
    """Le traitement par blocs donne le même résultat que le traitement d'un seul coup"""
    sim = SimSource(script=timeline_script([(1, 2, {"x": 1, "jaw": True})]), seed=3)
    raw = sim.generate(3 * RATE)
    a = RealtimeProcessor(RATE).process(raw)
    p = RealtimeProcessor(RATE)
    b = np.vstack([p.process(raw[i:i + 7]) for i in range(0, len(raw), 7)])
    assert np.allclose(a, b, atol=1e-6), np.abs(a - b).max()


if __name__ == "__main__":
    test_filter_chunking()
    print("✅ filtrage par blocs cohérent")
    calib = run_calibration()
    print("✅ analyse de calibration")
    run_control(calib, "joystick")
    print("✅ mode joystick")
    run_control(calib, "absolute")
    print("✅ mode absolu")
