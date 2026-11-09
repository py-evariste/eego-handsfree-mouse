# Souris mains libres avec eego (EOG + EMG)

**Français** | [Português](README.pt.md)

Une souris pilotée sans les mains, avec l'amplificateur eemagine eego EE-410 (8 voies bipolaires) et les électrodes à pression XE-293 :

- Les mouvements des yeux (EOG) déplacent le curseur.
- L'activité musculaire du visage (EMG) fait les clics : serrer les dents = clic gauche, lever les sourcils = clic droit.

## Fichiers

| Fichier | Rôle |
| --- | --- |
| `config.py` | Tous les paramètres : affectation des voies, fréquence d'échantillonnage, filtres, vitesse, durée des gestes. Le montage des électrodes est décrit en tête du fichier |
| `eego.py` | Interface ctypes vers la DLL du eego SDK 1.3.19, pour lire les données directement en Python |
| `source.py` | Sources de données : l'amplificateur réel `EegoSource` et le simulateur au clavier `SimSource` |
| `dsp.py` | Filtrage temps réel : EOG passe-bas 10 Hz ; EMG passe-bande 30–200 Hz puis enveloppe RMS ; coupe-bande 50 Hz et harmoniques (100/150/200 Hz) |
| `check_device.py` | Autotest de l'appareil : modèle, firmware, type de voies, impédance, qualité du signal, échantillons perdus |
| `live_view.py` | Visualisation du signal en temps réel, pour vérifier les électrodes ; peut aussi enregistrer les données |
| `calibrate.py` + `calibration.py` | Calibration guidée en plein écran (environ 1 min 30), génère `calibration.json` |
| `controller.py` | Logique de contrôle : EOG → curseur, EMG → gestes. Calcul pur, testable séparément |
| `mouse_control.py` | Programme principal |
| `test_pipeline.py` | Test de bout en bout sans matériel (données simulées : calibration, déplacement, clics, pause, rejet des clignements) |

Dépendances : `pip install numpy scipy matplotlib` (tkinter est fourni avec Python).

---

## Étapes avec l'appareil

### 0. Préparer le SDK (une seule fois)

Le dossier du SDK déjà décompressé qui se trouvait sur la clé USB **est corrompu** : la DLL 64 bits ne fait que 352 Ko (la version complète fait 639 Ko) et les fichiers du pilote sont tronqués aussi.
Il faut décompresser à nouveau depuis le zip :

1. Créer un dossier `sdk` dans ce répertoire et y décompresser le contenu de `eego-SDK-1.3.19.40453.zip` (le zip contient directement les dossiers `eemagine`, `linux` et `windows`).
2. Vérifier que `sdk\windows\64bit\eego-SDK.dll` existe et fait environ 639 Ko.

### 1. Installer le pilote (une seule fois)

