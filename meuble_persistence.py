# meuble_persistence.py -- lecture/ecriture des valeurs par defaut (JSON a
# cote de l'add-in) et des parametres stockes en attribut sur chaque
# composant meuble (ATTR_GROUP/ATTR_PARAMS/ATTR_DOORS).

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
# Fichier JSON (a cote de l'add-in, donc partage par tous les documents et
# persistant d'une session Fusion a l'autre) ou sont memorisees les valeurs
# par defaut personnalisees via le bouton Enregistrer par defaut.
DEFAULTS_FILE = os.path.join(SCRIPT_DIR, 'meuble_defaults.json')

ATTR_GROUP = 'MeubleParametrique'
ATTR_PARAMS = 'params'
ATTR_DOORS = 'doors'

# id du champ -> (nom repère, valeur par défaut mm, min, max, libellé)
FIELDS_CAISSON = [
    ('champHauteur', 'hauteur', 1200, 200, 2780, 'Hauteur'),
    ('champLargeur', 'largeur', 1000, 100, 2780, 'Largeur'),
    ('champProfondeur', 'profondeur', 500, 150, 1500, 'Profondeur'),
    ('champEpPanneau', 'ep_panneau', 19, 8, 40, 'Épaisseur panneaux'),
    ('champEpFond', 'ep_fond', 8, 3, 19, 'Épaisseur fond'),
]


def read_stored_values(comp, fallback):
    attr = comp.attributes.itemByName(ATTR_GROUP, ATTR_PARAMS)
    if attr and attr.value:
        try:
            return json.loads(attr.value)
        except Exception:
            pass
    return fallback


def load_saved_defaults():
    """Charge les valeurs par défaut personnalisées enregistrées via le
    bouton « Enregistrer par défaut » (fichier JSON à côté de l'add-in).
    Renvoie un dict vide si rien n'a encore été enregistré, ou si le fichier
    est introuvable/corrompu (ne doit jamais faire planter l'ouverture de la
    boîte de dialogue)."""
    try:
        with open(DEFAULTS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_defaults_to_disk(values):
    """Enregistre 'values' (dict complet renvoyé par collect_values_mm, donc
    tous les paramètres actuels ET tout paramètre futur qui y sera ajouté)
    comme nouvelles valeurs par défaut pour tout prochain « Nouveau meuble »,
    dans un fichier JSON à côté de l'add-in (persiste entre documents et
    sessions Fusion)."""
    with open(DEFAULTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(values, f, ensure_ascii=False, indent=2)


def default_values_dict():
    """Valeurs par défaut d'un meuble neuf : valeurs de base (mêmes chiffres
    que les fallback utilisés par add_meuble_fields / collect_values_mm),
    surchargées par les valeurs enregistrées via « Enregistrer par défaut »
    si elles existent (un paramètre pas encore enregistré garde sa valeur de
    base, donc les futurs paramètres restent couverts automatiquement)."""
    values = {}
    for _field_id, key, default_mm, _min, _max, _label in FIELDS_CAISSON:
        values[key] = default_mm
    values['socle_actif'] = True
    values['socle'] = 20
    values['retrait_plinthe'] = 5
    values['nb_etageres'] = 0
    values['retrait_etagere'] = 0
    values['etageres_mode'] = 'hauteur_colonne'
    values['etageres_colonnes'] = None
    values['etageres_fixes_colonnes'] = None
    values['etageres_fixe_mode'] = 'hauteur_colonne'
    values['nb_montants'] = 1
    values['montants'] = None
    values['montants_mode'] = 'axe_egal'
    values['percage32_actif'] = True
    values['percage64_actif'] = False
    values['percage32_colonnes'] = None
    values['percage32_retrait'] = 37
    values['percage32_marge_bas'] = 9.5
    values['percage32_masquer_bas'] = 0
    values['percage32_masquer_haut'] = 0
    values['portes_colonnes'] = None
    values['portes_mode'] = 'applique'
    values['charniere_axe_basse'] = 100
    values['charniere_axe_haute'] = 100
    values['charniere_nb_inter'] = 0
    values['charniere_auto'] = True
    values['densite_panneau'] = 680
    values['jeu_porte'] = 2
    values['ep_porte'] = values['ep_panneau']
    values['nb_tiroirs'] = 0
    values['jeu_tiroir'] = 2
    values['ep_face_tiroir'] = values['ep_panneau']
    values.update(load_saved_defaults())
    return values
