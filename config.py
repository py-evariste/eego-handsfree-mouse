"""
Paramètres communs. En général, sur l'appareil, il suffit de modifier CHANNELS
(si les électrodes sont branchées sur d'autres voies).

Montage des électrodes (XE-293, rouge = +, noir = −, le numéro de voie est l'étiquette du câble) :
    Voie 1  EOG horizontal (HEOG)   rouge : 1 cm à l'extérieur du coin externe de l'œil droit
                                    noir  : 1 cm à l'extérieur du coin externe de l'œil gauche
    Voie 2  EOG vertical (VEOG)     rouge : 1 cm au-dessus du sourcil gauche
                                    noir  : juste sous la paupière inférieure gauche
    Voie 3  EMG masséter            rouge et noir sur le masséter gauche (devant et sous le lobe de
                                    l'oreille, là où le muscle gonfle quand on serre les dents), à 2 cm l'un de l'autre
    Voie 4  EMG frontal             rouge et noir sur le front, 2–3 cm au-dessus du sourcil droit, à 2 cm l'un de l'autre
    GND                             milieu du front (ou mastoïde)
    Voies 5–8 inutilisées, mais la fiche technique demande que toutes les électrodes soient posées
    sur la personne : coller chaque paire rouge/noir côte à côte derrière l'oreille (mastoïde).
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------- Matériel ----------
import os
DLL_PATH = os.environ.get("EEGO_DLL", str(HERE / "sdk" / "windows" / "64bit" / "eego-SDK.dll"))
RATE = 500                 # Hz ; l'EE-410 accepte 500/512/1000/1024/2000/2048
BIPOLAR_RANGE = 1.5        # V ; choix 4 / 1.5 / 0.7 / 0.35 ; 1.5 suffit pour EOG + EMG sans saturer

# Fonction → voie de l'amplificateur (à partir de 0 : la « voie 1 » du XE-293 = 0)
# "5pads" quand on a peu d'électrodes : seulement les voies 1, 2 et GND (5 électrodes) ;
#   l'EMG des dents est pris dans les hautes fréquences de la voie 1 (les électrodes des coins des yeux
#   sont sur le muscle temporal, qui se contracte quand on serre les dents),
#   l'EMG des sourcils dans les hautes fréquences de la voie 2 (l'électrode au-dessus du sourcil est sur le frontal).
# "9pads" quand on a assez d'électrodes : voies 3 et 4 posées séparément sur le masséter et le frontal, EMG plus propre.
LAYOUT = os.environ.get("EEGO_LAYOUT", "5pads")
CHANNELS = {
    "9pads": {"heog": 0, "veog": 1, "jaw": 2, "brow": 3},
    "5pads": {"heog": 0, "veog": 1, "jaw": 0, "brow": 1},
}[LAYOUT]
SIGNALS = ["heog", "veog", "jaw", "brow"]   # ordre interne des signaux

# ---------- Filtrage ----------
EOG_LOWPASS = 10.0         # Hz ; l'EOG ne garde que les variations lentes (composante continue conservée, pas de passe-haut)
EMG_BAND = (30.0, 200.0)   # Hz ; passe-bande EMG
NOTCH = 50.0               # Hz ; secteur (50 Hz en Europe/Chine) et ses harmoniques
EMG_ENV_WIN = 0.10         # s ; fenêtre de l'enveloppe EMG (RMS glissante)

# ---------- Fichier de calibration ----------
CALIB_FILE = HERE / "calibration.json"

# ---------- Contrôle ----------
CONTROL = {
    "mode": "joystick",        # joystick : regarder dans une direction fait avancer le curseur dans cette direction ;
                               # absolute : le curseur va là où l'on regarde
    "dead_zone": 0.35,         # zone morte du mode joystick (fraction de l'amplitude calibrée, 0~1)
    "max_speed": 900,          # vitesse maximale du mode joystick, pixels/s
    "speed_exponent": 1.6,     # courbe de vitesse : plus c'est grand, plus les petits écarts sont lents (positionnement fin)
    "vertical_hold": 0.30,     # s ; un écart vertical doit durer au moins ce temps (filtre les clignements)
    "absolute_smoothing": 0.15,  # s ; constante de lissage du curseur en mode absolu
    "baseline_tau": 3.0,       # s ; constante de suivi de la dérive quand les yeux sont au centre
    "baseline_tau_out": 15.0,  # s ; constante de suivi quand les yeux sont décalés (plus lente)
    "loop_dt": 0.02,           # s ; période de la boucle de contrôle

    # Gestes EMG (durées en s)
    "click_min": 0.08,         # plus court que ça : considéré comme du bruit
    "click_max": 0.60,         # plus court que ça : « clic »
    "long_press": 1.00,        # plus long que ça : « pause / reprise »
    "double_click_gap": 0.40,  # deux serrages courts plus rapprochés que ça : double-clic
    "refractory": 0.30,        # temps de repos après un geste
}