1. Brancher l'amplificateur, ouvrir le Gestionnaire de périphériques et trouver le périphérique inconnu avec le point d'exclamation jaune.
2. Clic droit → Mettre à jour le pilote → Parcourir mon poste de travail → choisir `sdk\windows\driver\win8\x64`. Sous Windows 10/11 on utilise aussi cette version win8.
3. **Sous Windows 11 le pilote est bloqué** (le Gestionnaire de périphériques affiche le code 39, « une stratégie de contrôle d'application a bloqué ce fichier ») :
   ce pilote Cypress de 2017 utilise l'ancienne signature croisée (cross-signing), à laquelle les versions récentes de Windows 11 ne font plus confiance. Désactiver l'intégrité de la mémoire ou la liste de blocage des pilotes **ne sert à rien**.
   La solution qui fonctionne est de désactiver temporairement le contrôle obligatoire des signatures de pilotes à chaque démarrage : maintenir Maj et cliquer sur « Redémarrer » → Dépannage → Options avancées → Paramètres → Redémarrer → appuyer sur **7**.
   Valable uniquement pour ce démarrage ; il faut recommencer après chaque redémarrage.

### 2. Autotest de l'appareil (sans électrodes)

```
python check_device.py --no-impedance
```

Vérifier trois points :

- **Version du firmware** : le SDK 1.3.19 ne gère que 011700, 011800 et 012000–020000.
- **Type de voies** : les 8 voies doivent apparaître comme `bipolar`.
- **sample_counter** : doit indiquer 0 discontinuité.

### 3. Poser les électrodes

Nettoyer la peau à l'alcool, coller les électrodes jetables d'ECG, puis clipser les connecteurs à pression. Rouge = +, noir = −.
**Les électrodes doivent être neuves, avec un gel humide** ; la masse (GND) doit être bien en contact. Maintenir les électrodes et les câbles avec du ruban adhésif.
Avec des électrodes usagées ou un mauvais contact de la masse, la composante continue dérive de plusieurs centaines de mV et le bruit EMG atteint plusieurs centaines de µV : le signal est inutilisable. Avec un bon contact, la composante continue reste sous quelques dizaines de mV et l'EMG au repos est à 3–6 µV.

**Par défaut, montage à 5 électrodes** (`LAYOUT = "5pads"` dans `config.py`) : seulement les voies 1, 2 et GND.
L'EMG des dents et des sourcils est extrait de la partie haute fréquence des voies 1 et 2.

| Étiquette XE-293 | Rôle | Rouge (+) | Noir (−) |
| --- | --- | --- | --- |
| 1 | EOG horizontal + EMG des dents | 1 cm à l'extérieur du coin externe de l'œil droit | 1 cm à l'extérieur du coin externe de l'œil gauche |
| 2 | EOG vertical + EMG des sourcils | 1 cm au-dessus du sourcil gauche | Sur le bord supérieur de la pommette, sous l'orbite gauche |
| GND | Masse | Milieu du front | |
| 3–8 | Inutilisées | Isoler avec du ruban adhésif, sans contact avec du métal | |

Si l'on a assez d'électrodes, on peut utiliser le montage à 9 électrodes (avant de lancer : `set EEGO_LAYOUT=9pads`), avec les voies 3 et 4 posées séparément :

| Étiquette XE-293 | Rôle | Rouge (+) | Noir (−) |
| --- | --- | --- | --- |
| 1 | EOG horizontal | 1 cm à l'extérieur du coin externe de l'œil droit | 1 cm à l'extérieur du coin externe de l'œil gauche |
| 2 | EOG vertical | 1 cm au-dessus du sourcil gauche | Juste sous la paupière inférieure gauche |
| 3 | EMG des dents | Masséter gauche (là où il gonfle quand on serre les dents) | Sur le même muscle, à 2 cm du rouge |
| 4 | EMG des sourcils | Front, 2–3 cm au-dessus du sourcil droit | À la même hauteur, à 2 cm du rouge |
| GND | Masse | Milieu du front | |
| 5–8 | Inutilisées | Chaque paire côte à côte derrière l'oreille (mastoïde) | |

Si les électrodes sont branchées sur d'autres voies, il suffit de modifier `CHANNELS` dans `config.py`.

### 4. Visualiser le signal

```
python live_view.py
```

Faire les mouvements suivants et vérifier que la courbe correspondante réagit :

- Regarder à droite puis à gauche : la ligne 1 monte et descend nettement.
- Regarder en haut puis en bas : la ligne 2 monte et descend nettement.
- Cligner des yeux : un pic apparaît sur la ligne 2.
- Serrer les dents : la ligne 3 monte nettement.
- Lever les sourcils : la ligne 4 monte nettement.

Pour enregistrer ces données, ajouter `--save test1.npz`.

### 5. Calibration

```
python calibrate.py
```

Suivre les consignes à l'écran :

1. **Bouger seulement les yeux pour suivre le point rouge ; ne pas bouger la tête.**
2. Quand la consigne devient orange, serrer les dents.
3. Quand la consigne devient bleue, lever les sourcils.

À la fin, l'amplitude de chaque mouvement s'affiche avec le verdict. En cas d'échec, ajuster les électrodes selon les remarques et recalibrer.

### 6. D'abord un essai, puis le contrôle réel

Mode essai : le programme affiche seulement les actions, sans bouger la souris.

```
python mouse_control.py --dry-run
```

Quand l'essai est concluant, passer au contrôle réel :

```
python mouse_control.py
```

Au démarrage, regarder le centre de l'écran pendant 2 secondes pendant que le programme mesure la ligne de base ; après le bip, on peut commencer.

| Action | Effet |
| --- | --- |
| Décaler le regard à gauche, à droite, en haut ou en bas | Le curseur avance en continu dans cette direction, d'autant plus vite que l'écart est grand ; il s'arrête quand on regarde à nouveau le centre |
| Serrer les dents une fois (0,1–0,6 s) | Clic gauche (deux fois rapidement = double-clic) |
| Serrer les dents plus d'1 s | Pause / reprise (son grave = pause, son aigu = reprise) |
| Lever les sourcils une fois | Clic droit |
| Regarder le centre de l'écran et lever les sourcils pendant 1 s | Recentrage (quand la ligne de base a dérivé et que le curseur bouge tout seul) |
| Touches F8 / Échap | Pause / quitter (sécurité pour la personne à côté) |

Pour le mode « le curseur va là où l'on regarde », ajouter `--mode absolute`. Ce mode doit être recentré souvent.

### Retour d'expérience (premier essai avec l'appareil, 9/11/2026)

- En montage à 5 électrodes, le geste des sourcils est peu fiable ; on conseille `--no-brow` : le clic droit devient « serrer les dents 0,6–1 s ».
- Serrer les dents ou lever les sourcils tire la peau autour des yeux et fait sauter la composante continue de l'EOG de plusieurs mV ; le programme **se recentre donc automatiquement** à la fin de chaque geste facial.
- La calibration peut inverser la gauche et la droite ou mesurer des amplitudes verticales trop faibles. On peut les fixer à la main (en µV, avec le signe), par exemple :
  `python mouse_control.py --no-brow --right-uv 300 --left-uv -300 --up-uv 150 --down-uv -120`
  Plus la valeur est petite, plus c'est sensible ; si une direction est inversée, changer le signe. On peut aussi modifier directement ces quatre valeurs dans la section `eog` de `calibration.json`.

### Réglages courants

Réglables par arguments en ligne de commande ou directement dans `config.py`.

- **Curseur trop rapide ou trop lent** : `--speed 500` (en pixels par seconde).
- **Le curseur bouge au moindre mouvement des yeux** : agrandir la zone morte, par exemple `--dead-zone 0.5`.
- **Il faut beaucoup décaler le regard pour que le curseur bouge** : réduire la zone morte, par exemple `--dead-zone 0.25`.
- **Les clics avec les dents ratent ou se déclenchent seuls** : recalibrer en serrant plus fort. On peut aussi augmenter la valeur `0.35` dans `calibration.py`.
- **En cas de problème** : conserver `calib_raw.npz` ou un enregistrement fait avec `live_view --save` pour une analyse hors ligne.

## Suivi du regard par webcam (étape 1 : caméra seule)

Objectif : stabiliser le contrôle en ajoutant la webcam. La caméra donne une position **absolue** du regard
(pas de dérive), l'EOG est rapide mais dérive, l'EMG reste le plus fiable pour cliquer. Cette première étape
fait fonctionner la caméra seule ; la fusion caméra + EOG viendra ensuite.

Dépendances supplémentaires : `pip install mediapipe opencv-python`
(le modèle `face_landmarker.task` est téléchargé automatiquement dans `models/` au premier lancement).

| Fichier | Rôle |
| --- | --- |
| `camera.py` | Capture webcam + repères MediaPipe (iris inclus) + pose de la tête → caractéristiques ; `python camera.py` ouvre un aperçu |
| `gaze_model.py` | Régression ridge caractéristiques → coordonnées écran, choix automatique de la régularisation, filtre de lissage « 1 € » |
| `camera_calibrate.py` | Entraînement plein écran : grille de 9 points × 2 tours + 5 points de validation, rapport d'erreur en pixels |
| `camera_mouse.py` | Le curseur va là où l'on regarde ; `--clicks` ajoute les clics par serrage des dents (eego) |
| `test_camera.py` | Tests sans caméra (repères synthétiques, modèle, filtre, analyse d'entraînement) |

Étapes :

```
python camera.py                    # 1. vérifier que le visage et les iris sont détectés (points verts sur les iris)
python camera_calibrate.py          # 2. entraîner le modèle : suivre le point des yeux
python camera_mouse.py --dry-run    # 3. essai : affiche la position estimée
python camera_mouse.py --clicks     # 4. caméra pour déplacer + dents pour cliquer
```

Conseils : visage bien éclairé de face (pas de fenêtre derrière soi), caméra à hauteur des yeux,
même distance et même position que pendant l'entraînement. Si l'on bouge beaucoup ou si l'éclairage change,
refaire `camera_calibrate.py`. Réglages : `--smooth` (plus petit = plus stable), `--dead-px` (seuil anti-tremblement).
Si l'estimation est décalée en permanence (par exemple trop haut) : regarder le centre de l'écran et appuyer sur **F9** ;
le programme affiche la correction, à réutiliser ensuite avec `--offset-x` / `--offset-y`.

## Tests (sans matériel)

```
python test_pipeline.py          # test automatique
python live_view.py --sim        # simulation au clavier : flèches = direction du regard, J maintenue = dents, B maintenue = sourcils
python calibrate.py --sim
python mouse_control.py --sim --dry-run
python test_camera.py           # tests du suivi par caméra
```
