# meuble_layout.py -- calculs geometriques purs (aucune dependance a
# l'API Fusion), extrait de MeubleParametrique.py pour etre testable
# independamment (voir aussi meuble_geometry.py et meuble_persistence.py).


class MeubleLayoutError(ValueError):
    # Combinaison de valeurs incompatible (ex. largeur trop faible pour le
    # nombre de montants/portes demande) : leve depuis compute_layout avec
    # un message directement affichable a l'utilisateur (pas de traceback
    # Python), pour remplacer un plantage brut par un message clair.
    pass


# Modes de calcul du placement vertical des étagères (liste déroulante) :
# code interne (stocké dans values['etageres_mode']) -> libellé affiché.
ETAGERES_MODES = [
    ('hauteur_colonne', 'Hauteur de la colonne'),
    ('nb_trous_symetrique', 'Nombre de trous symétrique'),
    ('nb_trous_ref_bas', 'Nombre de trous ref bas'),
    ('nb_trous_ref_haut', 'Nombre de trous ref haut'),
]

# --- Perçages Lamello (assemblage par goujons Ø5mm, façon fichier de
# référence « C1 Montant Inter Portes Étagères », groupe « Perçage Lamello »)
# Un perçage n'entame jamais tout à fait l'épaisseur du panneau (marge de
# sécurité) pour éviter de traverser une pièce fine de part en part.
LAMELLO_DIAM_MM = 5
LAMELLO_DEPTH_MM = 9
LAMELLO_MARGE_SECURITE_MM = 3
LAMELLO_DECALE_MM = 101  # écart entre les 2 perçages de chaque assemblage

# --- Perçage système 32 (taquets d'étagères réglables) ---
PERCAGE32_DIAM_MM = 5
PERCAGE32_DEPTH_MM = 10
PERCAGE32_PITCH_MM = 32

# Cinematique d'ouverture visuelle des portes (case 'Porte ouverte') :
# rotation autour de l'arete exterieure cote charniere, puis 2
# translations (voir hinges/build_door_component). Offset final selon
# le cas -- voir compute_layout, section Portes.
ANGLE_OUVERTURE_PORTE_DEG = 110.0
OFFSET_OUVERTURE_SIMPLE_MM = 12.0
OFFSET_OUVERTURE_JUMELEE_MM = 12.0
OFFSET_OUVERTURE_ENCASTRE_MM = 10.0
# Degagement sous la rainure du fond de tiroir (bas des cotes -> bas de
# la rainure), dimensionne pour des coulisses invisibles sous caisse
# (undermount) -- voir analyse du modele de reference 'Tiroir'.
DEGAGEMENT_COULISSE_CM = 1.5
# Degagement horizontal fixe entre la face interieure du caisson et la
# face exterieure du cote de tiroir (cote coulisse), impose par les
# coulisses invisibles sous caisse -- independant du Jeu peripherique
# facade (qui, lui, ne regle que le recouvrement visuel de la
# facade).
LARGEUR_COULISSE_CM = 2.45
# Cas particuliers du tiroir le plus bas d'une colonne (niche la plus
# basse) et du tiroir le plus haut (niche la plus haute) : reference a
# l'ecart REEL avec le Dessous/Dessus physique du caisson, plutot qu'a
# leur propre facade (qui, elle, sert de reference pour tous les
# tiroirs intermediaires).
DEGAGEMENT_TIROIR_BAS_CM = 2.85
MARGE_HAUT_COTE_CM = 1.0
# Longueurs nominales standard des coulisses Tandem Blum (mm). La
# profondeur utile du caisson (facade a fond de meuble, moins 5mm de
# marge) est arrondie a la plus grande longueur nominale qui y tient,
# puis le caisson est fabrique 10mm plus court que cette longueur
# nominale (convention Blum).
# Longueurs nominales Tandem Blum disponibles PAR CAPACITE de charge :
# 380/420/480/520 ne sont utilisees dans aucune des deux gammes.
TANDEM_BLUM_LONGUEURS_MM_30KG = [250, 270, 300, 350, 400, 450, 500, 550, 600]
TANDEM_BLUM_LONGUEURS_MM_50KG = [450, 550, 650, 700, 750]
# Positions X (mm depuis la face avant du MEUBLE) des percages
# Eurovis/vis agglo sur les cotes, pour chaque longueur nominale de
# coulisse Tandem Blum (voir gabarit de percage Blum). Diametre 5mm,
# profondeur 9.5mm. Les longueurs au-dela de 600 (650/700/750, gamme
# 50kg) reprennent le gabarit de 600 a defaut de cote fournie.
TANDEM_BLUM_PERCAGES_MM = {
    250: [19, 37, 69, 133, 197],
    270: [19, 37, 69, 133, 197],
    300: [19, 37, 69, 165, 261],
    350: [19, 37, 69, 165, 261],
    400: [19, 37, 69, 165, 229],
    450: [19, 37, 69, 165, 261],
    500: [19, 37, 69, 165, 243, 261, 284],
    550: [19, 37, 69, 165, 243, 261, 284],
    600: [19, 37, 69, 165, 243, 261, 325],
    650: [19, 37, 69, 165, 243, 261, 325],
    700: [19, 37, 69, 165, 243, 261, 325],
    750: [19, 37, 69, 165, 243, 261, 325],
}
DIAM_PERCAGE_COULISSE_CM = 0.5
PROFONDEUR_PERCAGE_COULISSE_CM = 0.95
DEGAGEMENT_PERCAGE_COULISSE_CM = 0.95


def choisir_profondeur_caisson_tiroir_mm(profondeur_utile_mm, capacite_kg=30):
    """Renvoie (longueur_coulisse_mm, profondeur_caisson_mm) : la plus
    grande longueur de coulisse Tandem Blum (parmi la gamme de la
    capacite demandee, 30 ou 50 kg) qui tient dans 'profondeur_utile_mm',
    et la profondeur de caisson resultante (10mm de moins que cette
    longueur nominale). Si meme la plus petite coulisse de la gamme ne
    tient pas, la prend quand meme (caisson au minimum standard, quitte
    a deborder legerement -- signale ailleurs par les contraintes de
    profondeur du meuble)."""
    longueurs = (TANDEM_BLUM_LONGUEURS_MM_50KG if capacite_kg == 50
                 else TANDEM_BLUM_LONGUEURS_MM_30KG)
    candidats = [l for l in longueurs if l <= profondeur_utile_mm]
    longueur = max(candidats) if candidats else min(longueurs)
    return longueur, longueur - 10.0


def mm_to_cm(v):
    return v / 10.0


def cm_to_mm(v):
    return v * 10.0


# ---------------------------------------------------------------------------
# Calcul du plan (layout) à partir des valeurs de la boîte de dialogue
# ---------------------------------------------------------------------------

def normalize_percage32_colonne(entry, legacy_actif=True, legacy_64=False,
                                 legacy_masquer_bas=0, legacy_masquer_haut=0):
    """Normalise une entrée de values['percage32_colonnes'] vers le format
    {'systeme': 'off'|'32'|'64', 'masquer_bas': int, 'masquer_haut': int}
    utilisé depuis la réorganisation « un volet par colonne ». Gère aussi
    l'ancien format (une simple valeur bool = colonne active/inactive, le
    système et le masquage étant alors globaux, passés ici en legacy_*), pour
    que les meubles déjà enregistrés continuent à se recharger correctement."""
    if isinstance(entry, dict):
        systeme = entry.get('systeme', '32')
        if systeme not in ('off', '32', '64'):
            systeme = '32'
        return {
            'systeme': systeme,
            'masquer_bas': max(0, int(entry.get('masquer_bas', 0) or 0)),
            'masquer_haut': max(0, int(entry.get('masquer_haut', 0) or 0)),
            'trois_trous': bool(entry.get('trois_trous', False)),
        }
    # Ancien format : bool (colonne active/inactive au sein d'un système 32/64
    # unique et global) ; le masquage était lui aussi un réglage global.
    colonne_active = bool(entry) if entry is not None else True
    if not colonne_active or not legacy_actif:
        systeme = 'off'
    else:
        systeme = '64' if legacy_64 else '32'
    return {
        'systeme': systeme,
        'masquer_bas': max(0, int(legacy_masquer_bas or 0)),
        'masquer_haut': max(0, int(legacy_masquer_haut or 0)),
    }


def normalized_percage32_colonnes(values, count):
    """Construit la liste normalisée (longueur 'count') des colonnes Perçage
    32 à partir de values['percage32_colonnes'] (nouveau ou ancien format),
    pour alimenter aussi bien compute_layout que la reconstruction de
    l'interface (rebuild_percage32_colonne_groups)."""
    legacy_actif = values.get('percage32_actif', True)
    legacy_64 = values.get('percage64_actif', False)
    legacy_masquer_bas = values.get('percage32_masquer_bas', 0)
    legacy_masquer_haut = values.get('percage32_masquer_haut', 0)
    brutes = values.get('percage32_colonnes') or []
    out = []
    for i in range(count):
        entry = brutes[i] if i < len(brutes) else None
        out.append(normalize_percage32_colonne(
            entry, legacy_actif, legacy_64, legacy_masquer_bas, legacy_masquer_haut))
    return out


def normalized_percage32_niches(values, col_idx, nb_niches):
    # Pour la colonne col_idx, renvoie la liste normalisee (longueur
    # nb_niches) des reglages Percage 32 PAR NICHE (compartiment vertical
    # delimite par les etageres fixe de cette colonne).
    # values['percage32_colonnes'][col_idx] peut etre soit une liste
    # d'entrees par niche (nouveau format), soit une seule entree
    # (ancien format anterieur aux niches) qui s'applique alors a
    # l'identique sur toutes les niches, pour rester compatible avec les
    # meubles deja crees.
    legacy_actif = values.get('percage32_actif', True)
    legacy_64 = values.get('percage64_actif', False)
    legacy_masquer_bas = values.get('percage32_masquer_bas', 0)
    legacy_masquer_haut = values.get('percage32_masquer_haut', 0)
    colonnes = values.get('percage32_colonnes') or []
    entry_col = colonnes[col_idx] if col_idx < len(colonnes) else None
    if isinstance(entry_col, list):
        niches_brutes = entry_col
    elif entry_col is None:
        niches_brutes = []
    else:
        niches_brutes = [entry_col] * nb_niches
    # L'interface affiche Niche 01 = la plus HAUTE (numeros croissants en
    # descendant), mais le calcul interne (compute_layout) raisonne en Z
    # croissant (indice 0 = la plus BASSE) : on inverse donc ici.
    niches_brutes = list(reversed(niches_brutes))
    out = []
    for k in range(nb_niches):
        entry = niches_brutes[k] if k < len(niches_brutes) else None
        out.append(normalize_percage32_colonne(
            entry, legacy_actif, legacy_64, legacy_masquer_bas, legacy_masquer_haut))
    return out


def normalize_etagere_colonne(entry, legacy_nb=0, legacy_mode='hauteur_colonne'):
    """Normalise une entrée de values['etageres_colonnes'] vers le format
    {'nb_etageres': int, 'mode': code}, utilisé depuis la réorganisation « un
    volet Étagères par colonne ». Gère aussi l'ancien format (pas d'entrée par
    colonne : Nombre d'étagères et Mode de calcul étaient globaux, passés ici
    en legacy_nb/legacy_mode), pour que les meubles déjà enregistrés se
    rechargent correctement."""
    mode_codes = dict(ETAGERES_MODES)
    if isinstance(entry, dict):
        nb = max(0, int(entry.get('nb_etageres', 0) or 0))
        mode = entry.get('mode', 'hauteur_colonne')
        if mode not in mode_codes:
            mode = 'hauteur_colonne'
        return {'nb_etageres': nb, 'mode': mode}
    mode = legacy_mode if legacy_mode in mode_codes else 'hauteur_colonne'
    return {'nb_etageres': max(0, int(legacy_nb or 0)), 'mode': mode}


def normalized_etageres_colonnes(values, count):
    """Construit la liste normalisée (longueur 'count') des colonnes Étagères
    à partir de values['etageres_colonnes'] (nouveau ou ancien format), pour
    alimenter aussi bien compute_layout que la reconstruction de l'interface
    (rebuild_etageres_colonne_groups)."""
    legacy_nb = values.get('nb_etageres', 0)
    legacy_mode = values.get('etageres_mode', 'hauteur_colonne')
    brutes = values.get('etageres_colonnes') or []
    out = []
    for i in range(count):
        entry = brutes[i] if i < len(brutes) else None
        out.append(normalize_etagere_colonne(entry, legacy_nb, legacy_mode))
    return out


def normalized_etageres_niches(values, col_idx, nb_niches):
    # Meme principe que normalized_percage32_niches, pour le nombre
    # d'etageres mobiles et le mode de calcul PAR NICHE.
    legacy_nb = values.get('nb_etageres', 0)
    legacy_mode = values.get('etageres_mode', 'hauteur_colonne')
    colonnes = values.get('etageres_colonnes') or []
    entry_col = colonnes[col_idx] if col_idx < len(colonnes) else None
    if isinstance(entry_col, list):
        niches_brutes = entry_col
    elif entry_col is None:
        niches_brutes = []
    else:
        niches_brutes = [entry_col] * nb_niches
    # Voir normalized_percage32_niches : Niche 01 (interface) = la plus
    # HAUTE, indice 0 (interne) = la plus BASSE -> inversion.
    niches_brutes = list(reversed(niches_brutes))
    out = []
    for k in range(nb_niches):
        entry = niches_brutes[k] if k < len(niches_brutes) else None
        out.append(normalize_etagere_colonne(entry, legacy_nb, legacy_mode))
    return out


TIROIRS_MODES = [
    ('hauteur_niche', 'Hauteur égale'),
    ('personnalise', 'Personnalisé'),
]


def normalize_tiroir_colonne(entry):
    """Normalise une entree de values['tiroirs_colonnes'] vers le
    format {'nb_tiroirs': int, 'mode': code}, meme principe que
    normalize_etagere_colonne (pas d'ancien format global a migrer,
    parametre cree directement par colonne/niche)."""
    mode_codes = dict(TIROIRS_MODES)
    if isinstance(entry, dict):
        nb = max(0, int(entry.get('nb_tiroirs', 0) or 0))
        mode = entry.get('mode', 'hauteur_niche')
        if mode not in mode_codes:
            mode = 'hauteur_niche'
        tiroirs_brutes = entry.get('tiroirs') or []
        tiroirs = []
        for k in range(nb):
            t = tiroirs_brutes[k] if k < len(tiroirs_brutes) else {}
            if not isinstance(t, dict):
                t = {}
            ref = t.get('ref', 'haut')
            if ref not in ('haut', 'bas'):
                ref = 'haut'
            try:
                hauteur_mm = float(t.get('hauteur_mm', 0) or 0)
            except (TypeError, ValueError):
                hauteur_mm = 0.0
            capacite_kg = t.get('capacite_kg', 30)
            if capacite_kg not in (30, 50):
                capacite_kg = 30
            tiroirs.append({
                'hauteur_mm': max(0.0, hauteur_mm), 'ref': ref, 'capacite_kg': capacite_kg,
            })
        return {'nb_tiroirs': nb, 'mode': mode, 'tiroirs': tiroirs}
    return {'nb_tiroirs': 0, 'mode': 'hauteur_niche', 'tiroirs': []}


def normalized_tiroirs_niches(values, col_idx, nb_niches):
    # Meme principe que normalized_etageres_niches, pour le nombre de
    # tiroirs et le mode de calcul PAR NICHE.
    colonnes = values.get('tiroirs_colonnes') or []
    entry_col = colonnes[col_idx] if col_idx < len(colonnes) else None
    if isinstance(entry_col, list):
        niches_brutes = entry_col
    elif entry_col is None:
        niches_brutes = []
    else:
        niches_brutes = [entry_col] * nb_niches
    # Niche 01 (interface) = la plus HAUTE, indice 0 (interne) = la
    # plus BASSE -> inversion (voir normalized_percage32_niches).
    niches_brutes = list(reversed(niches_brutes))
    out = []
    for k in range(nb_niches):
        entry = niches_brutes[k] if k < len(niches_brutes) else None
        out.append(normalize_tiroir_colonne(entry))
    return out


def normalized_prise_main_portes_niches(values, col_idx, nb_niches):
    # Meme principe d'inversion que normalized_tiroirs_niches : Niche
    # 01 (interface) = la plus HAUTE, indice 0 (interne) = la plus
    # BASSE.
    colonnes = values.get('prise_main_portes') or []
    niches_brutes = colonnes[col_idx] if col_idx < len(colonnes) else []
    if not isinstance(niches_brutes, list):
        niches_brutes = []
    niches_brutes = list(reversed(niches_brutes))
    out = []
    for k in range(nb_niches):
        code = niches_brutes[k] if k < len(niches_brutes) else 'sans'
        if code not in ('sans', 'oppose', 'haut', 'bas'):
            code = 'sans'
        out.append(code)
    return out


def normalized_prise_main_tiroirs_niches(values, col_idx, nb_niches):
    # Meme principe d'inversion ; chaque niche porte une LISTE de
    # codes (un par tiroir, dans l'ordre d'empilement du tableau).
    colonnes = values.get('prise_main_tiroirs') or []
    niches_brutes = colonnes[col_idx] if col_idx < len(colonnes) else []
    if not isinstance(niches_brutes, list):
        niches_brutes = []
    niches_brutes = list(reversed(niches_brutes))
    out = []
    for k in range(nb_niches):
        entry = niches_brutes[k] if k < len(niches_brutes) else []
        if not isinstance(entry, list):
            entry = []
        out.append(entry)
    return out


def normalize_etagere_fixe_colonne(entry):
    """Normalise une entrée de values['etageres_fixes_colonnes'] vers le
    format {'nb_etageres': int, 'hauteurs': [mm, ...]} (une hauteur par
    étagère fixe de cette colonne, mesurée depuis le bas du Dessous jusqu'à
    l'axe de l'étagère). Aucun ancien format à migrer (paramètre créé
    directement sous cette forme)."""
    if isinstance(entry, dict):
        nb = max(0, int(entry.get('nb_etageres', 0) or 0))
        hauteurs_brutes = entry.get('hauteurs') or []
        hauteurs = []
        for k in range(nb):
            try:
                hauteurs.append(float(hauteurs_brutes[k]) if k < len(hauteurs_brutes) else 0.0)
            except (TypeError, ValueError):
                hauteurs.append(0.0)
        return {'nb_etageres': nb, 'hauteurs': hauteurs}
    return {'nb_etageres': 0, 'hauteurs': []}


def normalized_etageres_fixes_colonnes(values, count):
    """Construit la liste normalisée (longueur 'count') des colonnes Étagères
    fixe à partir de values['etageres_fixes_colonnes'], pour alimenter aussi
    bien compute_layout que la reconstruction de l'interface
    (rebuild_etageres_fixe_colonne_groups)."""
    brutes = values.get('etageres_fixes_colonnes') or []
    out = []
    for i in range(count):
        entry = brutes[i] if i < len(brutes) else None
        out.append(normalize_etagere_fixe_colonne(entry))
    return out


PORTES_CHOIX = ('off', 'gauche', 'droite', '2portes')


def normalize_porte_colonne(entry):
    # Normalise une entree de values['portes_colonnes'] vers le format
    # {'choix': code}, code parmi PORTES_CHOIX. Aucun ancien format a migrer
    # (nb_portes/sens_ouverture globaux precedents n'etaient pas relies a la
    # geometrie, une colonne neuve part donc simplement sur Off).
    if isinstance(entry, dict):
        choix = entry.get('choix', 'off')
        if choix not in PORTES_CHOIX:
            choix = 'off'
        return {'choix': choix, 'ouverte': bool(entry.get('ouverte', False))}
    return {'choix': 'off', 'ouverte': False}


def normalized_portes_colonnes(values, count):
    # Construit la liste normalisee (longueur 'count') des colonnes Portes a
    # partir de values['portes_colonnes'], pour alimenter aussi bien
    # compute_layout que la reconstruction de l'interface
    # (rebuild_portes_colonne_groups).
    brutes = values.get('portes_colonnes') or []
    out = []
    for i in range(count):
        entry = brutes[i] if i < len(brutes) else None
        out.append(normalize_porte_colonne(entry))
    return out


def normalized_portes_niches(values, col_idx, nb_niches):
    # Meme principe, pour le choix de porte (Off/Gauche/Droite/2 Portes)
    # PAR NICHE.
    colonnes = values.get('portes_colonnes') or []
    entry_col = colonnes[col_idx] if col_idx < len(colonnes) else None
    if isinstance(entry_col, list):
        niches_brutes = entry_col
    elif entry_col is None:
        niches_brutes = []
    else:
        niches_brutes = [entry_col] * nb_niches
    # Voir normalized_percage32_niches : Niche 01 (interface) = la plus
    # HAUTE, indice 0 (interne) = la plus BASSE -> inversion.
    niches_brutes = list(reversed(niches_brutes))
    out = []
    for k in range(nb_niches):
        entry = niches_brutes[k] if k < len(niches_brutes) else None
        out.append(normalize_porte_colonne(entry))
    return out


def _split_by_axes(low, high, axes, gap):
    # Decoupe l'intervalle [low, high] en tronçons separes par les positions
    # de 'axes' (ex. axes des etageres fixe d'une colonne), en appliquant
    # 'gap' de part et d'autre de CHAQUE coupure interne (low et high ne sont
    # pas eux-memes recules : l'appelant les a deja positionnes correctement).
    # Utilise pour scinder une porte de colonne au droit de chaque etagere
    # fixe qu'elle recouvrirait sinon.
    pts = sorted(a for a in axes if low < a < high)
    bounds = [low] + pts + [high]
    segments = []
    n = len(bounds) - 1
    for i in range(n):
        a, b = bounds[i], bounds[i + 1]
        lo = a + (gap if i > 0 else 0.0)
        hi = b - (gap if i < n - 1 else 0.0)
        if hi > lo:
            segments.append((lo, hi))
    return segments


def _v_slices_facade_applique(z0_zone, z1_zone, axes_ef, actifs_niche, jeu, Ep):
    # Variante de _split_by_axes pour les facades (portes ET tiroirs) en
    # mode 'applique' : le jeu a chaque coupure (etagere fixe) est
    # partage moitie-moitie SI les 2 niches voisines ont chacune une
    # facade active (porte OU tiroir, voir actifs_niche_par_colonne),
    # mais si l'une des 2 niches n'a RIEN, la facade active de l'autre
    # recouvre l'etagere fixe en entier, en s'arretant 'jeu' au-dela de
    # sa face opposee -- meme principe que pour les montants. Utilisee
    # a la fois par la boucle Portes et la boucle Tiroirs, avec le meme
    # actifs_niche (evite tout chevauchement entre porte et tiroir de
    # part et d'autre d'une meme etagere fixe). Ne s'applique pas au
    # mode 'encastre', ou la facade est dans la meme profondeur que
    # l'etagere fixe et doit TOUJOURS degager sa demi-epaisseur, quel
    # que soit l'etat de la niche voisine, sous peine de chevauchement
    # solide.
    n = len(axes_ef) + 1
    result = []
    for j in range(n):
        if j == 0:
            lo = z0_zone
        else:
            axis = axes_ef[j - 1]
            voisin_off = (not actifs_niche[j - 1]
                          if j - 1 < len(actifs_niche) else True)
            lo = (axis - Ep / 2.0) + jeu if voisin_off else axis + jeu / 2.0
        if j == n - 1:
            hi = z1_zone
        else:
            axis = axes_ef[j]
            voisin_off = (not actifs_niche[j + 1]
                          if j + 1 < len(actifs_niche) else True)
            hi = (axis + Ep / 2.0) - jeu if voisin_off else axis - jeu / 2.0
        if hi > lo:
            result.append((lo, hi))
        else:
            result.append(None)
    return result


def nb_charnieres_auto(largeur_porte_cm, hauteur_porte_cm, ep_porte_cm, densite_kg_m3):
    # Nombre de charnieres selon la documentation Blum : depend du poids
    # de la face, estime via son volume et la densite du panneau.
    # Paliers Blum : jusqu'a 6kg = 2 charnieres, 6 a 12kg = 3, 12 a 17kg
    # = 4, 17 a 22kg = 5. Au-dela de 22kg (hors plage documentee par
    # Blum), on double la charniere du haut plutot que d'extrapoler un
    # nombre standard.
    # Renvoie (nb_charnieres_standard, doubler_la_charniere_du_haut).
    volume_m3 = (largeur_porte_cm / 100.0) * (hauteur_porte_cm / 100.0) * (ep_porte_cm / 100.0)
    masse_kg = volume_m3 * densite_kg_m3
    if masse_kg <= 6.0:
        return 2, False
    if masse_kg <= 12.0:
        return 3, False
    if masse_kg <= 17.0:
        return 4, False
    if masse_kg <= 22.0:
        return 5, False
    return 5, True


def hinge_positions_locales_mm(hauteur_door_cm, vz0_cm, axe_basse_mm, axe_haute_mm,
                                nb_inter, grille_col_cm, etageres_occupees_cm=None,
                                doubler_haut=False, snap_to_grid=True):
    # Positions Y (mm, depuis le bas de CETTE porte) des charnieres :
    # basse + haute (par defaut axe_basse_mm / axe_haute_mm depuis chaque
    # bord), plus nb_inter charnieres intermediaires reparties a espace
    # egal entre les 2 -- meme convention que l'add-in Porte Inserta
    # Blum. Repli automatique si la porte est trop courte pour ces axes
    # par defaut (niche etroite creee par une etagere fixe).
    #
    # Chaque axe de charniere (percage 35mm) est ensuite ancre EXACTEMENT
    # a mi-chemin entre les 2 trous systeme 32 adjacents de
    # 'grille_col_cm' (la grille COMPLETE de la colonne, independante du
    # systeme 32/64/off/masquage choisi pour cette niche). L'entraxe des
    # chevilles Inserta (percage 8mm, voir CHARNIERE_ENTREAXE_INSERTA_MM)
    # est une cote materiel fixe (45mm), independante de ce calage. Les 2
    # trous encadrant chaque axe sont aussi renvoyes (trous_requis_cm) :
    # l'appelant doit s'assurer qu'ils sont bien perces cote caisson,
    # meme s'ils avaient ete masques ou exclus par le systeme 32/64/off
    # de cette niche.
    #
    # 'etageres_occupees_cm' : z_start (cm, absolu) des etageres mobiles
    # de cette niche. Une charniere INTERMEDIAIRE (jamais la basse/haute,
    # positionnees par l'utilisateur) qui tomberait en face d'une
    # etagere est deplacee vers le trou libre le plus proche.
    #
    # 'doubler_haut' : porte de plus de 22kg (hors plage documentee par
    # Blum) -- au lieu d'extrapoler un nombre standard de charnieres
    # reparties, on ajoute une charniere tout pres de celle du haut
    # (renfort du point le plus sollicite), plutot qu'une repartition
    # uniforme.
    hauteur_mm = cm_to_mm(hauteur_door_cm)
    axe_basse, axe_haute = axe_basse_mm, axe_haute_mm
    if axe_basse + axe_haute >= hauteur_mm:
        axe_basse = hauteur_mm * 0.25
        axe_haute = hauteur_mm * 0.25
    positions_mm = [axe_basse, hauteur_mm - axe_haute]
    nb_fixes = len(positions_mm)
    if nb_inter > 0:
        span = (hauteur_mm - axe_haute) - axe_basse
        step = span / (nb_inter + 1)
        for i in range(1, nb_inter + 1):
            positions_mm.append(axe_basse + step * i)
    if doubler_haut:
        # ~3 pas de systeme 32 (32mm) en dessous de la charniere du haut :
        # proche pour renforcer ce point, mais distincte.
        offset_doublage_mm = 64.0
        positions_mm.append(max(axe_basse, (hauteur_mm - axe_haute) - offset_doublage_mm))
    # Marge de securite : ne jamais placer une charniere si pres du bord
    # que les chevilles Inserta (entraxe 45mm, +/-22.5mm de l'axe)
    # sortiraient du panneau -- sous peine d'echec du percage (aucun
    # corps a couper). 22.5mm de demi-entraxe + 2mm de tolerance.
    marge_bord_mm = 24.5
    grille_triee = sorted(grille_col_cm)
    etageres_occ = etageres_occupees_cm or []

    def _trou_occupe(g_cm):
        # Un trou systeme 32 deja utilise comme appui d'etagere mobile
        # (z_start d'une etagere coïncide avec ce trou) ne doit jamais
        # etre repris pour une charniere : un trou ne peut recevoir qu'un
        # seul taquet/vis a la fois.
        return any(abs(g_cm - z_start) < 0.05 for z_start in etageres_occ)

    pairs_utilisees = set()

    def _paire_en_conflit(g_bas, g_haut):
        # Un trou deja utilise par une etagere mobile, OU une paire deja
        # prise par une AUTRE charniere de cette meme porte (evite
        # qu'une charniere de doublage, par exemple, ne se superpose
        # exactement a la charniere du haut ou a une intermediaire).
        return (_trou_occupe(g_bas) or _trou_occupe(g_haut)
                or (g_bas, g_haut) in pairs_utilisees)

    def _milieu(g_bas, g_haut):
        return (g_bas + g_haut) / 2.0

    hinges_mm = []
    trous_requis_cm = []
    if not snap_to_grid:
        # Percage 32 Off pour cette niche : pas de grille a caler,
        # positions EXACTES (axe_basse/axe_haute + repartition egale
        # des intermediaires), aucun trou systeme 32 associe.
        return positions_mm, []
    for idx, p_mm in enumerate(positions_mm):
        abs_cm = vz0_cm + mm_to_cm(p_mm)
        en_dessous = [g for g in grille_triee if g <= abs_cm]
        au_dessus = [g for g in grille_triee if g > abs_cm]
        if en_dessous and au_dessus:
            i_bas = len(en_dessous) - 1
            g_bas, g_haut = grille_triee[i_bas], grille_triee[i_bas + 1]
            axis_cm = _milieu(g_bas, g_haut)
            # Recherche de la paire de trous libres la plus proche pour
            # les charnieres intermediaires (dont le doublage)
            # uniquement (idx >= nb_fixes) : essaie alternativement le
            # decalage suivant vers le haut puis vers le bas jusqu'a
            # trouver 2 trous adjacents libres (ni etagere, ni deja
            # utilises par une autre charniere de cette porte).
            if idx >= nb_fixes and _paire_en_conflit(g_bas, g_haut):
                trouve = False
                for decalage in range(1, len(grille_triee)):
                    for sens_rech in (1, -1):
                        j = i_bas + sens_rech * decalage
                        if j < 0 or j + 1 >= len(grille_triee):
                            continue
                        cand_g_bas, cand_g_haut = grille_triee[j], grille_triee[j + 1]
                        cand_axis = _milieu(cand_g_bas, cand_g_haut)
                        cand_mm = cm_to_mm(cand_axis - vz0_cm)
                        if not (marge_bord_mm <= cand_mm <= hauteur_mm - marge_bord_mm):
                            continue
                        if not _paire_en_conflit(cand_g_bas, cand_g_haut):
                            g_bas, g_haut = cand_g_bas, cand_g_haut
                            axis_cm = cand_axis
                            trouve = True
                            break
                    if trouve:
                        break
                # Si aucune paire libre n'est trouvee (cas extreme), on
                # garde la position d'origine plutot que d'echouer.
            pairs_utilisees.add((g_bas, g_haut))
            trous_requis_cm.append(g_bas)
            trous_requis_cm.append(g_haut)
        else:
            # Pas de grille disponible (systeme 32 jamais actif sur cette
            # colonne) : position par defaut non ancree.
            axis_cm = abs_cm
        axis_mm = cm_to_mm(axis_cm - vz0_cm)
        axis_mm = max(marge_bord_mm, min(hauteur_mm - marge_bord_mm, axis_mm))
        hinges_mm.append(axis_mm)
    return sorted(hinges_mm), trous_requis_cm


def niche_bounds_colonne(col_ef, interior_z0, interior_z1, Ep):
    # Decoupe la hauteur utile d'UNE colonne (interior_z0..interior_z1)
    # en niches (compartiments verticaux) delimitees par ses etageres
    # fixe (col_ef['hauteurs'], en mm) : chaque niche degage la
    # demi-epaisseur (Ep/2) de chaque etagere fixe qui la borde. Sans
    # etagere fixe, une seule niche couvre toute la colonne (comportement
    # identique a avant l'introduction des niches).
    axes = sorted(mm_to_cm(h) for h in col_ef.get('hauteurs', []))
    return _split_by_axes(interior_z0, interior_z1, axes, Ep / 2.0)


def compute_etagere_z_starts(nb_etageres, mode, interior_z0, interior_h, Ep, col_candidates):
    """Calcule les z_start (bas de panneau) de chaque étagère d'UNE colonne,
    selon le mode de calcul choisi (values['etageres_mode'], voir
    ETAGERES_MODES) et la grille de trous de perçage système 32 propre à
    cette colonne (col_candidates, déjà filtrée par système/masquage).
    Retombe sur le mode 'hauteur_colonne' si cette colonne n'a aucun trou
    disponible (système Off ou grille vide)."""
    n_trous = len(col_candidates)
    if mode != 'hauteur_colonne' and n_trous > 0:
        pas_trous_sym = n_trous / float(nb_etageres + 1)
        pas_trous_ref = n_trous / float(nb_etageres)
        z_starts = []
        for i in range(1, nb_etageres + 1):
            if mode == 'nb_trous_ref_bas':
                idx = round((i - 1) * pas_trous_ref)
            elif mode == 'nb_trous_ref_haut':
                idx = (n_trous - 1) - round((nb_etageres - i) * pas_trous_ref)
            else:  # 'nb_trous_symetrique' (et repli par défaut)
                idx = round(i * pas_trous_sym) - 1
            idx = max(0, min(n_trous - 1, int(idx)))
            z_starts.append(col_candidates[idx])
        return z_starts
    pas = interior_h / (nb_etageres + 1)
    z_starts = []
    for i in range(1, nb_etageres + 1):
        z_centre = interior_z0 + pas * i
        z_start_defaut = z_centre - Ep / 2.0
        if col_candidates:
            z_start = min(col_candidates, key=lambda z: abs(z - z_start_defaut))
        else:
            z_start = z_start_defaut
        z_starts.append(z_start)
    return z_starts


def compute_layout(values):
    """values : dict en mm/entiers. Renvoie un dict de listes de panneaux
    (en cm) prêts à être construits, plus la liste des portes à créer."""
    L = mm_to_cm(values['largeur'])
    H = mm_to_cm(values['hauteur'])
    P = mm_to_cm(values['profondeur'])
    Ep = mm_to_cm(values['ep_panneau'])
    Ef = mm_to_cm(values['ep_fond'])
    Soc = mm_to_cm(values.get('socle', 0)) if values.get('socle_actif', True) else 0.0
    # La hauteur de socle ne doit pas augmenter la hauteur totale du meuble :
    # le socle mord sur la hauteur demandée (H) plutôt que de s'y ajouter. En
    # réduisant H ici, tout ce qui suit (caisson, portes, perçages Lamello...)
    # utilise directement la hauteur de caisson réduite ; les côtés, plus bas,
    # redeviennent bien de hauteur H_originale = H_ici + Soc, socle compris.
    H = H - Soc

    interior_depth = P - Ef       # profondeur utile (devant le fond)
    if interior_depth <= 0:
        raise MeubleLayoutError(
            'Profondeur utile insuffisante : reduire l\'epaisseur du fond, ou '
            'augmenter la Profondeur.')
    interior_z0 = Ep              # dessus du panneau du bas
    interior_z1 = H - Ep          # dessous du panneau du haut
    interior_h = interior_z1 - interior_z0
    if interior_h <= 0:
        raise MeubleLayoutError(
            'Hauteur utile insuffisante : reduire l\'epaisseur des panneaux ou '
            'le socle, ou augmenter la Hauteur.')

    panels = []  # (x0,x1,y0,y1,z_start,z_extent,name)
    grooves = []  # ('x', x_plane, sign, y0, y1, z0, z1, depth, name, target_panel_name)
    prises_main = []  # (axis, ...params..., name, target_panel_name)
    holes = []  # ('X', x_plane, sign, y_center, z_center, diam, depth, name) ou ('Z', ...)

    # Les côtés (montants extérieurs) descendent de Soc sous le niveau z=0 du
    # caisson ; le dessous/dessus/fond restent inchangés.
    panels.append((0, Ep, 0, interior_depth, -Soc, H + Soc, 'Côté gauche', None))
    panels.append((L - Ep, L, 0, interior_depth, -Soc, H + Soc, 'Côté droit', None))
    panels.append((Ep, L - Ep, 0, interior_depth, 0, Ep, 'Dessous', 'EpPanneau'))
    panels.append((Ep, L - Ep, 0, interior_depth, H - Ep, Ep, 'Dessus', 'EpPanneau'))
    panels.append((0, L, interior_depth, P, 0, H, 'Fond', None))

    # --- Plinthe : panneau vertical qui passe sous le Dessous, entre les
    # montants droite/gauche, en épaisseur panneaux, reculé de la face avant
    # du Dessous (y=0) par le retrait de plinthe. N'existe que s'il y a un
    # socle (sinon il n'y a pas d'espace sous le caisson pour la loger).
    if Soc > 1e-6:
        retrait_plinthe = mm_to_cm(values.get('retrait_plinthe', 5))
        panels.append(('XZ', Ep, L - Ep, -Soc, 0, retrait_plinthe, Ep, 'Plinthe', 'EpPanneau'))

    # --- Montants intermédiaires (renforts verticaux entre dessus et dessous,
    # même profondeur que les côtés). Par défaut, chaque axe de montant est
    # positionné par division égale : Largeur meuble ÷ (nombre de montants +
    # 1), le montant i étant au i-ème pas depuis l'extérieur gauche. Chaque
    # montant peut être personnalisé individuellement via values['montants']
    # (liste de {'axe': mm, 'ref': 'gauche'|'droite'}), la distance étant
    # mesurée depuis l'extérieur du côté choisi comme référence. Calculés
    # avant les étagères pour pouvoir découper celles-ci de part et d'autre.
    nb_montants = values.get('nb_montants', 0)
    montant_centres = []  # centres (cm), triés ensuite pour découper les étagères
    if nb_montants > 0:
        montants_specs = values.get('montants', [])
        pas_defaut = values['largeur'] / (nb_montants + 1)
        for i in range(1, nb_montants + 1):
            if i <= len(montants_specs):
                m = montants_specs[i - 1]
                axe_mm = m.get('axe', pas_defaut * i)
                ref = m.get('ref', 'gauche')
            else:
                axe_mm = pas_defaut * i
                ref = 'gauche'
            axe_cm = mm_to_cm(axe_mm)
            centre = axe_cm if ref != 'droite' else (L - axe_cm)
            montant_centres.append(centre)
            panels.append((centre - Ep / 2.0, centre + Ep / 2.0, 0, interior_depth,
                            interior_z0, interior_h, 'Montant intermédiaire {}'.format(i), None))
    montant_centres.sort()

    # Compartiments d'étagère (bornes gauche/droite de chaque bac délimité par
    # les côtés et les montants intermédiaires) : utilisés à la fois pour
    # découper les panneaux d'étagère et pour positionner le perçage système
    # 32, indépendamment du nombre d'étagères réellement posées.
    seg_starts = [Ep] + [c + Ep / 2.0 for c in montant_centres]
    seg_ends = [c - Ep / 2.0 for c in montant_centres] + [L - Ep]
    segments = [(s, e) for s, e in zip(seg_starts, seg_ends) if e > s]
    if not segments:
        raise MeubleLayoutError(
            'Largeur insuffisante pour le nombre de montants demande : '
            'augmenter la Largeur ou reduire le Nombre de montants.')

    # --- Étagères fixe : panneaux fixes (assemblés aux montants par goujons,
    # voir plus bas dans cette fonction où sont générés leurs perçages), une
    # configuration PAR COLONNE (même correspondance colonne <-> compartiment
    # que Perçage 32 / Étagères mobile) via values['etageres_fixes_colonnes'].
    # Contrairement aux étagères mobiles, ces étagères n'ont pas de retrait
    # (profondeur pleine) et ne dépendent pas du perçage système 32 : leur
    # position est réglée directement par une Hauteur (mm), mesurée depuis le
    # bas du Dessous (z=0 dans ce repère, avant le décalage socle final
    # appliqué uniformément à tout en fin de fonction) jusqu'à l'AXE (centre)
    # de l'étagère.
    etageres_fixes_colonnes_norm = normalized_etageres_fixes_colonnes(values, len(segments))
    for bay_i, (seg_x0, seg_x1) in enumerate(segments):
        col_ef = (etageres_fixes_colonnes_norm[bay_i] if bay_i < len(etageres_fixes_colonnes_norm)
                  else {'nb_etageres': 0, 'hauteurs': []})
        for k, hauteur_mm in enumerate(col_ef['hauteurs'], start=1):
            z_centre_ef = mm_to_cm(hauteur_mm)
            j = bay_i + 1
            name_ef = ('Étagère fixe {}'.format(k) if len(segments) == 1
                       else 'Étagère fixe {} section {}'.format(k, j))
            panels.append((seg_x0, seg_x1, 0, interior_depth, z_centre_ef - Ep / 2.0, Ep, name_ef, 'EpPanneau'))

    # --- Perçage système 32 : configuration PAR COLONNE (compartiment
    # d'étagère), une entrée de plus que le nombre de montants intermédiaires,
    # dans l'ordre gauche->droite. Chaque colonne a son propre système
    # (Off/32/64) et son propre masquage (nombre de perçages depuis le bas et
    # depuis le haut) ; le retrait façade et la référence (marge basse)
    # restent communs à toutes les colonnes. Les grilles de trous candidats de
    # chaque colonne sont calculées une seule fois ici pour être partagées
    # entre le positionnement automatique des étagères (ci-dessous) et la
    # génération des perçages eux-mêmes (plus bas dans cette fonction).
    # Chaque colonne est decoupee en NICHES (compartiments verticaux) par
    # ses etageres fixe : Percage 32, Etageres mobile et Portes sont tous
    # desormais regles INDEPENDAMMENT par niche (pas seulement par
    # colonne). Sans etagere fixe, une colonne n'a qu'une seule niche
    # (comportement identique a avant l'introduction des niches).
    marge_bas_p32 = mm_to_cm(values.get('percage32_marge_bas', 9.5))
    # Le dernier trou depuis le haut n'est plus paramétrable : la grille de
    # perçage remonte jusqu'au plus proche multiple de l'entraxe en dessous
    # du haut utile de CHAQUE niche, sans marge de securite additionnelle.
    marge_haut_p32 = 0.0

    def _grille_percage32_colonne(z0_col, z1_col):
        # Grille CONTINUE de trous candidats systeme 32 (entraxe 32mm),
        # ancree au bas utile de la COLONNE ENTIERE (pas de chaque
        # niche) : ajouter ou retirer une etagere fixe decoupe cette
        # grille en niches mais ne doit JAMAIS decaler la phase des trous
        # deja existants -- comme un vrai systeme 32, dont la grille
        # court sur toute la hauteur du meuble independamment des
        # etageres fixe posees dessus.
        if z1_col <= z0_col:
            return []
        pitch32 = mm_to_cm(PERCAGE32_PITCH_MM)
        n_trous = int((z1_col - z0_col) / pitch32) + 1
        return [z0_col + idx * pitch32 for idx in range(n_trous)]

    def _candidats_percage32_niche(grille_32, systeme, masquer_bas, masquer_haut, niche_z0, niche_z1):
        if systeme == 'off':
            return []
        if systeme == '64':
            # Un trou sur deux de la grille systeme 32, phase ancree sur
            # les indices de la grille GLOBALE de la colonne (pas sur le
            # sous-ensemble propre a cette niche), pour rester coherent
            # si on rebascule cette niche en 32 plus tard.
            selection = [z for idx, z in enumerate(grille_32) if idx % 2 == 0]
        else:
            selection = grille_32
        candidats = [z for z in selection if niche_z0 <= z <= niche_z1]
        # Masquage d'un nombre fixe de percages depuis le bas/le haut de la
        # portion de grille VISIBLE dans cette niche : les trous masques
        # sont retires avant tout usage, donc ni perces, ni utilises comme
        # reference pour les etageres.
        if masquer_bas > 0 or masquer_haut > 0:
            fin = len(candidats) - masquer_haut
            candidats = candidats[masquer_bas:fin] if fin > masquer_bas else []
        return candidats

    niches_bounds_par_colonne = []
    percage32_niches_par_colonne = []
    p32_niches_candidates = []
    p32_colonnes_candidates = []
    grilles_p32_par_colonne = []
    # Positions ajoutees UNIQUEMENT pour une charniere (voir section
    # Portes plus bas) : percees seulement cote charniere (avant) ET
    # seulement sur la face du montant/cote ou se trouve REELLEMENT la
    # charniere (pas les deux faces comme les trous systeme 32
    # habituels). {z_cm_arrondi: set des cotes} par colonne -- un SET
    # (pas un seul cote) car en mode 2 portes, les 2 vantaux partagent
    # les memes Z de charniere mais des cotes opposes ; un dict a
    # valeur simple ferait que le 2e ecrase le 1er.
    p32_extra_side_only = [dict() for _ in range(len(segments))]
    for _bay_i in range(len(segments)):
        _col_ef = (etageres_fixes_colonnes_norm[_bay_i]
                   if _bay_i < len(etageres_fixes_colonnes_norm) else {'hauteurs': []})
        _nbounds = niche_bounds_colonne(_col_ef, interior_z0, interior_z1, Ep)
        niches_bounds_par_colonne.append(_nbounds)
        _p32_niches = normalized_percage32_niches(values, _bay_i, len(_nbounds))
        percage32_niches_par_colonne.append(_p32_niches)
        _grille_col = _grille_percage32_colonne(
            interior_z0 + marge_bas_p32, interior_z1 - marge_haut_p32)
        grilles_p32_par_colonne.append(_grille_col)
        _cand_par_niche = []
        for (_nz0, _nz1), _niche in zip(_nbounds, _p32_niches):
            _cand_par_niche.append(_candidats_percage32_niche(
                _grille_col, _niche['systeme'], _niche['masquer_bas'], _niche['masquer_haut'],
                _nz0, _nz1))
        p32_niches_candidates.append(_cand_par_niche)
        p32_colonnes_candidates.append([z for _nc in _cand_par_niche for z in _nc])

    # --- Étagères : découpées en autant de tronçons que nécessaire de part et
    # d'autre de chaque montant intermédiaire, pour ne pas les traverser. Sans
    # montant, une étagère reste un seul panneau pleine largeur (comme avant).
    # Nombre d'étagères et Mode de calcul sont réglés PAR COLONNE (compartiment
    # d'étagère), via values['etageres_colonnes'] (une entrée de plus que le
    # nombre de montants intermédiaires) ; le Retrait (recul façade) reste
    # commun à toutes les colonnes. Le mode de calcul du placement vertical
    # offre 4 méthodes :
    #  - 'hauteur_colonne' (historique) : répartition égale sur la hauteur
    #    utile, le dessous de chaque étagère étant ensuite aligné sur l'axe
    #    du trou de perçage système 32 le plus proche s'il en existe.
    #  - 'nb_trous_symetrique' : la grille de trous existante (et non plus la
    #    hauteur) est divisée en (nb étagères + 1) intervalles égaux, de façon
    #    symétrique entre le bas et le haut.
    #  - 'nb_trous_ref_bas' : pas = nb de trous / nb étagères, l'étagère la
    #    plus basse étant calée sur le trou le plus bas.
    #  - 'nb_trous_ref_haut' : même pas (nb de trous / nb étagères), mais
    #    l'étagère la plus haute est calée sur le trou le plus haut.
    # Les 3 modes « nombre de trous » retombent sur le mode hauteur si la
    # colonne n'a pas de perçage système 32 actif ou n'a aucun trou disponible.
    retrait = mm_to_cm(values.get('retrait_etagere', 0))
    # Portes calculees ici (avant les etageres) : une colonne a porte
    # ENCASTREE (voir plus bas) reduit la profondeur utile de ses
    # etageres mobiles de l'epaisseur de la porte, pour ne pas entrer en
    # collision avec elle.
    portes_mode = values.get('portes_mode', 'applique')
    portes_montage = values.get('portes_montage', 'inserta')
    portes_colonnes_norm = normalized_portes_colonnes(values, len(segments))
    jeu_porte = mm_to_cm(values.get('jeu_porte', 2))
    ep_porte = mm_to_cm(values.get('ep_porte', values['ep_panneau']))
    # Charnieres Inserta Blum (voir hinge_positions_locales_mm) : axes
    # bas/haut et nombre d'intermediaires, communs a toutes les portes.
    charniere_axe_basse_mm = values.get('charniere_axe_basse', 100)
    charniere_axe_haute_mm = values.get('charniere_axe_haute', 100)
    charniere_nb_inter = int(values.get('charniere_nb_inter', 0))
    charniere_auto = values.get('charniere_auto', True)
    densite_panneau_kg_m3 = values.get('densite_panneau', 680)
    portes_niches_par_colonne = [
        normalized_portes_niches(values, _bay_i, len(niches_bounds_par_colonne[_bay_i]))
        for _bay_i in range(len(segments))
    ]
    etageres_niches_par_colonne = [
        normalized_etageres_niches(values, _bay_i, len(niches_bounds_par_colonne[_bay_i]))
        for _bay_i in range(len(segments))
    ]
    tiroirs_niches_par_colonne = [
        normalized_tiroirs_niches(values, _bay_i, len(niches_bounds_par_colonne[_bay_i]))
        for _bay_i in range(len(segments))
    ]
    prise_main_portes_par_colonne = [
        normalized_prise_main_portes_niches(values, _bay_i, len(niches_bounds_par_colonne[_bay_i]))
        for _bay_i in range(len(segments))
    ]
    prise_main_tiroirs_par_colonne = [
        normalized_prise_main_tiroirs_niches(values, _bay_i, len(niches_bounds_par_colonne[_bay_i]))
        for _bay_i in range(len(segments))
    ]
    # Niche 'active' = a une porte OU un tiroir en applique : sert a
    # decider, pour CHAQUE cote (montant/etagere fixe), si le jeu doit
    # etre partage en 2 (l'autre cote a aussi une facade active) ou si
    # la facade active doit recouvrir tout le support (l'autre cote n'a
    # RIEN, ni porte ni tiroir) -- evite tout chevauchement entre une
    # porte et un tiroir de part et d'autre d'un meme montant/etagere.
    actifs_niche_par_colonne = [
        [bool(po.get('choix', 'off') != 'off') or bool(ti.get('nb_tiroirs', 0) > 0)
         for po, ti in zip(portes_niches_par_colonne[_bay_i], tiroirs_niches_par_colonne[_bay_i])]
        for _bay_i in range(len(segments))
    ]

    def _colonne_sans_facade_active(idx):
        if idx < 0 or idx >= len(segments):
            return True
        return not any(actifs_niche_par_colonne[idx])

    def _tiroir_espace_libre(niche_z0, niche_z1, ti_entry, jeu_t_local):
        # Espace de la niche NON occupe par les tiroirs personnalises
        # (uniquement quand leurs hauteurs ne remplissent pas toute la
        # niche) : None si pas de tiroir, mode non-personnalise (les
        # tiroirs remplissent alors toujours toute la niche), ou si les
        # hauteurs couvrent deja tout. Sert a limiter une porte de la
        # meme niche a ce seul espace libre, pour ne pas chevaucher les
        # tiroirs.
        nb_t = int(ti_entry.get('nb_tiroirs', 0) or 0)
        if nb_t <= 0 or ti_entry.get('mode', 'hauteur_niche') != 'personnalise':
            return None
        tiroirs_c = ti_entry.get('tiroirs') or []
        hauteurs_cm = [
            mm_to_cm(tiroirs_c[_i].get('hauteur_mm', 0)) if _i < len(tiroirs_c) else 0.0
            for _i in range(nb_t)
        ]
        occupe = sum(hauteurs_cm) + max(nb_t - 1, 0) * jeu_t_local
        ref0 = tiroirs_c[0].get('ref', 'haut') if tiroirs_c else 'haut'
        if ref0 == 'bas':
            libre_z0 = niche_z0 + occupe
            libre_z1 = niche_z1
        else:
            libre_z0 = niche_z0
            libre_z1 = niche_z1 - occupe
        if libre_z1 - libre_z0 < 1.0:
            return None
        return (libre_z0, libre_z1, ref0)

    # Reproduit ici, en avance, les MEMES bornes de niche que la
    # section Tiroirs plus bas (z0/z1_tiroir_zone selon Type de pose,
    # puis decoupe par etagere fixe) : necessaire pour que l'espace
    # libre calcule pour les Portes soit dans le MEME referentiel que
    # les tiroirs reellement generes (sinon decalage).
    _jeu_t_tot = mm_to_cm(values.get('jeu_tiroir', 2))
    _tiroirs_mode_tot = values.get('tiroirs_mode', 'applique')
    if _tiroirs_mode_tot == 'encastre':
        _z0_tir_tot = Ep + _jeu_t_tot
        _z1_tir_tot = H - Ep - _jeu_t_tot
        _gap_ef_tir_tot = Ep / 2.0 + _jeu_t_tot
    else:
        _z0_tir_tot = _jeu_t_tot / 2.0
        _z1_tir_tot = H - _jeu_t_tot / 2.0
        _gap_ef_tir_tot = _jeu_t_tot / 2.0
    tiroirs_gap_par_colonne = []
    for _bay_i in range(len(segments)):
        _col_ef_tot = (etageres_fixes_colonnes_norm[_bay_i]
                       if _bay_i < len(etageres_fixes_colonnes_norm) else {'hauteurs': []})
        _axes_ef_tot = [mm_to_cm(h) for h in _col_ef_tot.get('hauteurs', [])]
        if _tiroirs_mode_tot == 'applique':
            _v_slices_tot = _v_slices_facade_applique(
                _z0_tir_tot, _z1_tir_tot, _axes_ef_tot,
                actifs_niche_par_colonne[_bay_i], _jeu_t_tot, Ep)
        else:
            _v_slices_tot = _split_by_axes(_z0_tir_tot, _z1_tir_tot, _axes_ef_tot, _gap_ef_tir_tot)
        _ligne = []
        for _n_i, _sl in enumerate(_v_slices_tot):
            if _sl is None:
                _ligne.append(None)
                continue
            _tn = (tiroirs_niches_par_colonne[_bay_i][_n_i]
                   if _n_i < len(tiroirs_niches_par_colonne[_bay_i]) else {'nb_tiroirs': 0})
            _ligne.append(_tiroir_espace_libre(_sl[0], _sl[1], _tn, _jeu_t_tot))
        tiroirs_gap_par_colonne.append(_ligne)
    # Positions des etageres mobiles par niche (z_start, cm) : reutilisees
    # plus bas par la section Portes pour eviter qu'une charniere
    # intermediaire ne tombe juste en face d'une etagere.
    etageres_z_par_colonne = [[[] for _ in _nb] for _nb in niches_bounds_par_colonne]
    for bay_i, (seg_x0, seg_x1) in enumerate(segments):
        nbounds = niches_bounds_par_colonne[bay_i]
        p32_niches = percage32_niches_par_colonne[bay_i]
        et_niches = etageres_niches_par_colonne[bay_i]
        po_niches = portes_niches_par_colonne[bay_i]
        for niche_i, (nz0, nz1) in enumerate(nbounds):
            col_etageres = et_niches[niche_i]
            nb_etageres_niche = col_etageres['nb_etageres']
            if nb_etageres_niche <= 0 or p32_niches[niche_i]['systeme'] == 'off':
                continue  # pas d'etagere demandee, ou niche sans percage systeme 32
            niche_candidates = p32_niches_candidates[bay_i][niche_i]
            z_starts = compute_etagere_z_starts(
                nb_etageres_niche, col_etageres['mode'], nz0, nz1 - nz0, Ep, niche_candidates)
            etageres_z_par_colonne[bay_i][niche_i] = list(z_starts)
            porte_encastree_ici = (portes_mode == 'encastre'
                                   and po_niches[niche_i].get('choix', 'off') != 'off')
            retrait_niche = (retrait + ep_porte) if porte_encastree_ici else retrait
            for i in range(1, nb_etageres_niche + 1):
                z_start = z_starts[i - 1]
                j = bay_i + 1
                parts = []
                if len(segments) > 1:
                    parts.append('section {}'.format(j))
                if len(nbounds) > 1:
                    parts.append('niche {}'.format(niche_i + 1))
                suffixe = (' ' + ' '.join(parts)) if parts else ''
                name = 'Étagère {}{}'.format(i, suffixe)
                panels.append(
                    (seg_x0, seg_x1, retrait_niche, interior_depth, z_start, Ep, name, 'EpPanneau'))

    # --- Option '3 Trous' (Percage 32, par niche) : masque tous les
    # trous du systeme 32 de cette niche SAUF ceux reellement utilises
    # par une etagere mobile, plus le trou immediatement au-dessus et
    # celui immediatement en dessous de chacun (3 trous par etagere).
    # S'applique APRES le calcul des etageres mobiles (dont on a besoin
    # des positions reelles) mais AVANT la section Portes : les trous
    # ajoutes plus loin pour les charnieres (voir plus bas) ne sont donc
    # jamais affectes par ce masquage.
    for bay_i in range(len(segments)):
        _p32_niches_ici = percage32_niches_par_colonne[bay_i]
        _nbounds_ici = niches_bounds_par_colonne[bay_i]
        for niche_i in range(len(_nbounds_ici)):
            _niche_cfg = (_p32_niches_ici[niche_i]
                          if niche_i < len(_p32_niches_ici) else {})
            if not _niche_cfg.get('trois_trous'):
                continue
            _candidats_niche = sorted(
                p32_niches_candidates[bay_i][niche_i]
                if niche_i < len(p32_niches_candidates[bay_i]) else [])
            _z_etageres_niche = (
                etageres_z_par_colonne[bay_i][niche_i]
                if niche_i < len(etageres_z_par_colonne[bay_i]) else [])
            _gardes = set()
            for _z_et in _z_etageres_niche:
                if not _candidats_niche:
                    continue
                _idx_proche = min(
                    range(len(_candidats_niche)),
                    key=lambda _idx: abs(_candidats_niche[_idx] - _z_et))
                for _offset in (-1, 0, 1):
                    _idx = _idx_proche + _offset
                    if 0 <= _idx < len(_candidats_niche):
                        _gardes.add(round(_candidats_niche[_idx], 4))
            _a_retirer = [c for c in _candidats_niche if round(c, 4) not in _gardes]
            if _a_retirer and bay_i < len(p32_colonnes_candidates):
                for _c in _a_retirer:
                    p32_colonnes_candidates[bay_i] = [
                        _cc for _cc in p32_colonnes_candidates[bay_i] if abs(_cc - _c) > 0.001
                    ]

    # --- Portes (composants independants de premier niveau, compatibles
    # avec l'add-in Porte Inserta Blum), configurees PAR NICHE (compartiment
    # vertical delimite par les etageres fixe d'une colonne -- voir
    # niches_bounds_par_colonne) via values['portes_colonnes'] : chaque
    # niche choisit Off / Gauche (1 porte) / Droite (1 porte) / 2 Portes.
    # Horizontalement, une porte va du bord exterieur de sa colonne (bord du
    # caisson, ou axe du montant intermediaire voisin) jusqu'a son autre
    # bord, moins le Jeu peripherique -- identique pour toutes les niches
    # d'une meme colonne (les niches ne subdivisent que verticalement).
    doors = []  # (x0_local_cm, z0_local_cm, largeur_cm, hauteur_cm, ep_cm, sens, mode)
    # portes_mode / jeu_porte / ep_porte / portes_niches_par_colonne deja
    # calcules plus haut (avant les etageres, voir commentaire la-bas). Le
    # mode 'applique' (par defaut) va jusqu'a l'axe du montant voisin, comme
    # avant ; le mode 'encastre' reste a l'interieur de la colonne (bornes
    # 'segments', comme les etageres/percage32), toujours avec le jeu
    # complet de chaque cote puisque ces bornes sont deja des faces
    # physiques (pas d'axe partage avec la colonne voisine, contrairement
    # au mode applique).
    if portes_mode == 'encastre':
        portes_col_starts = [s for s, _e in segments]
        portes_col_ends = [e for _s, e in segments]
    else:
        portes_col_starts = [0.0] + montant_centres
        portes_col_ends = montant_centres + [L]
    # En mode encastre, la porte occupe la MEME plage de profondeur que
    # le Dessus/Dessous (qui courent sur toute la profondeur) : il faut
    # donc degager leur EPAISSEUR reelle (Ep), pas seulement le jeu, sous
    # peine de chevauchement solide. En applique, la porte est devant le
    # caisson (profondeur differente) : le jeu seul suffit, comme avant.
    if portes_mode == 'encastre':
        z0_porte_zone = Ep + jeu_porte
        z1_porte_zone = H - Ep - jeu_porte
    else:
        # Meme principe que pour le bord exterieur horizontal (voir plus
        # bas) : le Dessous/Dessus doit perdre le MEME jeu qu'une
        # etagere fixe intermediaire partagee (jeu_porte/2), pour que
        # les hauteurs de porte restent identiques partout.
        z0_porte_zone = jeu_porte / 2.0
        z1_porte_zone = H - jeu_porte / 2.0

    def _colonne_a_charniere_cote(idx, cote):
        # Vraie si la colonne idx a AU MOINS UNE niche avec une porte
        # dont une charniere se trouve sur le bord 'cote' ('gauche' ou
        # 'droite') du COMPARTIMENT -- verification au niveau colonne
        # (simplification : ne recoupe pas precisement les niches d'une
        # colonne avec celles de la colonne voisine, qui peuvent avoir
        # des etageres fixe differentes).
        if idx < 0 or idx >= len(segments):
            return False
        for _niche in portes_niches_par_colonne[idx]:
            _c = _niche.get('choix', 'off')
            if _c == cote or _c == '2portes':
                return True
        return False

    for bay_i, _seg in enumerate(segments):
        po_niches = portes_niches_par_colonne[bay_i]
        if all(n.get('choix', 'off') == 'off' for n in po_niches):
            continue  # aucune niche de cette colonne n'a de porte
        col_x0 = portes_col_starts[bay_i]
        col_x1 = portes_col_ends[bay_i]
        if portes_mode == 'encastre':
            # Bornes deja physiques (face du cote/montant) des deux cotes :
            # jeu complet partout, pas de partage possible.
            x0 = col_x0 + jeu_porte
            x1 = col_x1 - jeu_porte
        else:
            # Jeu peripherique complet contre un bord EXTERIEUR (cote du
            # caisson), mais moitie du jeu contre un axe de montant
            # intermediaire PARTAGE avec la colonne voisine (les 2 portes
            # se partagent alors le jeu total : moitie de chaque cote,
            # pour un jeu combine egal au jeu regle, pas le double).
            # Si la colonne voisine est ENTIEREMENT Off (aucune de ses
            # niches n'a de porte), il n'y a rien a partager : cette porte
            # recouvre alors le montant en entier, s'arretant jeu_porte
            # au-dela de sa face opposee (cote de la colonne Off), au lieu
            # de s'arreter a mi-montant.
            gauche_off = bay_i > 0 and _colonne_sans_facade_active(bay_i - 1)
            droite_off = bay_i < len(segments) - 1 and _colonne_sans_facade_active(bay_i + 1)
            # Les entraxes bruts (bord-a-1er-axe, axe-a-axe, dernier-axe-a-
            # bord) sont deja identiques par construction (division
            # simple). Pour que les largeurs de porte FINALES le restent,
            # le bord EXTERIEUR (cote du caisson) doit donc perdre
            # exactement le MEME jeu que celui partage sur un montant
            # intermediaire (jeu_porte/2), et non le jeu complet -- sinon
            # les portes exterieures ressortent plus etroites.
            if bay_i == 0:
                x0 = col_x0 + jeu_porte / 2.0
            elif gauche_off:
                x0 = (montant_centres[bay_i - 1] - Ep / 2.0) + jeu_porte
            else:
                x0 = col_x0 + jeu_porte / 2.0
            if bay_i == len(segments) - 1:
                x1 = col_x1 - jeu_porte / 2.0
            elif droite_off:
                x1 = (montant_centres[bay_i] + Ep / 2.0) - jeu_porte
            else:
                x1 = col_x1 - jeu_porte / 2.0
        if x1 <= x0:
            raise MeubleLayoutError(
                'Largeur de colonne insuffisante pour une porte (colonne {}) : '
                'augmenter la Largeur, reduire le Jeu peripherique porte ou '
                'ajouter un montant.'.format(bay_i + 1))
        col_ef = (etageres_fixes_colonnes_norm[bay_i]
                  if bay_i < len(etageres_fixes_colonnes_norm) else {'hauteurs': []})
        axes_ef = [mm_to_cm(h) for h in col_ef.get('hauteurs', [])]
        # A la verticale, une etagere fixe est un axe PARTAGE entre 2 niches
        # empilees. En mode encastre, la porte occupe la meme profondeur que
        # l'etagere fixe (qui court sur toute la profondeur) : il faut
        # degager sa DEMI-EPAISSEUR (Ep/2) en plus du jeu, sous peine de
        # chevauchement solide -- pas seulement un jeu autour de l'axe
        # theorique comme en mode applique (ou la porte est devant le
        # caisson, donc jamais en collision reelle).
        if portes_mode == 'applique':
            v_slices_par_niche = _v_slices_facade_applique(
                z0_porte_zone, z1_porte_zone, axes_ef,
                actifs_niche_par_colonne[bay_i], jeu_porte, Ep)
        else:
            gap_ef = Ep / 2.0 + jeu_porte
            v_slices_par_niche = _split_by_axes(z0_porte_zone, z1_porte_zone, axes_ef, gap_ef)
        for niche_i, _slice in enumerate(v_slices_par_niche):
            if _slice is None:
                continue
            vz0, vz1 = _slice
            choix_niche = (po_niches[niche_i].get('choix', 'off')
                           if niche_i < len(po_niches) else 'off')
            if choix_niche == 'off':
                continue
            # Si des tiroirs personnalises occupent une partie de cette
            # meme niche sans la remplir entierement, la porte se limite
            # a l'espace libre restant (intersection avec ses propres
            # bornes harmonisees habituelles).
            _gap = (tiroirs_gap_par_colonne[bay_i][niche_i]
                    if bay_i < len(tiroirs_gap_par_colonne)
                    and niche_i < len(tiroirs_gap_par_colonne[bay_i]) else None)
            if _gap is not None:
                _gap_z0, _gap_z1, _gap_ref = _gap
                # Le bord du vide adjacent au tiroir a besoin du meme jeu
                # que n'importe quel autre bord de porte (jeu_porte/2) ;
                # l'autre bord est deja le bord normal de la niche, deja
                # jeu-margine par le calcul standard (vz0/vz1 d'origine).
                if _gap_ref == 'bas':
                    _gap_z0 += jeu_porte / 2.0
                else:
                    _gap_z1 -= jeu_porte / 2.0
                vz0 = max(vz0, _gap_z0)
                vz1 = min(vz1, _gap_z1)
                if vz1 - vz0 < 1.0:
                    continue
            if choix_niche == '2portes':
                largeur_paire = (x1 - x0 - jeu_porte) / 2.0
                if largeur_paire <= 0:
                    raise MeubleLayoutError(
                        'Largeur de colonne insuffisante pour 2 portes (colonne {}) : '
                        'augmenter la Largeur, reduire le Jeu peripherique porte ou '
                        'choisir 1 porte pour cette colonne.'.format(bay_i + 1))
                # Chaque battant garde sa charniere sur son bord
                # EXTERIEUR (le battant de gauche a gauche, celui de
                # droite a droite) : l'etiquette est ici l'inverse de
                # celle du choix Gauche/Droite du dialogue, voir la
                # convention inversee dans _drill_hinges_inserta.
                h_slices = [(x0, x0 + largeur_paire, 'gauche'),
                            (x0 + largeur_paire + jeu_porte, x1, 'droite')]
            else:
                h_slices = [(x0, x1, choix_niche)]
            grille_col = (grilles_p32_par_colonne[bay_i]
                          if bay_i < len(grilles_p32_par_colonne) else [])
            for hx0, hx1, sens in h_slices:
                # Nombre de charnieres calcule PAR BATTANT (la largeur
                # d'un battant en mode '2 Portes' est la moitie de celle
                # d'une porte simple, donc potentiellement moins lourd et
                # necessitant moins de charnieres).
                if charniere_auto:
                    nb_std, doubler_haut_ici = nb_charnieres_auto(
                        hx1 - hx0, vz1 - vz0, ep_porte, densite_panneau_kg_m3)
                    nb_inter_ici = max(0, nb_std - 2)
                else:
                    nb_inter_ici = charniere_nb_inter
                    doubler_haut_ici = False
                etageres_occ_ici = (etageres_z_par_colonne[bay_i][niche_i]
                                   if (bay_i < len(etageres_z_par_colonne)
                                       and niche_i < len(etageres_z_par_colonne[bay_i]))
                                   else [])
                _p32_niche_pour_snap = (percage32_niches_par_colonne[bay_i][niche_i]
                                        if bay_i < len(percage32_niches_par_colonne)
                                        and niche_i < len(percage32_niches_par_colonne[bay_i])
                                        else {'systeme': 'off'})
                _snap_ici = _p32_niche_pour_snap.get('systeme', 'off') != 'off'
                hinges_mm, trous_requis_cm = hinge_positions_locales_mm(
                    vz1 - vz0, vz0, charniere_axe_basse_mm, charniere_axe_haute_mm,
                    nb_inter_ici, grille_col, etageres_occ_ici, doubler_haut_ici,
                    snap_to_grid=_snap_ici)
                # Les 2 trous qui encadrent chaque axe de charniere doivent
                # exister cote caisson, meme s'ils ont ete masques (Masquer
                # bas/haut) ou exclus par le systeme 64/Off de cette niche --
                # on les rajoute donc a la grille REELLEMENT percee.
                if trous_requis_cm and bay_i < len(p32_colonnes_candidates):
                    for _z in trous_requis_cm:
                        _deja = next((_c for _c in p32_colonnes_candidates[bay_i]
                                      if abs(_z - _c) < 0.01), None)
                        if _deja is None:
                            # Trou absent de la grille reellement percee
                            # (masque, ou exclu par le systeme 64/Off) : on
                            # le note pour percage separe, UNIQUEMENT cote
                            # charniere (avant) ET UNIQUEMENT sur la face du
                            # montant/cote ou se trouve reellement cette
                            # charniere (le sens de la porte, 'gauche' ou
                            # 'droite', correspond directement a la face
                            # 'Gauche'/'Droite' du compartiment).
                            p32_extra_side_only[bay_i].setdefault(
                                round(_z, 4), set()).add(sens)
                        # Sinon (deja perce normalement) : rien a faire, il
                        # sera perce des deux cotes (avant + arriere) comme
                        # d'habitude.
                ouverte_ici = bool(po_niches[niche_i].get('ouverte', False)
                                   if niche_i < len(po_niches) else False)
                if portes_mode == 'encastre':
                    offset_ouverture_mm = OFFSET_OUVERTURE_ENCASTRE_MM
                elif sens == 'gauche':
                    jumelee = _colonne_a_charniere_cote(bay_i - 1, 'droite')
                    offset_ouverture_mm = (
                        OFFSET_OUVERTURE_JUMELEE_MM if jumelee else OFFSET_OUVERTURE_SIMPLE_MM)
                else:
                    jumelee = _colonne_a_charniere_cote(bay_i + 1, 'gauche')
                    offset_ouverture_mm = (
                        OFFSET_OUVERTURE_JUMELEE_MM if jumelee else OFFSET_OUVERTURE_SIMPLE_MM)
                _pm_porte_niche = (prise_main_portes_par_colonne[bay_i]
                                   if bay_i < len(prise_main_portes_par_colonne) else [])
                pm_porte_code = (_pm_porte_niche[niche_i]
                                 if niche_i < len(_pm_porte_niche) else 'sans')
                doors.append((hx0, vz0, hx1 - hx0, vz1 - vz0, ep_porte, sens, portes_mode, hinges_mm,
                              ouverte_ici, offset_ouverture_mm, pm_porte_code, portes_montage))
                # Percages d'embase (Ã5mm) sur le montant cote
                # charnieres, UNIQUEMENT quand le percage systeme 32 de
                # cette niche est Off (sinon la grille systeme 32
                # elle-meme sert de fixation) : memes positions Z que les
                # charnieres (par paires, entraxe 32mm), a la meme
                # distance Y de la face avant que le systeme 32
                # (percage32_retrait).
                _p32_niche_ici = (percage32_niches_par_colonne[bay_i][niche_i]
                                  if bay_i < len(percage32_niches_par_colonne)
                                  and niche_i < len(percage32_niches_par_colonne[bay_i])
                                  else {'systeme': 'off'})
                if (_p32_niche_ici.get('systeme', 'off') == 'off'
                        and values.get('portes_montage_embase', 'eurovis') != 'off'):
                    _retrait_p32_ici = mm_to_cm(values.get('percage32_retrait', 37))
                    _x_plane_embase = _seg[0] if sens == 'gauche' else _seg[1]
                    _sign_embase = -1 if sens == 'gauche' else 1
                    # Profondeur d'embase : 'a visser' = toujours 1mm ;
                    # 'Eurovis' = 11.5mm normalement, mais 8.5mm si ce
                    # trou est sur un montant INTERMEDIAIRE (pas le cote
                    # exterieur du meuble) -- l'autre colonne peut aussi
                    # y percer depuis l'autre face, il faut degager pour
                    # ne pas se croiser.
                    _montage_embase_ici = values.get('portes_montage_embase', 'eurovis')
                    if _montage_embase_ici == 'visser':
                        _prof_embase_cm = mm_to_cm(1.0)
                    else:
                        _montant_intermediaire = (
                            bay_i > 0 if sens == 'gauche' else bay_i < len(segments) - 1)
                        _prof_embase_cm = mm_to_cm(8.5 if _montant_intermediaire else 11.5)
                    for _hz_mm in hinges_mm:
                        _hz_cm = vz0 + mm_to_cm(_hz_mm)
                        for _dz in (-1.6, 1.6):
                            holes.append((
                                'X', _x_plane_embase, _sign_embase, _retrait_p32_ici,
                                _hz_cm + _dz, mm_to_cm(5.0), _prof_embase_cm,
                                'Colonne {} Percage embase charniere {}mm'.format(
                                    bay_i + 1, int(round(_hz_mm)))))

    # --- Tiroirs (facade + caisse interne, en corps separes), PAR NICHE,
    # meme comportement dimensionnel que les Portes : Type de pose
    # (applique/encastre), bornes de colonne harmonisees horizontalement
    # (jeu_tiroir/2 partout en applique, jeu_tiroir complet en encastre),
    # et decoupe verticale par niche (etageres fixe). Dans chaque niche,
    # le Nombre de tiroirs de cette niche partage sa hauteur en bandes
    # egales (mode 'hauteur_niche', seul mode actuel).
    tiroirs_mode = values.get('tiroirs_mode', 'applique')
    jeu_t = mm_to_cm(values.get('jeu_tiroir', 2))
    ep_face = mm_to_cm(values.get('ep_face_tiroir', values['ep_panneau']))
    if tiroirs_mode == 'encastre':
        tiroir_col_starts = [s for s, _e in segments]
        tiroir_col_ends = [e for _s, e in segments]
        z0_tiroir_zone = Ep + jeu_t
        z1_tiroir_zone = H - Ep - jeu_t
        gap_ef_t = Ep / 2.0 + jeu_t
    else:
        tiroir_col_starts = [0.0] + montant_centres
        tiroir_col_ends = montant_centres + [L]
        z0_tiroir_zone = jeu_t / 2.0
        z1_tiroir_zone = H - jeu_t / 2.0
        gap_ef_t = jeu_t / 2.0

    for bay_i, _seg in enumerate(segments):
        tir_niches = tiroirs_niches_par_colonne[bay_i]
        if all(n.get('nb_tiroirs', 0) <= 0 for n in tir_niches):
            continue
        col_x0 = tiroir_col_starts[bay_i]
        col_x1 = tiroir_col_ends[bay_i]
        if tiroirs_mode == 'encastre':
            x0 = col_x0 + jeu_t
            x1 = col_x1 - jeu_t
        else:
            # Meme principe que pour les Portes : jeu complet contre un
            # bord EXTERIEUR ou un montant dont l'autre colonne n'a AUCUNE
            # facade active (porte ou tiroir) -- sinon jeu partage.
            gauche_off = bay_i > 0 and _colonne_sans_facade_active(bay_i - 1)
            droite_off = bay_i < len(segments) - 1 and _colonne_sans_facade_active(bay_i + 1)
            if bay_i == 0:
                x0 = col_x0 + jeu_t / 2.0
            elif gauche_off:
                x0 = (montant_centres[bay_i - 1] - Ep / 2.0) + jeu_t
            else:
                x0 = col_x0 + jeu_t / 2.0
            if bay_i == len(segments) - 1:
                x1 = col_x1 - jeu_t / 2.0
            elif droite_off:
                x1 = (montant_centres[bay_i] + Ep / 2.0) - jeu_t
            else:
                x1 = col_x1 - jeu_t / 2.0
        if x1 <= x0:
            raise MeubleLayoutError(
                'Largeur de colonne insuffisante pour un tiroir (colonne {}) : '
                'augmenter la Largeur, reduire le Jeu peripherique facade ou '
                'ajouter un montant.'.format(bay_i + 1))
        col_ef = (etageres_fixes_colonnes_norm[bay_i]
                  if bay_i < len(etageres_fixes_colonnes_norm) else {'hauteurs': []})
        axes_ef = [mm_to_cm(h) for h in col_ef.get('hauteurs', [])]
        if tiroirs_mode == 'applique':
            v_slices_par_niche = _v_slices_facade_applique(
                z0_tiroir_zone, z1_tiroir_zone, axes_ef,
                actifs_niche_par_colonne[bay_i], jeu_t, Ep)
        else:
            v_slices_par_niche = _split_by_axes(z0_tiroir_zone, z1_tiroir_zone, axes_ef, gap_ef_t)
        seg_x0, seg_x1 = _seg
        for niche_i, _slice in enumerate(v_slices_par_niche):
            if _slice is None:
                continue
            vz0, vz1 = _slice
            nb_t = (tir_niches[niche_i].get('nb_tiroirs', 0)
                    if niche_i < len(tir_niches) else 0)
            if nb_t <= 0:
                continue
            niche_mode_tiroir = (tir_niches[niche_i].get('mode', 'hauteur_niche')
                                 if niche_i < len(tir_niches) else 'hauteur_niche')
            if niche_mode_tiroir == 'personnalise':
                # Facades personnalisees : hauteur exacte saisie par
                # tiroir, empilees dans l'ordre du tableau (jeu_t entre
                # chacune), ancrees en haut ou en bas de la niche selon la
                # reference du 1er tiroir de la pile (les references des
                # tiroirs suivants ne sont pas utilisees).
                tiroirs_custom = (tir_niches[niche_i].get('tiroirs', [])
                                  if niche_i < len(tir_niches) else [])
                hauteurs_cm = [
                    mm_to_cm(tiroirs_custom[_i].get('hauteur_mm', 0)) if _i < len(tiroirs_custom) else 0.0
                    for _i in range(nb_t)
                ]
                ref0 = tiroirs_custom[0].get('ref', 'haut') if tiroirs_custom else 'haut'
                face_bounds = []
                if ref0 == 'bas':
                    _cur = vz0
                    for _h in hauteurs_cm:
                        face_bounds.append((_cur, _cur + _h))
                        _cur += _h + jeu_t
                else:
                    _cur = vz1
                    _tmp = []
                    for _h in hauteurs_cm:
                        _tmp.append((_cur - _h, _cur))
                        _cur -= _h + jeu_t
                    face_bounds = _tmp
                if any(f1 <= f0 or f0 < vz0 - 0.001 or f1 > vz1 + 0.001 for f0, f1 in face_bounds):
                    raise MeubleLayoutError(
                        "Hauteurs de facade personnalisees invalides ou depassant la "
                        "niche (colonne {}, niche {}) : verifier les hauteurs et le Jeu "
                        "peripherique facade.".format(bay_i + 1, niche_i + 1))
            else:
                band_h = (vz1 - vz0) / nb_t
                if band_h <= jeu_t:
                    raise MeubleLayoutError(
                        'Hauteur de niche insuffisante pour le nombre de tiroirs demande '
                        '(colonne {}, niche {}) : augmenter la Hauteur, reduire le Nombre '
                        'de tiroirs ou le Jeu peripherique facade.'.format(bay_i + 1, niche_i + 1))
                face_bounds = [(vz0 + _i * band_h + jeu_t / 2.0, vz0 + (_i + 1) * band_h - jeu_t / 2.0)
                               for _i in range(nb_t)]
            # Supports physiques reels au-dessus/en-dessous de CETTE
            # niche (Dessus/Dessous du caisson si niche extreme, sinon
            # etagere fixe voisine) : ne dependent pas de k, calcules une
            # fois par niche.
            if niche_i == len(v_slices_par_niche) - 1:
                support_haut = interior_z1
            else:
                support_haut = axes_ef[niche_i] - Ep / 2.0
            if niche_i == 0:
                support_bas = interior_z0
            else:
                support_bas = axes_ef[niche_i - 1] + Ep / 2.0
            # Pre-passe : hauteur de cote/fond-arriere individuelle de
            # chaque tiroir de CETTE niche (meme formules que plus bas),
            # pour en tirer le minimum -- simplifie la fabrication en
            # appliquant cette meme hauteur (reduite) a tous les caissons
            # de la serie. Seul le HAUT est ainsi raccourci : le bas de
            # chaque cote (donc la rainure/le fond) reste individuel,
            # inchange.
            # Indice PHYSIQUEMENT le plus bas/le plus haut (pas forcement
            # k==0/k==nb_t-1 : en mode personnalise avec Ref Haut, le
            # tiroir k==0 est place tout en haut, pas en bas).
            k_bas_physique = min(range(nb_t), key=lambda kk: face_bounds[kk][0])
            k_haut_physique = max(range(nb_t), key=lambda kk: face_bounds[kk][1])
            # En mode personnalise, les facades ne remplissent pas forcement
            # toute la niche (ex. Ref Haut avec un vide en dessous) : la
            # reference Dessus/Dessous ne s'applique que si le tiroir
            # physiquement le plus bas/haut TOUCHE vraiment ce bord (sinon
            # c'est juste le dernier tiroir place, pas forcement adjacent).
            k_bas_touche_bord = abs(face_bounds[k_bas_physique][0] - vz0) < 1.0
            k_haut_touche_bord = abs(face_bounds[k_haut_physique][1] - vz1) < 1.0
            _side_h_par_tiroir = []
            for _k in range(nb_t):
                _face_z0, _face_z1 = face_bounds[_k]
                if _k == k_haut_physique and nb_t > 1 and k_haut_touche_bord:
                    _cote_top = support_haut - MARGE_HAUT_COTE_CM
                else:
                    _cote_top = _face_z1 - MARGE_HAUT_COTE_CM
                if _k == k_bas_physique and nb_t > 1 and k_bas_touche_bord:
                    _fond_z0 = support_bas + DEGAGEMENT_TIROIR_BAS_CM
                else:
                    _fond_z0 = _face_z0 + DEGAGEMENT_TIROIR_BAS_CM
                _side_z0 = _fond_z0 - DEGAGEMENT_COULISSE_CM
                _side_h_par_tiroir.append(max(_cote_top - _side_z0, 1.0))
            min_side_h_niche = min(_side_h_par_tiroir) if _side_h_par_tiroir else 1.0

            for k in range(nb_t):
                face_z0, face_z1 = face_bounds[k]
                n = k + 1
                parts = ['Colonne {}'.format(bay_i + 1)]
                if len(v_slices_par_niche) > 1:
                    parts.append('Niche {}'.format(niche_i + 1))
                parts.append('Tiroir {} Façade'.format(n))
                name = ' '.join(parts)

                # facade (plan XZ, en applique devant le caisson, encastree
                # dans le caisson sinon -- meme convention que les Portes).
                ep_face_signee = (
                    -ep_face if tiroirs_mode != 'encastre'
                    else mm_to_cm(values.get('retrait_percage_coulisse', 0)))
                panels.append(('XZ', x0, x1, face_z0, face_z1,
                               ep_face_signee, ep_face, name, 'EpFaceTiroir'))

                # Prise de main (uniquement 'Haut' pour les tiroirs) :
                # rainure sur le chant du haut de LA FACADE, debouchante
                # sur toute sa largeur.
                _pm_tir_niche = (prise_main_tiroirs_par_colonne[bay_i][niche_i]
                                 if bay_i < len(prise_main_tiroirs_par_colonne)
                                 and niche_i < len(prise_main_tiroirs_par_colonne[bay_i])
                                 else [])
                _pm_tir_code = _pm_tir_niche[k] if k < len(_pm_tir_niche) else 'sans'
                if _pm_tir_code == 'haut':
                    prises_main.append((
                        'x', x0, x1 - x0, ep_face_signee, face_z1, -1,
                        name + ' Prise de main', name))
                elif _pm_tir_code == 'bas':
                    prises_main.append((
                        'x', x0, x1 - x0, ep_face_signee, face_z0, 1,
                        name + ' Prise de main', name))

                # Caisse interne simplifiee (meme logique que le caisson
                # principal), a l'interieur des bornes PHYSIQUES de la
                # colonne (segments), pas des bornes harmonisees de la
                # facade.
                # LARGEUR_COULISSE_CM se mesure jusqu'a la face INTERIEURE
                # (rainuree) du cote, pas sa face exterieure : on retranche
                # donc Ep pour positionner la face exterieure (drawer_x0).
                drawer_x0 = seg_x0 + LARGEUR_COULISSE_CM - Ep
                drawer_x1 = seg_x1 - LARGEUR_COULISSE_CM + Ep
                # Profondeur utile REELLE entre le dos de la facade et le
                # fond du meuble (moins 5mm de marge), arrondie a la plus
                # grande longueur de coulisse Tandem Blum qui y tient ; le
                # caisson fait alors 10mm de moins que cette longueur
                # nominale (convention Blum).
                _profondeur_utile_mm = cm_to_mm(interior_depth - ep_face) - 5.0
                # En encastre, le tiroir recule en plus du decalage
                # 'Decalage en profondeur' (voir box_y0) : la profondeur
                # utile disponible pour le caisson diminue d'autant.
                if tiroirs_mode == 'encastre':
                    _profondeur_utile_mm -= values.get('retrait_percage_coulisse', 0)
                _tiroirs_niche_k = tir_niches[niche_i].get('tiroirs', [])
                _t_capacite = (_tiroirs_niche_k[k].get('capacite_kg', 30)
                               if k < len(_tiroirs_niche_k) else 30)
                _longueur_coulisse_mm, _profondeur_caisson_mm = (
                    choisir_profondeur_caisson_tiroir_mm(_profondeur_utile_mm, _t_capacite))
                drawer_depth_total = max(mm_to_cm(_profondeur_caisson_mm), ep_face + 1.0)
                # En encastre, la facade occupe elle-meme les premiers
                # ep_face de profondeur (voir ep_face_signee=0 ci-dessus) :
                # le caisson du tiroir doit donc reculer d'autant pour ne
                # pas la chevaucher. En applique, la facade est devant le
                # caisson (hors du volume interieur) : pas de recul.
                box_y0 = (ep_face + mm_to_cm(values.get('retrait_percage_coulisse', 0))
                          if tiroirs_mode == 'encastre' else 0.0)
                # Le haut du cote reste normalement MARGE_HAUT_COTE_CM en
                # dessous du haut de SA PROPRE facade -- sauf pour le tiroir
                # le plus haut de sa niche, qui se refere plutot au support
                # REEL juste au-dessus (le Dessus du caisson si c'est la
                # niche la plus haute, sinon la face basse de l'etagere fixe
                # qui la borde en haut), meme s'il est plus proche/loin que
                # sa facade.
                est_dernier_tiroir = k == k_haut_physique
                if est_dernier_tiroir and nb_t > 1 and k_haut_touche_bord:
                    cote_top = support_haut - MARGE_HAUT_COTE_CM
                else:
                    cote_top = face_z1 - MARGE_HAUT_COTE_CM
                # Le bas du fond (donc le bas utile du cote au niveau de la
                # rainure) reste normalement DEGAGEMENT_COULISSE_CM au-dessus
                # du bas de SA PROPRE facade -- sauf pour le tiroir le plus
                # bas de sa niche, qui se refere plutot au support REEL juste
                # en dessous (le Dessous du caisson si c'est la niche la plus
                # basse, sinon la face haute de l'etagere fixe qui la borde
                # en bas) -- ecart reel demande. Le bas du cote lui-meme est
                # alors AUSSI recalcule pour preserver EXACTEMENT
                # DEGAGEMENT_COULISSE_CM entre le bas du cote et le bas de la
                # rainure (comme dans le cas normal), sous peine de
                # decalage de la rainure par rapport au cote.
                est_premier_tiroir = k == k_bas_physique
                if est_premier_tiroir and nb_t > 1 and k_bas_touche_bord:
                    fond_z0_override = support_bas + DEGAGEMENT_TIROIR_BAS_CM
                else:
                    # Meme regle que le cas special (28,5mm), mais mesuree
                    # depuis le bas de SA PROPRE facade plutot que depuis un
                    # support physique (Dessous/etagere fixe).
                    fond_z0_override = face_z0 + DEGAGEMENT_TIROIR_BAS_CM
                side_z0 = fond_z0_override - DEGAGEMENT_COULISSE_CM
                # L'harmonisation (hauteur minimale commune a toute la
                # serie) ne s'applique qu'en mode Hauteur egale ; en mode
                # Personnalise, chaque caisson garde sa propre hauteur
                # (calculee par rapport a sa propre facade et, le cas
                # echeant, au Dessus/etagere fixe juste au-dessus).
                side_h = (max(cote_top - side_z0, 1.0)
                          if niche_mode_tiroir == 'personnalise' else min_side_h_niche)
                if drawer_x1 > drawer_x0:
                    nom_cote_g = name.replace('Façade', 'Côté G')
                    nom_cote_d = name.replace('Façade', 'Côté D')
                    panels.append((drawer_x0, drawer_x0 + Ep, box_y0, box_y0 + drawer_depth_total,
                                   side_z0, side_h, nom_cote_g, None))
                    panels.append((drawer_x1 - Ep, drawer_x1, box_y0, box_y0 + drawer_depth_total,
                                   side_z0, side_h, nom_cote_d, None))
                    # Fond capte dans une rainure de chaque cote (coulisses
                    # invisibles sous caisse) : degagement DEGAGEMENT_COULISSE_CM
                    # au-dessus du bas des cotes, rainure haute et profonde de
                    # Ef (ajustement exact), traversante sur toute la
                    # profondeur, usinee depuis la face interieure de chaque
                    # cote vers l'exterieur (voir cut_groove_x).
                    fond_z0 = fond_z0_override
                    # Percages sur les cotes du MEUBLE (pas du tiroir) pour
                    # les vis/eurovis de fixation de la coulisse Tandem
                    # Blum, selon le gabarit correspondant a la longueur
                    # nominale choisie pour ce tiroir. Positions X donnees
                    # depuis la face avant du MEUBLE (Y=0), percage cote
                    # gauche ET droite de la colonne, a Z = dessous du fond
                    # + 9.5mm.
                    _z_percage_coulisse = fond_z0 + DEGAGEMENT_PERCAGE_COULISSE_CM
                    _positions_percage = TANDEM_BLUM_PERCAGES_MM.get(
                        int(_longueur_coulisse_mm), [])
                    # En encastre, la facade occupe le premier ep_face de
                    # profondeur (le tiroir recule d'autant, voir plus haut) :
                    # les percages, mesures depuis la face avant du MEUBLE
                    # dans le gabarit Blum (prevu pour de l'applique), doivent
                    # donc aussi reculer d'autant, plus un retrait
                    # supplementaire reglable.
                    _decalage_percage_cm = box_y0
                    # Profondeur des trous de coulisse : 'a visser' =
                    # toujours 1mm ; 'Eurovis' = 11.5mm, ou 8.5mm si ce
                    # cote de la colonne est un montant INTERMEDIAIRE
                    # (l'autre colonne peut aussi y percer depuis l'autre
                    # face, il faut degager pour ne pas se croiser).
                    _montage_coulisse_ici = values.get('tiroirs_montage_coulisse', 'eurovis')
                    if _montage_coulisse_ici == 'visser':
                        _prof_coulisse_g = mm_to_cm(1.0)
                        _prof_coulisse_d = mm_to_cm(1.0)
                    else:
                        _prof_coulisse_g = mm_to_cm(8.5 if bay_i > 0 else 11.5)
                        _prof_coulisse_d = mm_to_cm(
                            8.5 if bay_i < len(segments) - 1 else 11.5)
                    for _pos_mm in _positions_percage:
                        _y_percage = mm_to_cm(_pos_mm) + _decalage_percage_cm
                        holes.append((
                            'X', seg_x0, -1, _y_percage, _z_percage_coulisse,
                            DIAM_PERCAGE_COULISSE_CM, _prof_coulisse_g,
                            name.replace('Façade', 'Percage coulisse G {}mm'.format(_pos_mm))))
                        holes.append((
                            'X', seg_x1, 1, _y_percage, _z_percage_coulisse,
                            DIAM_PERCAGE_COULISSE_CM, _prof_coulisse_d,
                            name.replace('Façade', 'Percage coulisse D {}mm'.format(_pos_mm))))
                    # Traverses avant/arriere : entre les cotes (largeur
                    # interieure), posees SUR le fond (juste au-dessus de la
                    # rainure), 5mm plus courtes en hauteur que les cotes
                    # (marge sous leur propre haut). Remplacent l'ancien
                    # panneau arriere pleine hauteur (voir modele de
                    # reference 'Tiroir').
                    traverse_z0 = fond_z0 + Ef
                    traverse_z1 = (side_z0 + side_h) - 0.5
                    traverse_h = max(traverse_z1 - traverse_z0, 0.5)
                    panels.append((drawer_x0 + Ep, drawer_x1 - Ep, box_y0, box_y0 + Ep,
                                   traverse_z0, traverse_h,
                                   name.replace('Façade', 'Traverse avant'), None))
                    panels.append((drawer_x0 + Ep, drawer_x1 - Ep,
                                   box_y0 + drawer_depth_total - Ep, box_y0 + drawer_depth_total,
                                   traverse_z0, traverse_h,
                                   name.replace('Façade', 'Traverse arrière'), None))
                    panels.append((drawer_x0 + Ep - Ef, drawer_x1 - Ep + Ef,
                                   box_y0, box_y0 + drawer_depth_total,
                                   fond_z0, Ef, name.replace('Façade', 'Fond bas'), 'EpFond'))
                    grooves.append(('x', drawer_x0 + Ep, -1, box_y0, box_y0 + drawer_depth_total,
                                    fond_z0, fond_z0 + Ef, Ef, nom_cote_g + ' Rainure', nom_cote_g))
                    grooves.append(('x', drawer_x1 - Ep, 1, box_y0, box_y0 + drawer_depth_total,
                                    fond_z0, fond_z0 + Ef, Ef, nom_cote_d + ' Rainure', nom_cote_d))
                    # Masque automatiquement tout le percage systeme 32/64
                    # de cette colonne sur la hauteur occupee par ce tiroir
                    # (un tiroir prend la place, les trous n'y servent a
                    # rien et genent la coulisse).
                    if bay_i < len(p32_colonnes_candidates):
                        p32_colonnes_candidates[bay_i] = [
                            _cc for _cc in p32_colonnes_candidates[bay_i]
                            if not (face_z0 - 0.001 <= _cc <= face_z1 + 0.001)
                        ]

    # --- Perçages Lamello (assemblage par goujons Ø5mm) : à chaque jonction
    # montant/traverse, une paire de perçages avant/arrière (marge d'une
    # demi-épaisseur de panneau depuis la face avant et depuis le fond),
    # façon fichier de référence « C1 Montant Inter Portes Étagères ». Pour
    # les côtés (montants extérieurs), le perçage est fait dans le montant ;
    # pour chaque montant intermédiaire, il est fait dans la traverse.
    diam_lamello = mm_to_cm(LAMELLO_DIAM_MM)
    depth_lamello = max(0.0, min(mm_to_cm(LAMELLO_DEPTH_MM), Ep - mm_to_cm(LAMELLO_MARGE_SECURITE_MM)))
    if depth_lamello > 0:
        # 4 perçages par assemblage : les 2 perçages de bord habituels (marge
        # d'une demi-épaisseur de panneau depuis la face avant, et depuis le
        # fond) plus 2 perçages supplémentaires décalés de 101mm vers
        # l'intérieur depuis chacun de ces perçages de bord. Repli (avec
        # dédoublonnage) si le meuble est trop peu profond pour les caser tous
        # sans se chevaucher ou se croiser.
        front_margin = Ep / 2.0
        back_margin = interior_depth - Ep / 2.0
        decale = mm_to_cm(LAMELLO_DECALE_MM)
        front_decale = min(front_margin + decale, back_margin)
        back_decale = max(back_margin - decale, front_margin)
        positions = []
        seen = set()
        for y_center, tag in ((front_margin, 'avant'), (front_decale, 'avant décalé'),
                               (back_decale, 'arrière décalé'), (back_margin, 'arrière')):
            key = round(y_center, 4)
            if key not in seen:
                seen.add(key)
                positions.append((y_center, tag))
        for y_center, tag in positions:
            holes.append(('X', Ep, -1, y_center, Ep / 2.0, diam_lamello, depth_lamello,
                           'Lamello Montant G-Dessous {}'.format(tag)))
            holes.append(('X', Ep, -1, y_center, H - Ep / 2.0, diam_lamello, depth_lamello,
                           'Lamello Montant G-Dessus {}'.format(tag)))
            holes.append(('X', L - Ep, 1, y_center, Ep / 2.0, diam_lamello, depth_lamello,
                           'Lamello Montant D-Dessous {}'.format(tag)))
            holes.append(('X', L - Ep, 1, y_center, H - Ep / 2.0, diam_lamello, depth_lamello,
                           'Lamello Montant D-Dessus {}'.format(tag)))
            for mi, centre in enumerate(montant_centres, start=1):
                holes.append(('Z', Ep, -1, centre, y_center, diam_lamello, depth_lamello,
                               'Lamello Montant Inter {} Dessous {}'.format(mi, tag)))
                holes.append(('Z', H - Ep, 1, centre, y_center, diam_lamello, depth_lamello,
                               'Lamello Montant Inter {} Dessus {}'.format(mi, tag)))

        # --- Étagères fixe : mêmes perçages d'assemblage Lamello (avant /
        # avant décalé / arrière décalé / arrière) que Dessus/Dessous, mais à
        # l'axe (centre) de chaque étagère fixe plutôt qu'en haut/bas du
        # caisson. Comme pour le perçage système 32, la profondeur est pleine
        # sur une face extérieure (côté gauche/droit) et réduite de moitié
        # sur un montant intermédiaire partagé par deux compartiments, pour
        # que les deux perçages opposés ne se rejoignent jamais à l'intérieur
        # du panneau.
        marge_lamello_cm = mm_to_cm(LAMELLO_MARGE_SECURITE_MM)
        depth_lamello_partage = max(0.0, min(depth_lamello, (Ep - marge_lamello_cm) / 2.0))
        nb_segments_ef = len(segments)
        for bay_i, (seg_x0, seg_x1) in enumerate(segments):
            col_ef = (etageres_fixes_colonnes_norm[bay_i]
                      if bay_i < len(etageres_fixes_colonnes_norm) else {'hauteurs': []})
            depth_gauche_ef = depth_lamello if bay_i == 0 else depth_lamello_partage
            depth_droite_ef = depth_lamello if bay_i == nb_segments_ef - 1 else depth_lamello_partage
            for k, hauteur_mm in enumerate(col_ef['hauteurs'], start=1):
                z_centre_ef = mm_to_cm(hauteur_mm)
                for y_center, tag in positions:
                    if depth_gauche_ef > 0:
                        holes.append(('X', seg_x0, -1, y_center, z_centre_ef, diam_lamello, depth_gauche_ef,
                                       'Lamello Étagère fixe {} Compart {} Gauche {}'.format(
                                           k, bay_i + 1, tag)))
                    if depth_droite_ef > 0:
                        holes.append(('X', seg_x1, 1, y_center, z_centre_ef, diam_lamello, depth_droite_ef,
                                       'Lamello Étagère fixe {} Compart {} Droite {}'.format(
                                           k, bay_i + 1, tag)))

    # --- Perçage système 32 (taquets d'étagères réglables) : deux colonnes de
    # trous Ø5mm (une proche de l'avant, une proche du fond, symétriques par le
    # même retrait), dans chaque face de montant (extérieur ou intermédiaire)
    # qui délimite un compartiment d'étagère (« colonne ») dont le système
    # n'est pas Off — indépendant du nombre d'étagères réellement posées, pour
    # préparer le meuble à recevoir des étagères réglables sur toute sa
    # hauteur utile. Chaque colonne (une de plus que le nombre de montants
    # intermédiaires) a son propre système/masquage via
    # values['percage32_colonnes'] (p32_colonnes_candidates, calculé plus
    # haut) : ses DEUX faces (gauche et droite) suivent alors sa propre
    # grille, indépendamment des colonnes voisines qui peuvent partager un
    # même montant intermédiaire.
    diam_p32 = mm_to_cm(PERCAGE32_DIAM_MM)
    marge_p32_cm = mm_to_cm(LAMELLO_MARGE_SECURITE_MM)
    # Profondeur max sur une face extérieure (côté gauche/droit) : le perçage
    # est seul dans l'épaisseur du panneau, donc profondeur pleine.
    depth_p32_exterieur = max(0.0, min(mm_to_cm(PERCAGE32_DEPTH_MM), Ep - marge_p32_cm))
    # Profondeur sur un montant intermédiaire : deux compartiments percent ce
    # même montant, aux mêmes (y, z), depuis ses deux faces opposées. Il faut
    # donc que les deux perçages ne se rejoignent jamais à l'intérieur du
    # panneau, sinon la coupe Fusion échoue sur le second.
    depth_p32_partage = max(0.0, min(depth_p32_exterieur, (Ep - marge_p32_cm) / 2.0))
    retrait_p32 = mm_to_cm(values.get('percage32_retrait', 37))
    nb_segments = len(segments)
    # Colonne avant (retrait depuis la face avant) + colonne arrière (même
    # retrait, mesuré cette fois depuis le fond), dédoublonnées si le meuble
    # est trop peu profond pour les distinguer.
    positions_p32 = []
    seen_p32 = set()
    for y_center, tag in ((retrait_p32, 'avant'), (interior_depth - retrait_p32, 'arrière')):
        key = round(y_center, 4)
        if key not in seen_p32:
            seen_p32.add(key)
            positions_p32.append((y_center, tag))
    if depth_p32_exterieur > 0:
        for bay_i, (seg_x0, seg_x1) in enumerate(segments):
            col_candidates = (p32_colonnes_candidates[bay_i]
                               if bay_i < len(p32_colonnes_candidates) else [])
            depth_gauche = depth_p32_exterieur if bay_i == 0 else depth_p32_partage
            depth_droite = depth_p32_exterieur if bay_i == nb_segments - 1 else depth_p32_partage
            if col_candidates:
                for y_center, tag in positions_p32:
                    for k, z_k in enumerate(col_candidates):
                        if depth_gauche > 0:
                            holes.append(('X', seg_x0, -1, y_center, z_k, diam_p32, depth_gauche,
                                           'Perçage32 Compart {} Gauche {} {:02d}'.format(bay_i + 1, tag, k + 1)))
                        if depth_droite > 0:
                            holes.append(('X', seg_x1, 1, y_center, z_k, diam_p32, depth_droite,
                                           'Perçage32 Compart {} Droite {} {:02d}'.format(bay_i + 1, tag, k + 1)))
            # Trous ajoutes UNIQUEMENT pour une charniere (absents de la
            # grille normale, ex. masques ou colonne en systeme 64/Off) :
            # perces seulement cote charniere (avant, jamais vers le fond)
            # ET seulement sur la face du montant/cote ou se trouve
            # REELLEMENT cette charniere (pas les deux faces).
            extra_side = (p32_extra_side_only[bay_i]
                          if bay_i < len(p32_extra_side_only)
                          and values.get('portes_montage_embase', 'eurovis') != 'off'
                          else {})
            y_avant = retrait_p32
            # Profondeur specifique aux trous d'embase (differente de
            # depth_gauche/depth_droite, qui est celle des trous
            # systeme 32 normaux) : 'a visser' = toujours 1mm ; 'Eurovis'
            # = 11.5mm, ou 8.5mm sur un montant INTERMEDIAIRE (l'autre
            # colonne peut aussi y percer depuis l'autre face).
            _montage_embase_ici = values.get('portes_montage_embase', 'eurovis')
            if _montage_embase_ici == 'visser':
                _prof_embase_gauche = mm_to_cm(1.0)
                _prof_embase_droite = mm_to_cm(1.0)
            else:
                _prof_embase_gauche = mm_to_cm(8.5 if bay_i > 0 else 11.5)
                _prof_embase_droite = mm_to_cm(8.5 if bay_i < len(segments) - 1 else 11.5)
            _k_gauche = 0
            _k_droite = 0
            for z_k, sides in extra_side.items():
                if 'gauche' in sides and depth_gauche > 0:
                    _k_gauche += 1
                    holes.append(('X', seg_x0, -1, y_avant, z_k, diam_p32, _prof_embase_gauche,
                                   'Perçage32 Compart {} Gauche charnière {:02d}'.format(
                                       bay_i + 1, _k_gauche)))
                if 'droite' in sides and depth_droite > 0:
                    _k_droite += 1
                    holes.append(('X', seg_x1, 1, y_avant, z_k, diam_p32, _prof_embase_droite,
                                   'Perçage32 Compart {} Droite charnière {:02d}'.format(
                                       bay_i + 1, _k_droite)))

    # --- Décalage vertical final : tout ce qui précède est calculé dans un
    # repère où z=0 est le bas du caisson (haut du panneau du bas), le socle
    # descendant en dessous vers les z négatifs. Quand le socle est actif, le
    # bas du socle doit se trouver à Z0 : on translate donc tout vers le haut
    # de Soc (panneaux, portes, perçages) pour que le vrai plancher (bas du
    # socle, ou bas des côtés s'il n'y a pas de socle) tombe exactement à 0.
    if Soc:
        shifted_panels = []
        for p in panels:
            if p[0] == 'XZ':
                _, x0, x1, z0, z1, y_start, y_extent, name, thickness_param = p
                shifted_panels.append(('XZ', x0, x1, z0 + Soc, z1 + Soc, y_start, y_extent, name, thickness_param))
            else:
                x0, x1, y0, y1, z_start, z_extent, name, thickness_param = p
                shifted_panels.append((x0, x1, y0, y1, z_start + Soc, z_extent, name, thickness_param))
        panels = shifted_panels

        doors = [(x0_local, z0_local + Soc, largeur_cm, hauteur_cm, ep_cm, sens, dmode, hinges,
                   ouverte, offset_mm, pm_code, montage_code)
                 for (x0_local, z0_local, largeur_cm, hauteur_cm, ep_cm, sens, dmode, hinges,
                      ouverte, offset_mm, pm_code, montage_code) in doors]

        shifted_holes = []
        for h in holes:
            axis, plane, sign, u, v, diam, depth, name = h
            if axis == 'X':
                shifted_holes.append((axis, plane, sign, u, v + Soc, diam, depth, name))
            elif axis == 'Z':
                shifted_holes.append((axis, plane + Soc, sign, u, v, diam, depth, name))
            else:
                shifted_holes.append(h)
        holes = shifted_holes

        shifted_grooves = []
        for g in grooves:
            g_axis, x_plane, sign, y0, y1, z0, z1, depth, name, target = g
            shifted_grooves.append(
                (g_axis, x_plane, sign, y0, y1, z0 + Soc, z1 + Soc, depth, name, target))
        grooves = shifted_grooves

        shifted_pm = []
        for pm in prises_main:
            if pm[0] == 'x':
                _, x_plane, largeur, y_front, z_edge, sens, name, target = pm
                shifted_pm.append(
                    ('x', x_plane, largeur, y_front, z_edge + Soc, sens, name, target))
            else:
                _, z_plane, hauteur, y_front, x_edge, sens, name, target = pm
                shifted_pm.append(
                    ('z', z_plane + Soc, hauteur, y_front, x_edge, sens, name, target))
        prises_main = shifted_pm

    return {'panels': panels, 'doors': doors, 'holes': holes, 'grooves': grooves,
            'prises_main': prises_main, 'L': L, 'H': H, 'P': P}
