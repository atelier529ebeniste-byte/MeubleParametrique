import adsk.core
import adsk.fusion
import datetime
import json
import os
import re
import time
import traceback

# Numero de version affiche dans le dialogue (sous le logo, et dans
# le bloc Mise a jour). Format N.NN. A incrementer manuellement a
# chaque publication sur Drive/GitHub.
ADDIN_VERSION = '1.14'

app = None
ui = None
handlers = []

# Dossier contenant ce fichier .py, pour retrouver le dossier resources/ à côté
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

# Aperçu SVG live (Palette HTML) : id et chemin du fichier html,
# conserves tant que l'add-in tourne (necessaire pour retrouver/fermer
# la palette entre deux ouvertures du dialogue).
APERCU_PALETTE_ID = 'meubleApercuPalette'
# Chemin avec des '/' (pas des '\\') : Palettes.add construit une URL
# file:// a partir de cette chaine, et des antislashs Windows non
# convertis produisent une URL invalide (ERR_INVALID_URL).
APERCU_HTML_PATH = os.path.join(SCRIPT_DIR, 'apercu_meuble.html').replace('\\', '/')
apercu_handlers = []
RESOURCE_FOLDER_REFRESH = os.path.join(SCRIPT_DIR, 'resources', 'Refresh')
RESOURCE_FOLDER_SAVE_DEFAULT = os.path.join(SCRIPT_DIR, 'resources', 'EnregistrerDefaut')
RESOURCE_FOLDER_MEUBLE = os.path.join(SCRIPT_DIR, 'resources', 'MeubleParametrique')
RESOURCE_FOLDER_APPLIQUER = os.path.join(SCRIPT_DIR, 'resources', 'Appliquer')
RESOURCE_FOLDER_SUPPRIMER_PRESET = os.path.join(SCRIPT_DIR, 'resources', 'SupprimerPreset')
# Presets : ensemble de valeurs nomme, enregistrable/rechargeable depuis
# le dialogue (voir onglet Options avancees), meme principe que l'add-in
# MancheCompense (fichier JSON plat {nom: {cle: valeur, ...}}).
PRESETS_FILE = os.path.join(SCRIPT_DIR, 'presets.json')
NO_PRESET_LABEL = '(aucun)'
import sys
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Fusion ne vide de sys.modules que le module d'entree de l'add-in au
# Arreter/Executer, pas les modules importes ci-dessous : sans ce nettoyage
# explicite, un Arreter/Executer apres modification de ces fichiers
# recharge silencieusement l'ancienne version en cache (ou pire, echoue a
# l'import si un nom a change), au lieu de relire le disque.
for _mod_name in ('meuble_layout', 'meuble_geometry', 'meuble_persistence'):
    if _mod_name in sys.modules:
        del sys.modules[_mod_name]

# Modules extraits (voir meuble_layout.py / meuble_geometry.py /
# meuble_persistence.py) : calculs purs, creation de geometrie Fusion,
# et persistance JSON/attributs, respectivement.
from meuble_layout import (
    mm_to_cm, cm_to_mm, ETAGERES_MODES, TIROIRS_MODES, compute_layout,
    compute_etagere_z_starts, normalize_percage32_colonne,
    normalized_percage32_colonnes, normalize_etagere_colonne,
    normalized_etageres_colonnes, normalize_etagere_fixe_colonne,
    normalized_etageres_fixes_colonnes, normalize_porte_colonne,
    normalized_portes_colonnes, MeubleLayoutError,
)
from meuble_geometry import (
    add_panel_xy, add_panel_xz, drill_hole_x, drill_hole_z,
    drill_holes_batch, clear_component_geometry,
    THICKNESS_PARAM_DEFS, sanitize_param_prefix, ensure_meuble_parameters,
    build_meuble_body, build_door_component,
    GenerationCancelled, _tick,
)
from meuble_persistence import (
    DEFAULTS_FILE, ATTR_GROUP, ATTR_PARAMS, ATTR_DOORS, FIELDS_CAISSON,
    read_stored_values, load_saved_defaults, save_defaults_to_disk,
    default_values_dict,
)



# ---------------------------------------------------------------------------
# Constantes générales (mêmes panneaux/workspace que l'add-in Porte Inserta
# Blum, pour que les deux outils cohabitent naturellement dans le même onglet)
# ---------------------------------------------------------------------------

CMD_ID_CREATE = 'meubleParametriqueCreerCmd'
CMD_NAME_CREATE = 'Meuble Paramétrique'
CMD_TOOLTIP_CREATE = ("Créer ou modifier un meuble paramétrique (caisson, étagères, "
                       "portes, tiroirs) : choisir « Nouveau meuble » ou un meuble "
                       "existant dans la liste déroulante Meuble")

PANEL_ID_CREATE = 'SolidCreatePanel'
WORKSPACE_ID = 'FusionSolidEnvironment'

NOUVEAU_MEUBLE = 'Nouveau meuble'

MEUBLE_PREFIX = 'Meuble'
PORTE_PREFIX = 'Porte'          # identique à l'add-in PorteInsertaDialog

DEFAULT_GAP_MM = 100


# Plafond « garde-fou » du spinner numérique (pas une limite fonctionnelle :
# juste pour éviter une saisie absurde qui ferait planter la génération).
MAX_COMPTEUR = 999








def get_design():
    return adsk.fusion.Design.cast(app.activeProduct)


def load_presets():
    try:
        if os.path.isfile(PRESETS_FILE):
            with open(PRESETS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_presets(presets):
    try:
        with open(PRESETS_FILE, 'w', encoding='utf-8') as f:
            json.dump(presets, f)
    except Exception:
        pass


def apply_design_mode(design, mode):
    """Bascule le document dans le mode demandé ('parametrique' ou 'direct').
    Renvoie False si l'utilisateur annule un basculement destructeur."""
    target = (adsk.fusion.DesignTypes.DirectDesignType if mode == 'direct'
              else adsk.fusion.DesignTypes.ParametricDesignType)
    try:
        if design.designType == target:
            return True
        if target == adsk.fusion.DesignTypes.DirectDesignType and ui:
            result = ui.messageBox(
                "Passer en modélisation directe supprime définitivement tout "
                "l'historique de conception (plan de montage chronologique) de "
                "CE DOCUMENT, pas seulement de ce meuble. Continuer ?",
                "Basculer en modélisation directe",
                adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                adsk.core.MessageBoxIconTypes.WarningIconType)
            if result != adsk.core.DialogResults.DialogYes:
                return False
        design.designType = target
        return True
    except Exception:
        return True


    # Le choix Modelisation parametrique/directe a ete retire : le
    # meuble est toujours cree en parametrique (voir apply_design_mode,
    # appele avec mode='parametrique' fixe).


def group_timeline_range(design, start_index, group_name):
    try:
        timeline = design.timeline
        if not timeline:
            return
        end_index = timeline.count - 1
        if end_index < start_index:
            return
        group = timeline.timelineGroups.add(start_index, end_index)
        if group:
            group.name = group_name
    except Exception:
        pass


def add_value_field(container, field_id, label, default_cm, min_mm, max_mm):
    v = container.addValueInput(
        field_id, label, 'mm',
        adsk.core.ValueInput.createByReal(default_cm))
    v.isMinimumLimited = True
    v.minimumValue = mm_to_cm(min_mm)
    v.isMaximumLimited = True
    v.maximumValue = mm_to_cm(max_mm)
    return v


# ---------------------------------------------------------------------------
# Nommage et positionnement des composants de premier niveau
# ---------------------------------------------------------------------------

def next_component_name(root, prefix):
    existing = set()
    for i in range(root.occurrences.count):
        existing.add(root.occurrences.item(i).component.name)
    n = 1
    while True:
        candidate = '{} {:02d}'.format(prefix, n)
        if candidate not in existing:
            return candidate
        n += 1


def next_x_offset_cm(root, prefix):
    max_x_cm = None
    for i in range(root.occurrences.count):
        occ = root.occurrences.item(i)
        if occ.component.name.startswith(prefix + ' '):
            bbox = occ.boundingBox
            if bbox:
                if max_x_cm is None or bbox.maxPoint.x > max_x_cm:
                    max_x_cm = bbox.maxPoint.x
    return max_x_cm


def list_existing(root, prefix):
    items = []
    for i in range(root.occurrences.count):
        occ = root.occurrences.item(i)
        if occ.component.name.startswith(prefix + ' '):
            items.append(occ)
    items.sort(key=lambda o: o.component.name)
    return items








def generate_meuble(root, design, values, meuble_comp, meuble_transform, progress=None):
    """Construit (dans un composant déjà créé et vide) le meuble complet, crée
    les portes de premier niveau associées, et enregistre les attributs de
    suivi (paramètres + portes liées) sur le composant du meuble.
    meuble_transform est le transform (position/orientation au monde) RÉEL de
    l'occurrence du caisson : pour un nouveau meuble, celui qui vient d'être
    posé (translation par défaut) ; pour un meuble existant modifié, son
    transform actuel (target_occ.transform), qui reflète tout déplacement
    manuel effectué depuis Fusion — les portes sont alors recréées à la bonne
    place, alignées sur le caisson quelle que soit sa position."""
    param_prefix = sanitize_param_prefix(meuble_comp.name)
    thickness_params = ensure_meuble_parameters(design, param_prefix, values)

    doors_local = build_meuble_body(meuble_comp, values, thickness_params, progress)

    # Portes creees IMBRIQUEES dans meuble_comp (voir build_door_component) :
    # la numerotation Porte NN est donc unique par meuble (scope
    # meuble_comp.occurrences), pas globale au document.
    door_names = []
    base_step = progress.progressValue if progress is not None else 0
    for i, spec in enumerate(doors_local):
        name = next_component_name(meuble_comp, PORTE_PREFIX)
        door_occ = build_door_component(meuble_comp, spec, name, thickness_params.get('EpPorte'))
        # Fusion garantit l'unicite des noms de Component sur TOUT le
        # document (pas seulement au sein de meuble_comp) : si 'name' est
        # deja pris ailleurs (autre meuble, reliquat...), Fusion renomme
        # silencieusement (ex. 'Porte 01 (2)'). On retient donc le nom REEL
        # pour que delete_linked_doors retrouve toujours la bonne porte.
        actual_name = door_occ.component.name
        door_names.append(actual_name)
        _tick(progress, base_step + i + 1, actual_name)

    try:
        meuble_comp.attributes.add(ATTR_GROUP, ATTR_PARAMS, json.dumps(values))
        meuble_comp.attributes.add(ATTR_GROUP, ATTR_DOORS, ','.join(door_names))
    except Exception:
        pass


def delete_linked_doors(meuble_comp):
    # Les portes sont imbriquees dans meuble_comp (voir generate_meuble /
    # build_door_component) : on les cherche donc dans ses propres
    # occurrences, plus au niveau racine.
    attr = meuble_comp.attributes.itemByName(ATTR_GROUP, ATTR_DOORS)
    if not attr or not attr.value:
        return
    names = [n for n in attr.value.split(',') if n]
    for i in range(meuble_comp.occurrences.count - 1, -1, -1):
        occ = meuble_comp.occurrences.item(i)
        if occ.component.name in names:
            occ.deleteMe()




def apply_meuble_selection(inputs, override_values=None):
    """Recharge tous les champs de la boîte de dialogue selon la sélection
    courante de la liste déroulante Meuble : valeurs par défaut si « Nouveau
    meuble », sinon les valeurs mémorisées du meuble existant choisi (pour le
    modifier). Si 'override_values' est fourni (application d'un preset,
    voir onglet Options avancées), l'utilise directement au lieu de
    determiner les valeurs depuis la selection Meuble."""
    if override_values is not None:
        values = default_values_dict()
        values.update(override_values)
    else:
        dd = inputs.itemById('dropdownMeuble')
        if not (dd and dd.selectedItem):
            return
        name = dd.selectedItem.name

        if name == NOUVEAU_MEUBLE:
            values = default_values_dict()
        else:
            design = get_design()
            root = design.rootComponent if design else None
            target = None
            if root:
                for occ in list_existing(root, MEUBLE_PREFIX):
                    if occ.component.name == name:
                        target = occ.component
                        break
            if not target:
                return
            values = read_stored_values(target, default_values_dict())

    for field_id, key, _default_mm, _min, _max, _label in FIELDS_CAISSON:
        ci = inputs.itemById(field_id)
        if ci and key in values:
            ci.value = mm_to_cm(values[key])
    for field_id, key in (
            ('champSocle', 'socle'),
            ('champRetraitEtagere', 'retrait_etagere'),
            ('champRetraitMontant', 'retrait_montant'),
            ('champEpMontant', 'ep_montant'),
            ('champRetraitEtagereFixe', 'retrait_etagere_fixe'),
            ('champEpEtagereFixe', 'ep_etagere_fixe'),
            ('champEpEtagereMobile', 'ep_etagere_mobile'),
            ('champRetraitPlinthe', 'retrait_plinthe'),
            ('champPercage32Retrait', 'percage32_retrait'),
            ('champPercage32MargeBas', 'percage32_marge_bas'),
            ('champJeuPorte', 'jeu_porte'),
            ('champEpPorte', 'ep_porte'),
            ('champCharniereAxeBasse', 'charniere_axe_basse'),
            ('champCharniereAxeHaute', 'charniere_axe_haute'),
            ('champJeuTiroir', 'jeu_tiroir'),
            ('champEpFaceTiroir', 'ep_face_tiroir'),
            ('champEpFondTiroir', 'ep_fond_tiroir'),
            ('champEpPanneauTiroir', 'ep_panneau_tiroir'),
            ('champRetraitPercageCoulisse', 'retrait_percage_coulisse')):
        ci = inputs.itemById(field_id)
        if ci and key in values:
            ci.value = mm_to_cm(values[key])
    chk_socle = inputs.itemById('checkSocleActif')
    if chk_socle and 'socle_actif' in values:
        chk_socle.value = bool(values['socle_actif'])
    chk_onglet = inputs.itemById('checkCoupeOnglet')
    if chk_onglet and 'coupe_onglet' in values:
        chk_onglet.value = bool(values['coupe_onglet'])
    chk_charniere_auto = inputs.itemById('checkCharniereAuto')
    if chk_charniere_auto:
        chk_charniere_auto.value = bool(values.get('charniere_auto', True))
    for field_id, key in (
            ('intNbMontants', 'nb_montants'),
            ('intCharniereNbInter', 'charniere_nb_inter')):
        ci = inputs.itemById(field_id)
        if ci and key in values:
            ci.value = int(values[key])
    tab_m = inputs.itemById('tabMontants')
    dd_mode_m = inputs.itemById('dropdownMontantsMode')
    if dd_mode_m:
        mode_label = {'axe_egal': 'Axe égal',
                      'largeur_colonne': 'Intérieur Colonne',
                      'personnalise': 'Personnalisé'}.get(
            values.get('montants_mode', 'axe_egal'), 'Axe égal')
        for li in dd_mode_m.listItems:
            li.isSelected = (li.name == mode_label)
    if tab_m:
        rebuild_montant_axis_fields(
            tab_m.children, values.get('nb_montants', 1),
            values.get('largeur', 1000), values.get('montants'),
            values.get('montants_mode', 'axe_egal'), values.get('ep_panneau', 19))
    nb_colonnes = values.get('nb_montants', 1) + 1
    dd_mode_ef = inputs.itemById('dropdownEtageresFixeMode')
    if dd_mode_ef:
        mode_ef_label = {'axe_egal': 'Axe égal',
                         'hauteur_colonne': 'Intérieur niche',
                         'personnalise': 'Personnalisé'}.get(
            values.get('etageres_fixe_mode', 'hauteur_colonne'), 'Intérieur niche')
        for li in dd_mode_ef.listItems:
            li.isSelected = (li.name == mode_ef_label)
    group_ef = inputs.itemById('groupEtageresFixeColonnes')
    if group_ef:
        rebuild_etageres_fixe_colonne_groups(
            group_ef.children, nb_colonnes,
            normalized_etageres_fixes_colonnes(values, nb_colonnes),
            hauteur_mm_max=(values.get('hauteur', 1200)
                            - (values.get('socle', 0) if values.get('socle_actif', True) else 0)),
            ep_panneau_mm=values.get('ep_panneau', 19),
            mode=values.get('etageres_fixe_mode', 'hauteur_colonne'))
    group_p32 = inputs.itemById('groupPercage32Colonnes')
    if group_p32:
        rebuild_percage32_tables(
            group_p32.children, nb_colonnes, values.get('percage32_colonnes'), inputs=inputs)
    group_etageres = inputs.itemById('groupEtageresColonnes')
    if group_etageres:
        rebuild_etageres_tables(
            group_etageres.children, nb_colonnes,
            values.get('etageres_colonnes'), inputs=inputs)
    dd_mode_portes = inputs.itemById('dropdownPortesMode')
    if dd_mode_portes:
        target_label = ('Encastré' if values.get('portes_mode') == 'encastre'
                        else 'En applique')
        for li in dd_mode_portes.listItems:
            li.isSelected = (li.name == target_label)
    dd_montage_portes = inputs.itemById('dropdownPortesMontage')
    if dd_montage_portes:
        _mp_val = values.get('portes_montage')
        target_label_m = ('Off' if _mp_val == 'off'
                          else 'À visser' if _mp_val == 'visser'
                          else 'Inserta/à frapper')
        for li in dd_montage_portes.listItems:
            li.isSelected = (li.name == target_label_m)
    dd_montage_embase = inputs.itemById('dropdownPortesMontageEmbase')
    if dd_montage_embase:
        _me_val = values.get('portes_montage_embase')
        target_label_e = ('Off' if _me_val == 'off'
                          else 'À visser' if _me_val == 'visser'
                          else 'Eurovis')
        for li in dd_montage_embase.listItems:
            li.isSelected = (li.name == target_label_e)
    group_portes = inputs.itemById('groupPortesColonnes')
    if group_portes:
        rebuild_portes_tables(
            group_portes.children, nb_colonnes,
            values.get('portes_colonnes'), inputs=inputs)
    dd_mode_tiroirs = inputs.itemById('dropdownTiroirsMode')
    if dd_mode_tiroirs:
        target_label_t = ('Encastré' if values.get('tiroirs_mode') == 'encastre'
                          else 'En applique')
        for li in dd_mode_tiroirs.listItems:
            li.isSelected = (li.name == target_label_t)
    dd_montage_coulisse = inputs.itemById('dropdownTiroirsMontageCoulisse')
    if dd_montage_coulisse:
        target_label_c = ('À visser' if values.get('tiroirs_montage_coulisse') == 'visser'
                          else 'Eurovis')
        for li in dd_montage_coulisse.listItems:
            li.isSelected = (li.name == target_label_c)
    group_tiroirs = inputs.itemById('groupTiroirsColonnes')
    if group_tiroirs:
        rebuild_tiroirs_tables(
            group_tiroirs.children, nb_colonnes,
            values.get('tiroirs_colonnes'), inputs=inputs,
            portes_colonnes=values.get('portes_colonnes'))
    tab_prise_main = inputs.itemById('tabPriseMain')
    if tab_prise_main:
        rebuild_prise_main_table(
            tab_prise_main.children, nb_colonnes,
            values.get('portes_colonnes'), values.get('tiroirs_colonnes'),
            values.get('prise_main_portes'), values.get('prise_main_tiroirs'),
            inputs=inputs)
    update_field_visibility(inputs)


# ---------------------------------------------------------------------------
# Champs dynamiques « Axe montant NN » (un par montant intermédiaire)
# ---------------------------------------------------------------------------

def count_montant_fields(children):
    """Compte combien de paires champAxeMontantNN existent déjà dans cette
    collection (numérotation continue sans trou)."""
    i = 1
    while children.itemById('champAxeMontant{:02d}'.format(i)) is not None:
        i += 1
    return i - 1


def delete_children_by_prefix(children, prefix):
    # Supprime TOUS les enfants directs de 'children' dont l'id commence par
    # 'prefix', en scannant la collection reelle (par index, de la fin vers
    # le debut) plutot qu'en devinant des id numerotes 01, 02... de facon
    # continue : plus robuste si un trou ou un reliquat existe dans la
    # numerotation (source de volets fantomes constatee en pratique).
    for j in range(children.count - 1, -1, -1):
        item = children.item(j)
        try:
            item_id = item.id
        except Exception:
            continue
        if item_id and item_id.startswith(prefix):
            item.deleteMe()


def rebuild_montant_axis_fields(children, count, largeur_mm, existing_montants=None,
                                mode='axe_egal', ep_panneau_mm=19):
    # Supprime tous les champs Axe montant NN / Reference NN, puis les
    # recree pour exactement 'count' montants. Le mode choisi (voir
    # dropdownMontantsMode) determine comment :
    #  - 'axe_egal'        : axes espaces egalement (Largeur / (count+1) x i),
    #                        recalcule TOUJOURS, sans tenir compte de saisies
    #                        manuelles precedentes (mode 100% automatique).
    #  - 'largeur_colonne' : largeur interieure des colonnes egale (tient
    #                        compte de l'epaisseur des montants), recalcule
    #                        TOUJOURS lui aussi.
    #  - 'personnalise'    : ne recalcule RIEN pour les montants deja
    #                        existants (existing_montants fait foi), seuls
    #                        les montants tout juste ajoutes recoivent un
    #                        defaut (formule axe_egal) en attendant que
    #                        l'utilisateur les ajuste.
    delete_children_by_prefix(children, 'champAxeMontant')
    delete_children_by_prefix(children, 'dropdownRefMontant')
    adsk.doEvents()

    if mode == 'largeur_colonne' and count > 0:
        col_w = max((largeur_mm - ep_panneau_mm * (count + 2)) / (count + 1), 0)
    else:
        col_w = 0
    # Axe egal : entraxe uniforme SAUF si plus de 2 colonnes (count >= 2
    # montants), auquel cas l'entraxe des colonnes INTERIEURES (entre 2
    # montants) vaut l'entraxe des colonnes EXTERIEURES (contre un cote)
    # + une demi-epaisseur de panneau -- pour harmoniser la largeur des
    # portes en applique (qui perdent une demi-epaisseur de moins cote
    # exterieur, contre le cote, que cote interieur, entre 2 montants).
    pas_defaut = largeur_mm / (count + 1) if count > 0 else 0

    def _axe_formule(i):
        if mode == 'largeur_colonne':
            return i * (col_w + ep_panneau_mm) + ep_panneau_mm / 2.0
        return i * pas_defaut

    for i in range(1, count + 1):
        has_existing = existing_montants and i <= len(existing_montants)
        if mode == 'personnalise' and has_existing:
            axe_defaut = existing_montants[i - 1].get('axe', _axe_formule(i))
            ref_defaut = existing_montants[i - 1].get('ref', 'gauche')
        elif mode == 'personnalise':
            axe_defaut = _axe_formule(i)
            ref_defaut = 'gauche'
        else:
            # Modes automatiques : recalcule toujours, ignore les saisies
            # manuelles precedentes (choisir un de ces modes signifie
            # explicitement re-agencer tous les montants selon la regle).
            axe_defaut = _axe_formule(i)
            ref_defaut = 'gauche'
        add_value_field(children, 'champAxeMontant{:02d}'.format(i),
                         'Axe montant {:02d}'.format(i), mm_to_cm(axe_defaut), 0, max(largeur_mm, 3000))
        dd_ref = children.addDropDownCommandInput(
            'dropdownRefMontant{:02d}'.format(i), 'Référence montant {:02d}'.format(i),
            adsk.core.DropDownStyles.TextListDropDownStyle)
        dd_ref.listItems.add('Extérieur gauche', ref_defaut != 'droite')
        dd_ref.listItems.add('Extérieur droit', ref_defaut == 'droite')


def get_nb_niches_colonne(inputs, col_idx):
    # Nombre de niches de la colonne col_idx = son nombre d'etageres fixe
    # + 1, lu directement depuis le volet Etageres fixe (source de verite
    # unique du decoupage en niches). Recherche depuis la racine
    # (inputs.itemById), pas depuis group_ef.children : ce compteur est
    # maintenant imbrique dans une cellule de tableau (en-tete Etageres
    # fixe), et seule la recherche recursive depuis la racine le
    # retrouve de facon fiable.
    int_nb = inputs.itemById('intEtageresFixeColonne{:02d}NbEtageres'.format(col_idx))
    if not int_nb:
        return 1
    return int(int_nb.value) + 1


def rebuild_percage32_tables(children, count, existing_colonnes=None, inputs=None):
    # Un tableau SEPARE par colonne (pas un seul tableau global) : un
    # mini-tableau d'en-tete a 1 ligne au-dessus (Colonne NN / Percage /
    # Masquer haut / Masquer bas, alignes sur les memes colonnes que le
    # tableau de donnees juste en dessous), puis ce tableau de donnees
    # sans quadrillage avec une ligne par niche (ou une seule ligne si la
    # colonne n'en a qu'une). Supprime d'abord tous les anciens
    # (en-tete + donnees), puis recree tout.
    delete_children_by_prefix(children, 'tableP32Col')
    delete_children_by_prefix(children, 'textP32ColCaption')
    adsk.doEvents()

    for i in range(1, count + 1):
        if existing_colonnes and i <= len(existing_colonnes):
            col = existing_colonnes[i - 1]
        else:
            col = {'systeme': '32', 'masquer_bas': 0, 'masquer_haut': 0}
        nb_niches = get_nb_niches_colonne(inputs, i) if inputs is not None else 1
        niches_brutes = col if isinstance(col, list) else []
        col_flat = (col if not isinstance(col, list)
                    else niches_brutes[0] if niches_brutes
                    else {'systeme': '32', 'masquer_bas': 0, 'masquer_haut': 0})

        header = children.addTableCommandInput(
            'tableP32Col{:02d}Header'.format(i), '', 5, '2:2:2:2:2')
        header.hasGrid = False
        header.minimumVisibleRows = 1
        for col_idx, texte in enumerate((
                'Colonne {:02d}'.format(i), 'Perçage', 'Masquer haut', 'Masquer bas',
                '3 Trous')):
            cell = header.commandInputs.addStringValueInput(
                'tableP32Col{:02d}HeaderCell{}'.format(i, col_idx), '', texte)
            cell.isReadOnly = True
            header.addCommandInput(cell, 0, col_idx)

        table = children.addTableCommandInput(
            'tableP32Col{:02d}'.format(i), '', 5, '2:2:2:2:2')
        table.hasGrid = False

        if nb_niches <= 1:
            entries = [('—', col_flat)]
        else:
            entries = []
            for k in range(1, nb_niches + 1):
                niche = (niches_brutes[k - 1] if k - 1 < len(niches_brutes)
                         else {'systeme': '32', 'masquer_bas': 0, 'masquer_haut': 0})
                entries.append(('Niche {:02d}'.format(k), niche))
        table.maximumVisibleRows = max(len(entries), 2)

        for row, (niche_label, entry) in enumerate(entries):
            systeme = entry.get('systeme', '32')
            lbl = table.commandInputs.addStringValueInput(
                'tableP32Col{:02d}Label{}'.format(i, row), '', niche_label)
            lbl.isReadOnly = True
            dd = table.commandInputs.addDropDownCommandInput(
                'tableP32Col{:02d}Systeme{}'.format(i, row), '',
                adsk.core.DropDownStyles.TextListDropDownStyle)
            dd.listItems.add('Off', systeme == 'off')
            dd.listItems.add('32', systeme == '32')
            dd.listItems.add('64', systeme == '64')
            sp_haut = table.commandInputs.addIntegerSpinnerCommandInput(
                'tableP32Col{:02d}Haut{}'.format(i, row), '',
                0, MAX_COMPTEUR, 1, int(entry.get('masquer_haut', 0)))
            sp_bas = table.commandInputs.addIntegerSpinnerCommandInput(
                'tableP32Col{:02d}Bas{}'.format(i, row), '',
                0, MAX_COMPTEUR, 1, int(entry.get('masquer_bas', 0)))
            # '3 Trous' : masque tous les trous sauf ceux utilises par une
            # etagere mobile de cette niche (+1 au-dessus, +1 en dessous).
            # Les perÃ§ages de charnieres ne sont jamais concernes.
            chk_3trous = table.commandInputs.addBoolValueInput(
                'tableP32Col{:02d}TroisTrous{}'.format(i, row), '', True,
                '', bool(entry.get('trois_trous', False)))
            table.addCommandInput(lbl, row, 0)
            table.addCommandInput(dd, row, 1)
            table.addCommandInput(sp_haut, row, 2)
            table.addCommandInput(sp_bas, row, 3)
            table.addCommandInput(chk_3trous, row, 4)


def rebuild_portes_tables(children, count, existing_colonnes=None, inputs=None):
    # Meme principe que rebuild_percage32_tables : en-tete aligne
    # (Colonne NN / Portes / Ouverture 110), puis le tableau de donnees
    # (Portes + Ouverture 110, chacune INDEPENDANTE par niche) sans
    # quadrillage, une ligne par colonne simple ou par niche.
    delete_children_by_prefix(children, 'tablePortesCol')
    adsk.doEvents()

    for i in range(1, count + 1):
        if existing_colonnes and i <= len(existing_colonnes):
            col = existing_colonnes[i - 1]
        else:
            col = {'choix': 'off'}
        nb_niches = get_nb_niches_colonne(inputs, i) if inputs is not None else 1
        niches_brutes = col if isinstance(col, list) else []
        col_flat = (col if not isinstance(col, list)
                    else niches_brutes[0] if niches_brutes
                    else {'choix': 'off'})

        header = children.addTableCommandInput(
            'tablePortesCol{:02d}Header'.format(i), '', 3, '2:2:2')
        header.hasGrid = False
        header.minimumVisibleRows = 1
        for col_idx, texte in enumerate((
                'Colonne {:02d}'.format(i), 'Portes', 'Ouverture 110°')):
            cell = header.commandInputs.addStringValueInput(
                'tablePortesCol{:02d}HeaderCell{}'.format(i, col_idx), '', texte)
            cell.isReadOnly = True
            header.addCommandInput(cell, 0, col_idx)

        table = children.addTableCommandInput(
            'tablePortesCol{:02d}'.format(i), '', 3, '2:2:2')
        table.hasGrid = False

        if nb_niches <= 1:
            entries = [('—', col_flat)]
        else:
            entries = []
            for k in range(1, nb_niches + 1):
                niche = niches_brutes[k - 1] if k - 1 < len(niches_brutes) else {'choix': 'off'}
                entries.append(('Niche {:02d}'.format(k), niche))
        table.maximumVisibleRows = max(len(entries), 2)

        for row, (niche_label, entry) in enumerate(entries):
            choix_n = entry.get('choix', 'off')
            lbl = table.commandInputs.addStringValueInput(
                'tablePortesCol{:02d}Label{}'.format(i, row), '', niche_label)
            lbl.isReadOnly = True
            dd = table.commandInputs.addDropDownCommandInput(
                'tablePortesCol{:02d}Choix{}'.format(i, row), '',
                adsk.core.DropDownStyles.TextListDropDownStyle)
            dd.listItems.add('Off', choix_n == 'off')
            dd.listItems.add('Gauche', choix_n == 'gauche')
            dd.listItems.add('Droite', choix_n == 'droite')
            dd.listItems.add('2 Portes', choix_n == '2portes')
            # Chaque niche peut s'ouvrir independamment des autres.
            chk_o = table.commandInputs.addBoolValueInput(
                'tablePortesCol{:02d}Ouverte{}'.format(i, row), '', True,
                '', bool(entry.get('ouverte', False)))
            table.addCommandInput(lbl, row, 0)
            table.addCommandInput(dd, row, 1)
            table.addCommandInput(chk_o, row, 2)


def rebuild_etageres_tables(children, count, existing_colonnes=None, inputs=None):
    # Meme principe que rebuild_percage32_tables : un mini-tableau
    # d'en-tete (Colonne NN / Nombre d'etageres / Mode de calcul) puis un
    # tableau de donnees sans quadrillage, une ligne par colonne simple ou
    # par niche.
    delete_children_by_prefix(children, 'tableEtgCol')
    adsk.doEvents()

    for i in range(1, count + 1):
        if existing_colonnes and i <= len(existing_colonnes):
            col = existing_colonnes[i - 1]
        else:
            col = {'nb_etageres': 0, 'mode': 'hauteur_colonne'}
        nb_niches = get_nb_niches_colonne(inputs, i) if inputs is not None else 1
        niches_brutes = col if isinstance(col, list) else []
        col_flat = (col if not isinstance(col, list)
                    else niches_brutes[0] if niches_brutes
                    else {'nb_etageres': 0, 'mode': 'hauteur_colonne'})

        header = children.addTableCommandInput(
            'tableEtgCol{:02d}Header'.format(i), '', 3, '2:2:2')
        header.hasGrid = False
        header.minimumVisibleRows = 1
        for col_idx, texte in enumerate((
                'Colonne {:02d}'.format(i), "Nombre d'étagères", 'Mode de calcul')):
            cell = header.commandInputs.addStringValueInput(
                'tableEtgCol{:02d}HeaderCell{}'.format(i, col_idx), '', texte)
            cell.isReadOnly = True
            header.addCommandInput(cell, 0, col_idx)

        table = children.addTableCommandInput(
            'tableEtgCol{:02d}'.format(i), '', 3, '2:2:2')
        table.hasGrid = False

        if nb_niches <= 1:
            entries = [('—', col_flat)]
        else:
            entries = []
            for k in range(1, nb_niches + 1):
                niche = (niches_brutes[k - 1] if k - 1 < len(niches_brutes)
                         else {'nb_etageres': 0, 'mode': 'hauteur_colonne'})
                entries.append(('Niche {:02d}'.format(k), niche))
        table.maximumVisibleRows = max(len(entries), 2)

        for row, (niche_label, entry) in enumerate(entries):
            lbl = table.commandInputs.addStringValueInput(
                'tableEtgCol{:02d}Label{}'.format(i, row), '', niche_label)
            lbl.isReadOnly = True
            sp_nb = table.commandInputs.addIntegerSpinnerCommandInput(
                'tableEtgCol{:02d}Nb{}'.format(i, row), '',
                0, MAX_COMPTEUR, 1, int(entry.get('nb_etageres', 0)))
            dd = table.commandInputs.addDropDownCommandInput(
                'tableEtgCol{:02d}Mode{}'.format(i, row), '',
                adsk.core.DropDownStyles.TextListDropDownStyle)
            mode_actuel = entry.get('mode', 'hauteur_colonne')
            if mode_actuel not in dict(ETAGERES_MODES):
                mode_actuel = 'hauteur_colonne'
            for code, label in ETAGERES_MODES:
                dd.listItems.add(label, mode_actuel == code)
            table.addCommandInput(lbl, row, 0)
            table.addCommandInput(sp_nb, row, 1)
            table.addCommandInput(dd, row, 2)


def refresh_tiroirs_niche_detail(inputs, col_i, niche_idx):
    """Reconstruit les tableaux 3 (Tiroir NN / hauteur) de TOUTES les
    niches de la colonne col_i (pas seulement niche_idx), sans jamais
    toucher aux tableaux 1/2 (dont celui qui contient le controle
    Nombre de tiroirs/Mode qui vient de declencher cet appel). Il faut
    TOUS les reconstruire, pas seulement celui qui a change : Fusion
    ajoute toujours un nouveau TableCommandInput a la FIN de la liste
    des champs, jamais a sa position logique -- si on ne recreait que
    celui d'une niche du milieu, il se retrouverait affiche apres tous
    les autres, cassant l'ordre visuel Niche 01/ses tiroirs/Niche 02/
    ses tiroirs/... Reconstruire tous les tableaux 3 de la colonne, dans
    l'ordre, retablit systematiquement le bon ordre."""
    group_tiroirs = inputs.itemById('groupTiroirsColonnes')
    if not group_tiroirs:
        return
    nb_niches = get_nb_niches_colonne(inputs, col_i)
    # 1) Lit l'etat actuel (Nb/Mode/hauteurs deja saisies) de TOUTES les
    #    niches de cette colonne AVANT de rien supprimer.
    etats = []
    for n_idx in range(nb_niches):
        sp_nb = inputs.itemById('tableTirCol{:02d}Nb{}'.format(col_i, n_idx))
        dd = inputs.itemById('tableTirCol{:02d}Mode{}'.format(col_i, n_idx))
        if not sp_nb or not dd:
            etats.append((0, 'hauteur_niche', []))
            continue
        nb_t = int(sp_nb.value)
        mode = 'hauteur_niche'
        if dd.selectedItem:
            for code, label in TIROIRS_MODES:
                if label == dd.selectedItem.name:
                    mode = code
                    break
        hauteurs = []
        capacites = []
        for tk in range(nb_t):
            v = inputs.itemById('tableTirCol{:02d}TirHauteur{}_{}'.format(col_i, n_idx, tk))
            hauteurs.append(cm_to_mm(v.value) if v else 100)
            dd_c = inputs.itemById('tableTirCol{:02d}TirCap{}_{}'.format(col_i, n_idx, tk))
            cap = 50 if (dd_c and dd_c.selectedItem and dd_c.selectedItem.name == '50 kg') else 30
            capacites.append(cap)
        etats.append((nb_t, mode, hauteurs, capacites))
    # 2) Supprime TOUS les tableaux 3 existants de cette colonne.
    for n_idx in range(nb_niches):
        old_t = inputs.itemById('tableTirCustomCol{:02d}Niche{:02d}'.format(col_i, n_idx))
        if old_t:
            old_t.deleteMe()
    adsk.doEvents()
    # 3) Recree, DANS L'ORDRE, un tableau 3 pour chaque niche encore en
    #    mode Personnalise (et met a jour l'indicateur 'Hauteur facade'
    #    de chaque niche au passage, sans toucher au reste du tableau 2).
    for n_idx in range(nb_niches):
        nb_t, mode, hauteurs_existantes, capacites_existantes = etats[n_idx]
        if mode != 'personnalise' or nb_t <= 0:
            continue
        tir_table = group_tiroirs.children.addTableCommandInput(
            'tableTirCustomCol{:02d}Niche{:02d}'.format(col_i, n_idx), '', 4, '2:1:2:2')
        tir_table.hasGrid = False
        tir_table.maximumVisibleRows = max(nb_t, 2)
        for tk in range(nb_t):
            lbl_t = tir_table.commandInputs.addStringValueInput(
                'tableTirCol{:02d}TirLabel{}_{}'.format(col_i, n_idx, tk),
                '', 'Tiroir {:02d}'.format(tk + 1))
            lbl_t.isReadOnly = True
            hint_t = tir_table.commandInputs.addStringValueInput(
                'tableTirCol{:02d}TirHint{}_{}'.format(col_i, n_idx, tk),
                '', 'Hauteur façade')
            hint_t.isReadOnly = True
            hv = hauteurs_existantes[tk] if tk < len(hauteurs_existantes) else 100
            val_h = tir_table.commandInputs.addValueInput(
                'tableTirCol{:02d}TirHauteur{}_{}'.format(col_i, n_idx, tk),
                '', 'mm', adsk.core.ValueInput.createByReal(mm_to_cm(hv)))
            cap_actuelle = capacites_existantes[tk] if tk < len(capacites_existantes) else 30
            dd_cap = tir_table.commandInputs.addDropDownCommandInput(
                'tableTirCol{:02d}TirCap{}_{}'.format(col_i, n_idx, tk), '',
                adsk.core.DropDownStyles.TextListDropDownStyle)
            dd_cap.listItems.add('30 kg', cap_actuelle != 50)
            dd_cap.listItems.add('50 kg', cap_actuelle == 50)
            tir_table.addCommandInput(lbl_t, tk, 0)
            tir_table.addCommandInput(hint_t, tk, 1)
            tir_table.addCommandInput(val_h, tk, 2)
            tir_table.addCommandInput(dd_cap, tk, 3)


def _niche_a_porte(portes_colonnes, col_i, niche_idx0):
    """Renvoie True si la niche 'niche_idx0' (index 0, MEME ordre brut
    que les tableaux Colonnes -- Niche 01 = index 0) de la colonne
    'col_i' (1-indexee) a une porte active (choix != 'off'). Sert a
    interdire les tiroirs dans une niche qui a deja une porte."""
    if not portes_colonnes or col_i > len(portes_colonnes):
        return False
    col = portes_colonnes[col_i - 1]
    niches = col if isinstance(col, list) else ([col] if col else [])
    if niche_idx0 >= len(niches):
        return False
    entry = niches[niche_idx0]
    if not isinstance(entry, dict):
        return False
    return entry.get('choix', 'off') != 'off'


def rebuild_tiroirs_tables(children, count, existing_colonnes=None, inputs=None,
                            portes_colonnes=None):
    # Un tableau INDEPENDANT a chaque niveau (jamais de lignes partagees
    # entre plusieurs Niche NN ou plusieurs Tiroir NN) : 1) en-tete par
    # colonne, 2) un tableau PAR NICHE (Niche NN / indicateur Hauteur
    # facade / Nombre de tiroirs / Mode / Ref), 3) en mode Personnalise,
    # un tableau PAR NICHE avec une ligne par tiroir (Tiroir NN / hauteur
    # mm). Quand une niche est ajoutee/retiree, elle a donc son PROPRE
    # tableau plutot que d'apparaitre comme une ligne de plus dans un
    # tableau partage avec les autres niches.
    delete_children_by_prefix(children, 'tableTirCustomCol')
    delete_children_by_prefix(children, 'tableTirNicheCol')
    delete_children_by_prefix(children, 'tableTirCol')
    adsk.doEvents()

    # Hauteurs REELLES (mm) des facades de tiroir, calculees via le
    # meme moteur geometrique que la construction finale (compute_
    # layout), a partir de l'etat ACTUEL du dialogue -- utilisees
    # comme valeurs affichees en mode Hauteur egale (lecture seule)
    # et comme valeurs de depart en mode Personnalise. Si le
    # dialogue est dans un etat incomplet/invalide (ex. pendant la
    # saisie), echoue silencieusement -- le repli habituel
    # (approximation ou vide) s'applique alors.
    hauteurs_reelles_par_col_niche = {}
    if inputs is not None:
        try:
            _values_actuelles = collect_values_mm(inputs)
            _layout_actuel = compute_layout(_values_actuelles)
            _re_nom = re.compile(
                r'^Colonne (\d+)(?: Niche (\d+))? Tiroir (\d+) Façade$')
            for _p in _layout_actuel['panels']:
                if _p[0] != 'XZ':
                    continue
                _nom_p = _p[7]
                _m = _re_nom.match(_nom_p)
                if not _m:
                    continue
                _col_p = int(_m.group(1))
                _niche_p = int(_m.group(2)) if _m.group(2) else 1
                _tir_p = int(_m.group(3))
                _h_mm = cm_to_mm(_p[4] - _p[3])
                hauteurs_reelles_par_col_niche.setdefault(
                    (_col_p, _niche_p - 1), {})[_tir_p - 1] = _h_mm
        except Exception:
            hauteurs_reelles_par_col_niche = {}

    for i in range(1, count + 1):
        if existing_colonnes and i <= len(existing_colonnes):
            col = existing_colonnes[i - 1]
        else:
            col = {'nb_tiroirs': 0, 'mode': 'hauteur_niche'}
        nb_niches = get_nb_niches_colonne(inputs, i) if inputs is not None else 1
        niches_brutes = col if isinstance(col, list) else []
        col_flat = (col if not isinstance(col, list)
                    else niches_brutes[0] if niches_brutes
                    else {'nb_tiroirs': 0, 'mode': 'hauteur_niche'})

        # --- Tableau 1 : en-tete (une fois par colonne) -------------------
        header = children.addTableCommandInput(
            'tableTirCol{:02d}Header'.format(i), '', 4, '2:1:2:2')
        header.hasGrid = False
        header.minimumVisibleRows = 1
        for col_idx, texte in enumerate((
                'Colonne {:02d}'.format(i), 'Nombre de tiroirs', 'Mode de calcul', 'Ref')):
            cell = header.commandInputs.addStringValueInput(
                'tableTirCol{:02d}HeaderCell{}'.format(i, col_idx), '', texte)
            cell.isReadOnly = True
            header.addCommandInput(cell, 0, col_idx)

        if nb_niches <= 1:
            entries = [('—', col_flat)]
        else:
            entries = []
            for k in range(1, nb_niches + 1):
                niche = (niches_brutes[k - 1] if k - 1 < len(niches_brutes)
                         else {'nb_tiroirs': 0, 'mode': 'hauteur_niche'})
                entries.append(('Niche {:02d}'.format(k), niche))

        for niche_idx, (niche_label, entry) in enumerate(entries):
            if _niche_a_porte(portes_colonnes, i, niche_idx):
                # Porte deja active sur cette niche : pas de tiroir
                # possible -- ligne indicative en lecture seule au lieu
                # du tableau editable habituel (et nb_tiroirs force a 0
                # via read_tiroirs_tables cote lecture, id absente ici).
                table_bloque = children.addTableCommandInput(
                    'tableTirNicheCol{:02d}_{:02d}'.format(i, niche_idx), '', 2, '3:5')
                table_bloque.hasGrid = False
                table_bloque.minimumVisibleRows = 1
                lbl_b = table_bloque.commandInputs.addStringValueInput(
                    'tableTirCol{:02d}Label{}'.format(i, niche_idx), '', niche_label)
                lbl_b.isReadOnly = True
                info_b = table_bloque.commandInputs.addStringValueInput(
                    'tableTirCol{:02d}Info{}'.format(i, niche_idx), '',
                    'Porte active sur cette niche — tiroir indisponible')
                info_b.isReadOnly = True
                table_bloque.addCommandInput(lbl_b, 0, 0)
                table_bloque.addCommandInput(info_b, 0, 1)
                continue
            mode_actuel = entry.get('mode', 'hauteur_niche')
            if mode_actuel not in dict(TIROIRS_MODES):
                mode_actuel = 'hauteur_niche'
            tiroirs_brutes = entry.get('tiroirs') or []
            ref_niche_actuel = tiroirs_brutes[0].get('ref', 'haut') if tiroirs_brutes else 'haut'

            # --- Tableau 2 : PROPRE a cette niche (1 seule ligne) ---------
            table = children.addTableCommandInput(
                'tableTirNicheCol{:02d}_{:02d}'.format(i, niche_idx), '', 4, '2:1:2:2')
            table.hasGrid = False
            table.minimumVisibleRows = 1

            lbl = table.commandInputs.addStringValueInput(
                'tableTirCol{:02d}Label{}'.format(i, niche_idx), '', niche_label)
            lbl.isReadOnly = True
            sp_nb = table.commandInputs.addIntegerSpinnerCommandInput(
                'tableTirCol{:02d}Nb{}'.format(i, niche_idx), '',
                0, MAX_COMPTEUR, 1, int(entry.get('nb_tiroirs', 0)))
            dd = table.commandInputs.addDropDownCommandInput(
                'tableTirCol{:02d}Mode{}'.format(i, niche_idx), '',
                adsk.core.DropDownStyles.TextListDropDownStyle)
            for code, label in TIROIRS_MODES:
                dd.listItems.add(label, mode_actuel == code)
            dd_ref = table.commandInputs.addDropDownCommandInput(
                'tableTirCol{:02d}Ref{}'.format(i, niche_idx), '',
                adsk.core.DropDownStyles.TextListDropDownStyle)
            dd_ref.listItems.add('Ref Haut', ref_niche_actuel != 'bas')
            dd_ref.listItems.add('Ref Bas', ref_niche_actuel == 'bas')
            table.addCommandInput(lbl, 0, 0)
            table.addCommandInput(sp_nb, 0, 1)
            table.addCommandInput(dd, 0, 2)
            table.addCommandInput(dd_ref, 0, 3)

            # --- Tableau 3 : PROPRE a cette niche, en mode Personnalise ---
            nb_t_ici = int(entry.get('nb_tiroirs', 0))
            est_personnalise = (mode_actuel == 'personnalise')
            if nb_t_ici > 0:
                # Hauteurs reelles (mm), calculees via compute_layout a
                # partir de l'etat actuel du dialogue (voir plus haut) :
                # affichees en lecture seule en mode Hauteur egale, et
                # utilisees comme valeur de depart en mode Personnalise
                # (avant la 1ere modification manuelle par le tiroir).
                _hauteurs_reelles_ici = hauteurs_reelles_par_col_niche.get(
                    (i, niche_idx), {})
                # Repli si compute_layout a echoue (dialogue incomplet) :
                # ancienne approximation par simple division.
                _champ_h = inputs.itemById('champHauteur') if inputs else None
                _hauteur_mm_defaut = cm_to_mm(_champ_h.value) if _champ_h else 1200.0
                _h_niche_defaut = _hauteur_mm_defaut / max(nb_niches, 1)
                _h_tiroir_defaut = max(_h_niche_defaut / nb_t_ici, 10.0)
                tir_table = children.addTableCommandInput(
                    'tableTirCustomCol{:02d}Niche{:02d}'.format(i, niche_idx), '', 4, '2:1:2:2')
                tir_table.hasGrid = False
                tir_table.maximumVisibleRows = max(nb_t_ici, 2)
                for tk in range(nb_t_ici):
                    t = tiroirs_brutes[tk] if tk < len(tiroirs_brutes) else {}
                    if not isinstance(t, dict):
                        t = {}
                    lbl_t = tir_table.commandInputs.addStringValueInput(
                        'tableTirCol{:02d}TirLabel{}_{}'.format(i, niche_idx, tk),
                        '', 'Tiroir {:02d}'.format(tk + 1))
                    lbl_t.isReadOnly = True
                    hint_t = tir_table.commandInputs.addStringValueInput(
                        'tableTirCol{:02d}TirHint{}_{}'.format(i, niche_idx, tk),
                        '', 'Hauteur façade')
                    hint_t.isReadOnly = True
                    _h_reelle_tk = _hauteurs_reelles_ici.get(tk)
                    if est_personnalise:
                        _h_defaut_tk = (_h_reelle_tk if _h_reelle_tk is not None
                                       else _h_tiroir_defaut)
                        _h_affichee = t.get('hauteur_mm', _h_defaut_tk)
                    else:
                        _h_affichee = (_h_reelle_tk if _h_reelle_tk is not None
                                      else _h_tiroir_defaut)
                    val_h = tir_table.commandInputs.addValueInput(
                        'tableTirCol{:02d}TirHauteur{}_{}'.format(i, niche_idx, tk),
                        '', 'mm',
                        adsk.core.ValueInput.createByReal(mm_to_cm(_h_affichee)))
                    val_h.isReadOnly = not est_personnalise
                    cap_actuelle = t.get('capacite_kg', 30)
                    dd_cap = tir_table.commandInputs.addDropDownCommandInput(
                        'tableTirCol{:02d}TirCap{}_{}'.format(i, niche_idx, tk), '',
                        adsk.core.DropDownStyles.TextListDropDownStyle)
                    dd_cap.listItems.add('30 kg', cap_actuelle != 50)
                    dd_cap.listItems.add('50 kg', cap_actuelle == 50)
                    tir_table.addCommandInput(lbl_t, tk, 0)
                    tir_table.addCommandInput(hint_t, tk, 1)
                    tir_table.addCommandInput(val_h, tk, 2)
                    tir_table.addCommandInput(dd_cap, tk, 3)


def _read_tiroirs_niche_entry(children, col_i, niche_idx):
    # Relit une ligne Niche NN (et ses lignes Tiroir NN eventuelles,
    # toutes DANS le meme tableau desormais) par identifiant stable,
    # pas par position physique (le nombre de lignes varie selon le
    # nombre de tiroirs personnalises deja affiches).
    sp_nb = children.itemById('tableTirCol{:02d}Nb{}'.format(col_i, niche_idx))
    if not sp_nb:
        return None
    dd = children.itemById('tableTirCol{:02d}Mode{}'.format(col_i, niche_idx))
    dd_ref = children.itemById('tableTirCol{:02d}Ref{}'.format(col_i, niche_idx))
    mode = 'hauteur_niche'
    if dd and dd.selectedItem:
        for code, label in TIROIRS_MODES:
            if label == dd.selectedItem.name:
                mode = code
                break
    ref_niche = 'haut'
    if dd_ref and dd_ref.selectedItem and dd_ref.selectedItem.name == 'Ref Bas':
        ref_niche = 'bas'
    nb_t = int(sp_nb.value)
    entry = {'nb_tiroirs': nb_t, 'mode': mode}
    if mode == 'personnalise' and nb_t > 0:
        tiroirs = []
        for tk in range(nb_t):
            val_h = children.itemById(
                'tableTirCol{:02d}TirHauteur{}_{}'.format(col_i, niche_idx, tk))
            dd_cap = children.itemById(
                'tableTirCol{:02d}TirCap{}_{}'.format(col_i, niche_idx, tk))
            cap = 50 if (dd_cap and dd_cap.selectedItem and dd_cap.selectedItem.name == '50 kg') else 30
            tiroirs.append({
                'hauteur_mm': cm_to_mm(val_h.value) if val_h else 100,
                'ref': ref_niche,
                'capacite_kg': cap,
            })
        entry['tiroirs'] = tiroirs
    return entry


PRISE_MAIN_PORTE_OPTIONS = ['Sans', 'Opposé charnières', 'Haut', 'Bas']
PRISE_MAIN_PORTE_CODES = ['sans', 'oppose', 'haut', 'bas']
PRISE_MAIN_TIROIR_OPTIONS = ['Sans', 'Haut', 'Bas']
PRISE_MAIN_TIROIR_CODES = ['sans', 'haut', 'bas']


def rebuild_prise_main_table(children, count, portes_colonnes, tiroirs_colonnes,
                              prise_main_portes, prise_main_tiroirs, inputs=None):
    """Tableau recapitulatif : une ligne par PORTE existante (choix !=
    'off') et par FACADE DE TIROIR existante (nb_tiroirs > 0), tous
    meubles/colonnes confondus, avec un menu deroulant Prise de main par
    ligne. Reconstruit entierement a chaque appel (pas d'etat
    intermediaire complexe a preserver ligne a ligne, contrairement aux
    tiroirs)."""
    delete_children_by_prefix(children, 'tablePriseMain')
    adsk.doEvents()

    header = children.addTableCommandInput('tablePriseMainHeader', '', 2, '3:2')
    header.hasGrid = False
    header.minimumVisibleRows = 1
    for col_idx, texte in enumerate(('Élément', 'Prise de main')):
        cell = header.commandInputs.addStringValueInput(
            'tablePriseMainHeaderCell{}'.format(col_idx), '', texte)
        cell.isReadOnly = True
        header.addCommandInput(cell, 0, col_idx)

    table = children.addTableCommandInput('tablePriseMain', '', 2, '3:2')
    table.hasGrid = False

    row = 0
    for i in range(1, count + 1):
        nb_niches = get_nb_niches_colonne(inputs, i) if inputs is not None else 1
        col_p = portes_colonnes[i - 1] if portes_colonnes and i <= len(portes_colonnes) else None
        niches_p = col_p if isinstance(col_p, list) else ([col_p] if col_p else [])
        col_t = tiroirs_colonnes[i - 1] if tiroirs_colonnes and i <= len(tiroirs_colonnes) else None
        niches_t = col_t if isinstance(col_t, list) else ([col_t] if col_t else [])
        pm_p_col = (prise_main_portes[i - 1]
                    if prise_main_portes and i <= len(prise_main_portes) else None)
        pm_p_niches = pm_p_col if isinstance(pm_p_col, list) else ([pm_p_col] if pm_p_col else [])
        pm_t_col = (prise_main_tiroirs[i - 1]
                    if prise_main_tiroirs and i <= len(prise_main_tiroirs) else None)
        pm_t_niches = pm_t_col if isinstance(pm_t_col, list) else ([pm_t_col] if pm_t_col else [])

        for k in range(nb_niches):
            niche_suffixe = ' Niche {:02d}'.format(k + 1) if nb_niches > 1 else ''
            entry_p = niches_p[k] if k < len(niches_p) else {}
            if not isinstance(entry_p, dict):
                entry_p = {}
            if entry_p.get('choix', 'off') != 'off':
                pm_actuel = pm_p_niches[k] if k < len(pm_p_niches) else 'sans'
                if pm_actuel not in PRISE_MAIN_PORTE_CODES:
                    pm_actuel = 'sans'
                lbl = table.commandInputs.addStringValueInput(
                    'tablePriseMainLabel{}'.format(row), '',
                    'Colonne {:02d}{} — Porte'.format(i, niche_suffixe))
                lbl.isReadOnly = True
                dd = table.commandInputs.addDropDownCommandInput(
                    'tablePriseMainPorteCol{:02d}Niche{:02d}'.format(i, k),
                    '', adsk.core.DropDownStyles.TextListDropDownStyle)
                for opt_label, opt_code in zip(PRISE_MAIN_PORTE_OPTIONS, PRISE_MAIN_PORTE_CODES):
                    dd.listItems.add(opt_label, pm_actuel == opt_code)
                table.addCommandInput(lbl, row, 0)
                table.addCommandInput(dd, row, 1)
                row += 1

            entry_t = niches_t[k] if k < len(niches_t) else {}
            if not isinstance(entry_t, dict):
                entry_t = {}
            nb_t = int(entry_t.get('nb_tiroirs', 0) or 0)
            pm_t_niche = pm_t_niches[k] if k < len(pm_t_niches) else []
            if not isinstance(pm_t_niche, list):
                pm_t_niche = []
            for tk in range(nb_t):
                pm_actuel_t = pm_t_niche[tk] if tk < len(pm_t_niche) else 'sans'
                if pm_actuel_t not in PRISE_MAIN_TIROIR_CODES:
                    pm_actuel_t = 'sans'
                lbl_t = table.commandInputs.addStringValueInput(
                    'tablePriseMainLabel{}'.format(row), '',
                    'Colonne {:02d}{} — Tiroir {:02d}'.format(i, niche_suffixe, tk + 1))
                lbl_t.isReadOnly = True
                dd_t = table.commandInputs.addDropDownCommandInput(
                    'tablePriseMainTiroirCol{:02d}Niche{:02d}_{}'.format(i, k, tk),
                    '', adsk.core.DropDownStyles.TextListDropDownStyle)
                for opt_label, opt_code in zip(PRISE_MAIN_TIROIR_OPTIONS, PRISE_MAIN_TIROIR_CODES):
                    dd_t.listItems.add(opt_label, pm_actuel_t == opt_code)
                table.addCommandInput(lbl_t, row, 0)
                table.addCommandInput(dd_t, row, 1)
                row += 1
    table.maximumVisibleRows = max(row, 2)


def read_prise_main_table(inputs, count):
    """Relit le tableau Prise de main par identifiant stable (le nombre
    de lignes varie selon les portes/tiroirs existants)."""
    portes_out = []
    tiroirs_out = []
    for i in range(1, count + 1):
        nb_niches = get_nb_niches_colonne(inputs, i)
        niches_p = []
        niches_t = []
        for k in range(nb_niches):
            dd = inputs.itemById('tablePriseMainPorteCol{:02d}Niche{:02d}'.format(i, k))
            code_p = 'sans'
            if dd and dd.selectedItem:
                for opt_label, opt_code in zip(PRISE_MAIN_PORTE_OPTIONS, PRISE_MAIN_PORTE_CODES):
                    if opt_label == dd.selectedItem.name:
                        code_p = opt_code
                        break
            niches_p.append(code_p)
            tk = 0
            codes_t = []
            while True:
                dd_t = inputs.itemById(
                    'tablePriseMainTiroirCol{:02d}Niche{:02d}_{}'.format(i, k, tk))
                if not dd_t:
                    break
                code_t = 'sans'
                if dd_t.selectedItem:
                    for opt_label, opt_code in zip(PRISE_MAIN_TIROIR_OPTIONS, PRISE_MAIN_TIROIR_CODES):
                        if opt_label == dd_t.selectedItem.name:
                            code_t = opt_code
                            break
                codes_t.append(code_t)
                tk += 1
            niches_t.append(codes_t)
        portes_out.append(niches_p)
        tiroirs_out.append(niches_t)
    return portes_out, tiroirs_out


def read_tiroirs_tables(children, count, inputs=None):
    # Recherche depuis la racine (inputs) quand elle est fournie : les
    # champs Nombre de tiroirs / Mode / Ref / hauteurs sont imbriques
    # dans des cellules de TableCommandInput, non retrouvables de facon
    # fiable via children.itemById quand 'children' n'est pas deja la
    # racine (meme piege que get_nb_niches_colonne).
    root = inputs if inputs is not None else children
    result = []
    for i in range(1, count + 1):
        table = root.itemById('tableTirCol{:02d}Header'.format(i))
        if not table:
            result.append({'nb_tiroirs': 0, 'mode': 'hauteur_niche'})
            continue
        nb_niches = get_nb_niches_colonne(inputs, i) if inputs is not None else 1
        if nb_niches <= 1:
            entry = _read_tiroirs_niche_entry(root, i, 0)
            result.append(entry if entry is not None else {'nb_tiroirs': 0, 'mode': 'hauteur_niche'})
        else:
            niches = []
            for k in range(nb_niches):
                entry = _read_tiroirs_niche_entry(root, i, k)
                niches.append(entry if entry is not None else {'nb_tiroirs': 0, 'mode': 'hauteur_niche'})
            result.append(niches)
    return result


def count_etageres_fixe_colonne_fields(inputs):
    # Compte combien de colonnes existent deja, numerotation continue
    # sans trou. Recherche depuis la racine (voir
    # read_etageres_fixes_colonnes_from_ui).
    i = 1
    while inputs.itemById('intEtageresFixeColonne{:02d}NbEtageres'.format(i)) is not None:
        i += 1
    return i - 1


def rebuild_etagere_fixe_hauteur_table(children, col_i, count, hauteur_mm_max,
                                        existing_hauteurs=None, ep_panneau_mm=19,
                                        mode='hauteur_colonne'):
    # Meme principe que les tableaux de donnees des autres onglets, mais
    # cible sur la SEULE colonne col_i (peut etre appele isolement par
    # refresh_etagere_fixe_colonne, sans toucher aux autres colonnes).
    # Les cellules Hauteur gardent leurs anciens identifiants
    # ('champEtageresFixeColonneNNHauteurKK') : le reste du code
    # (read_etageres_fixes_colonnes_from_ui) les relit sans changement.
    table_id = 'tableEtgFixeCol{:02d}'.format(col_i)
    old_table = children.itemById(table_id)
    if old_table:
        old_table.deleteMe()
    adsk.doEvents()

    table = children.addTableCommandInput(table_id, '', 4, '2:2:1:2')
    table.hasGrid = False
    table.maximumVisibleRows = max(count, 2)

    interior_h_mm = max(hauteur_mm_max - 2 * ep_panneau_mm, 0)
    gap_mm = (interior_h_mm - count * ep_panneau_mm) / (count + 1) if count > 0 else 0
    entraxe_in_ef = (interior_h_mm + ep_panneau_mm) / (count + 1) if count > 0 else 0

    def _hauteur_formule(k):
        if mode == 'hauteur_colonne':
            return k * (ep_panneau_mm + gap_mm) + ep_panneau_mm / 2.0
        return k * entraxe_in_ef - ep_panneau_mm / 2.0

    for k in range(1, count + 1):
        has_existing = existing_hauteurs and k <= len(existing_hauteurs)
        if mode == 'personnalise' and has_existing:
            hauteur_defaut = existing_hauteurs[k - 1]
        else:
            hauteur_defaut = _hauteur_formule(k)
        vide1 = table.commandInputs.addStringValueInput(
            'tableEtgFixeCol{:02d}Vide1_{:02d}'.format(col_i, k), '', '')
        vide1.isReadOnly = True
        lbl = table.commandInputs.addStringValueInput(
            'tableEtgFixeCol{:02d}Label{:02d}'.format(col_i, k), '',
            "Étagère {:02d}".format(k))
        lbl.isReadOnly = True
        val = table.commandInputs.addValueInput(
            'champEtageresFixeColonne{:02d}Hauteur{:02d}'.format(col_i, k), '', 'mm',
            adsk.core.ValueInput.createByReal(mm_to_cm(hauteur_defaut)))
        val.isMinimumLimited = True
        val.minimumValue = 0
        val.isMaximumLimited = True
        val.maximumValue = mm_to_cm(max(hauteur_mm_max, 3000))
        table.addCommandInput(vide1, k - 1, 0)
        # La colonne vide et la colonne Etagere NN sont fusionnees : le
        # libelle s'etend sur 2 colonnes (1 et 2) au lieu d'une cellule
        # vide separee.
        table.addCommandInput(lbl, k - 1, 1, 0, 1)
        table.addCommandInput(val, k - 1, 3)


def rebuild_etageres_fixe_colonne_groups(children, count, existing_colonnes=None,
                                          hauteur_mm_max=1200, ep_panneau_mm=19,
                                          mode='hauteur_colonne'):
    # Supprime tous les champs Colonne NN du volet Etageres fixe (dont les
    # tableaux de hauteur de chaque colonne), puis les recree pour
    # exactement 'count' compartiments, DIRECTEMENT dans 'children' (le
    # volet rabattable unique et permanent 'Colonnes', jamais recree
    # lui-meme). Si existing_colonnes (liste d'entrees deja normalisees
    # via normalize_etagere_fixe_colonne) est fournie, ses valeurs sont
    # reprises a la place des defauts (0 etagere).
    delete_children_by_prefix(children, 'intEtageresFixeColonne')
    delete_children_by_prefix(children, 'tableEtgFixeCol')
    adsk.doEvents()

    for i in range(1, count + 1):
        if existing_colonnes and i <= len(existing_colonnes):
            col = existing_colonnes[i - 1]
        else:
            col = {'nb_etageres': 0, 'hauteurs': []}
        nb = int(col.get('nb_etageres', 0))

        header = children.addTableCommandInput(
            'tableEtgFixeCol{:02d}Header'.format(i), '', 4, '2:2:1:2')
        header.hasGrid = False
        header.minimumVisibleRows = 1
        lbl_col = header.commandInputs.addStringValueInput(
            'tableEtgFixeCol{:02d}HeaderCell0'.format(i), '', 'Colonne {:02d}'.format(i))
        lbl_col.isReadOnly = True
        header.addCommandInput(lbl_col, 0, 0)
        lbl_nb = header.commandInputs.addStringValueInput(
            'tableEtgFixeCol{:02d}HeaderCell1'.format(i), '', "Nombre d'étagères")
        lbl_nb.isReadOnly = True
        header.addCommandInput(lbl_nb, 0, 1)
        # Le compteur est une cellule VIVANTE (editable), juste apres son
        # libelle texte, sur la MEME ligne.
        sp_nb = header.commandInputs.addIntegerSpinnerCommandInput(
            'intEtageresFixeColonne{:02d}NbEtageres'.format(i), '',
            0, MAX_COMPTEUR, 1, nb)
        header.addCommandInput(sp_nb, 0, 2)
        lbl_haut = header.commandInputs.addStringValueInput(
            'tableEtgFixeCol{:02d}HeaderCell3'.format(i), '', 'Hauteur')
        lbl_haut.isReadOnly = True
        header.addCommandInput(lbl_haut, 0, 3)

        rebuild_etagere_fixe_hauteur_table(
            children, i, nb, hauteur_mm_max, col.get('hauteurs'), ep_panneau_mm, mode)


def get_largeur_mm(inputs):
    ci = inputs.itemById('champLargeur')
    if ci:
        return cm_to_mm(ci.value)
    return 1000


def get_hauteur_mm(inputs):
    ci = inputs.itemById('champHauteur')
    if ci:
        return cm_to_mm(ci.value)
    return 1200


def get_ep_panneau_mm(inputs):
    ci = inputs.itemById('champEpPanneau')
    if ci:
        return cm_to_mm(ci.value)
    return 19


def refresh_etagere_fixe_colonne(inputs, col_i):
    """Reconstruit uniquement les champs Hauteur étagère NN de LA colonne
    col_i du volet Étagères fixe, quand son propre Nombre d'étagères change,
    ou quand son bouton Rafraîchir est cliqué (les autres colonnes ne sont
    PAS affectées). Comme pour les montants et les autres listes dynamiques
    de cet add-in, les hauteurs repartent des valeurs par défaut lors d'un
    changement de nombre (même précédent déjà établi pour
    rebuild_montant_axis_fields / refresh_computed_fields)."""
    group_ef = inputs.itemById('groupEtageresFixeColonnes')
    if not group_ef:
        return
    int_nb = inputs.itemById('intEtageresFixeColonne{:02d}NbEtageres'.format(col_i))
    if not int_nb:
        return
    dd_mode_ef = inputs.itemById('dropdownEtageresFixeMode')
    mode_ef_label_to_code = {
        'Axe égal': 'axe_egal',
        'Intérieur niche': 'hauteur_colonne',
        'Personnalisé': 'personnalise',
    }
    mode_ef = 'hauteur_colonne'
    if dd_mode_ef and dd_mode_ef.selectedItem:
        mode_ef = mode_ef_label_to_code.get(dd_mode_ef.selectedItem.name, 'hauteur_colonne')
    old_table = inputs.itemById('tableEtgFixeCol{:02d}'.format(col_i))
    old_hauteur_count = old_table.rowCount if old_table else 0
    existing_hauteurs = [
        cm_to_mm(inputs.itemById(
            'champEtageresFixeColonne{:02d}Hauteur{:02d}'.format(col_i, k)).value)
        for k in range(1, old_hauteur_count + 1)
    ]
    rebuild_etagere_fixe_hauteur_table(
        group_ef.children, col_i, int(int_nb.value), get_hauteur_mm(inputs),
        existing_hauteurs, get_ep_panneau_mm(inputs), mode_ef)

    # Le nombre de niches de CETTE colonne vient potentiellement de
    # changer : Percage 32 / Etageres mobile / Portes en dependent (voir
    # get_nb_niches_colonne). Contrairement au bouton Rafraichir (qui
    # passe par refresh_computed_fields et reconstruit AUSSI montants +
    # etageres fixe, deja faits ci-dessus), on ne relance ici QUE ces 3
    # tableaux, avec un adsk.doEvents() supplementaire entre chacun --
    # limite le nombre d'operations de destruction/recreation de
    # TableCommandInput enchainees dans le meme evenement, plus sur.
    int_m2 = inputs.itemById('intNbMontants')
    if int_m2:
        adsk.doEvents()
        group_p32 = inputs.itemById('groupPercage32Colonnes')
        if group_p32:
            existing_p32 = read_percage32_tables(
                group_p32.children, int_m2.value + 1, inputs=inputs)
            rebuild_percage32_tables(
                group_p32.children, int_m2.value + 1, existing_p32, inputs=inputs)
        adsk.doEvents()
        group_etageres = inputs.itemById('groupEtageresColonnes')
        if group_etageres:
            existing_e = read_etageres_tables(
                group_etageres.children, int_m2.value + 1, inputs=inputs)
            rebuild_etageres_tables(
                group_etageres.children, int_m2.value + 1, existing_e, inputs=inputs)
        adsk.doEvents()
        group_portes = inputs.itemById('groupPortesColonnes')
        if group_portes:
            existing_portes = read_portes_tables(
                group_portes.children, int_m2.value + 1, inputs=inputs)
            rebuild_portes_tables(
                group_portes.children, int_m2.value + 1, existing_portes, inputs=inputs)
        adsk.doEvents()
        group_tiroirs = inputs.itemById('groupTiroirsColonnes')
        if group_tiroirs:
            existing_tiroirs = read_tiroirs_tables(
                group_tiroirs.children, int_m2.value + 1, inputs=inputs)
            rebuild_tiroirs_tables(
                group_tiroirs.children, int_m2.value + 1, existing_tiroirs, inputs=inputs,
                portes_colonnes=existing_portes)
        adsk.doEvents()


def read_montants_from_ui(children, count):
    # Relit les champs Axe/Reference montant NN actuellement affiches, pour
    # que rebuild_montant_axis_fields puisse les reprendre au lieu de les
    # ecraser par les defauts calcules (voir refresh_computed_fields).
    result = []
    for i in range(1, count + 1):
        f_axe = children.itemById('champAxeMontant{:02d}'.format(i))
        f_ref = children.itemById('dropdownRefMontant{:02d}'.format(i))
        if not f_axe:
            break
        ref = ('droite' if (f_ref and f_ref.selectedItem
                             and f_ref.selectedItem.name == 'Extérieur droit') else 'gauche')
        result.append({'axe': cm_to_mm(f_axe.value), 'ref': ref})
    return result


def _read_etageres_table_row(table, row):
    if row >= table.rowCount:
        return None
    sp_nb = table.getInputAtPosition(row, 1)
    dd = table.getInputAtPosition(row, 2)
    mode = 'hauteur_colonne'
    if dd and dd.selectedItem:
        for code, label in ETAGERES_MODES:
            if label == dd.selectedItem.name:
                mode = code
                break
    return {'nb_etageres': int(sp_nb.value) if sp_nb else 0, 'mode': mode}


def read_etageres_tables(children, count, inputs=None):
    result = []
    for i in range(1, count + 1):
        table = children.itemById('tableEtgCol{:02d}'.format(i))
        if not table:
            result.append({'nb_etageres': 0, 'mode': 'hauteur_colonne'})
            continue
        nb_niches = get_nb_niches_colonne(inputs, i) if inputs is not None else 1
        if nb_niches <= 1:
            entry = _read_etageres_table_row(table, 0)
            result.append(entry if entry is not None else {'nb_etageres': 0, 'mode': 'hauteur_colonne'})
        else:
            niches = []
            for k in range(nb_niches):
                entry = _read_etageres_table_row(table, k)
                niches.append(entry if entry is not None else {'nb_etageres': 0, 'mode': 'hauteur_colonne'})
            result.append(niches)
    return result


def read_etageres_fixes_colonnes_from_ui(inputs, count):
    # Meme principe, pour le volet Etageres fixe (Nombre d'etageres +
    # liste des Hauteur etagere NN par colonne). Recherche depuis la
    # racine (inputs.itemById) : ces champs sont imbriques dans des
    # cellules de tableau, non retrouvables de facon fiable via
    # children.itemById.
    result = []
    for i in range(1, count + 1):
        int_col = inputs.itemById('intEtageresFixeColonne{:02d}NbEtageres'.format(i))
        if not int_col:
            break
        nb = int(int_col.value)
        hauteurs = []
        for k in range(1, nb + 1):
            f = inputs.itemById(
                'champEtageresFixeColonne{:02d}Hauteur{:02d}'.format(i, k))
            if f:
                hauteurs.append(cm_to_mm(f.value))
        result.append({'nb_etageres': nb, 'hauteurs': hauteurs})
    return result


def _read_percage32_table_row(table, row):
    # Lit les 4 valeurs (Systeme/Masquer haut/bas/3 Trous) de la ligne
    # 'row' du tableau Percage 32 (colonnes 1/2/3/4, la colonne 0 est
    # le repere en lecture seule). Renvoie None si la ligne n'existe pas
    # (tableau pas encore aussi rempli qu'attendu).
    if row >= table.rowCount:
        return None
    dd = table.getInputAtPosition(row, 1)
    sp_haut = table.getInputAtPosition(row, 2)
    sp_bas = table.getInputAtPosition(row, 3)
    chk_3trous = table.getInputAtPosition(row, 4)
    systeme = 'off'
    if dd and dd.selectedItem:
        systeme = {'Off': 'off', '32': '32', '64': '64'}.get(dd.selectedItem.name, 'off')
    return {
        'systeme': systeme,
        'masquer_haut': int(sp_haut.value) if sp_haut else 0,
        'masquer_bas': int(sp_bas.value) if sp_bas else 0,
        'trois_trous': bool(chk_3trous.value) if chk_3trous else False,
    }


def read_percage32_tables(children, count, inputs=None):
    # Relit l'etat actuel des tableaux Percçage 32 (un par colonne, voir
    # rebuild_percage32_tables) et reconstruit values['percage32_colonnes'],
    # meme format qu'avant (dict simple ou liste de dicts par niche).
    result = []
    for i in range(1, count + 1):
        table = children.itemById('tableP32Col{:02d}'.format(i))
        if not table:
            result.append({'systeme': '32', 'masquer_bas': 0, 'masquer_haut': 0})
            continue
        nb_niches = get_nb_niches_colonne(inputs, i) if inputs is not None else 1
        if nb_niches <= 1:
            entry = _read_percage32_table_row(table, 0)
            result.append(entry if entry is not None else {'systeme': '32', 'masquer_bas': 0, 'masquer_haut': 0})
        else:
            niches = []
            for k in range(nb_niches):
                entry = _read_percage32_table_row(table, k)
                niches.append(entry if entry is not None else {'systeme': '32', 'masquer_bas': 0, 'masquer_haut': 0})
            result.append(niches)
    return result


_CHOIX_PORTE_PAR_LABEL = {'Off': 'off', 'Gauche': 'gauche', 'Droite': 'droite', '2 Portes': '2portes'}


def _read_portes_table_row(table, row):
    if row >= table.rowCount:
        return None
    dd = table.getInputAtPosition(row, 1)
    choix = 'off'
    if dd and dd.selectedItem:
        choix = _CHOIX_PORTE_PAR_LABEL.get(dd.selectedItem.name, 'off')
    chk_o = table.getInputAtPosition(row, 2)
    ouverte = bool(chk_o.value) if chk_o else False
    return {'choix': choix, 'ouverte': ouverte}


def read_portes_tables(children, count, inputs=None):
    result = []
    for i in range(1, count + 1):
        table = children.itemById('tablePortesCol{:02d}'.format(i))
        if not table:
            result.append({'choix': 'off', 'ouverte': False})
            continue
        nb_niches = get_nb_niches_colonne(inputs, i) if inputs is not None else 1
        if nb_niches <= 1:
            entry = _read_portes_table_row(table, 0)
            result.append(entry if entry is not None else {'choix': 'off', 'ouverte': False})
        else:
            niches = []
            for k in range(nb_niches):
                entry = _read_portes_table_row(table, k)
                niches.append(entry if entry is not None else {'choix': 'off', 'ouverte': False})
            result.append(niches)
    return result


def refresh_computed_fields(inputs):
    """Recalcule les champs dérivés d'autres paramètres (les axes des
    montants intermédiaires, à partir de la Largeur et du Nombre de montants
    courants, ainsi que les volets par colonne de Perçage 32 / Étagères
    mobile / Étagères fixe, une entrée de plus que le nombre de montants) qui
    ne se mettent pas à jour automatiquement quand ces paramètres changent.
    Appelé par le bouton Rafraîchir et par le changement du Nombre de
    montants. Note : un bouton dupliqué près de chaque paramètre concerné
    (Nombre de montants, Nombre d'étagères par colonne) a été essayé, mais un
    bouton-icône ou une case à cocher imbriqués dans un volet rabattable ne
    réagissent pas au clic dans Fusion — le bouton reste donc unique, en bas
    de la boîte de dialogue. Lit les valeurs actuellement affichees de chaque
    colonne AVANT de reconstruire, pour ne jamais perdre ce que l'utilisateur a
    deja saisi ailleurs quand une seule colonne vient de changer."""
    tab_m = inputs.itemById('tabMontants')
    int_m = inputs.itemById('intNbMontants')
    # Chaque bloc ci-dessous ne supprime/recree ses volets que si le nombre
    # de colonnes a REELLEMENT change : reconstruire des volets identiques a
    # chaque clic sur Rafraichir (meme sans rien changer) s'est avere
    # laisser des volets fantomes vides dans Fusion (delete/recreate
    # repete inutilement) ; on l'evite desormais quand ce n'est pas requis.
    dd_mode_m = inputs.itemById('dropdownMontantsMode')
    mode_label_to_code = {
        'Axe égal': 'axe_egal',
        'Intérieur Colonne': 'largeur_colonne',
        'Personnalisé': 'personnalise',
    }
    mode_m = 'axe_egal'
    if dd_mode_m and dd_mode_m.selectedItem:
        mode_m = mode_label_to_code.get(dd_mode_m.selectedItem.name, 'axe_egal')
    if tab_m and int_m:
        # En mode Personnalise, on ne recalcule que si le NOMBRE a change
        # (comme les autres onglets) pour ne jamais perdre une saisie
        # manuelle. Dans les 2 modes automatiques, on recalcule toujours
        # (idempotent, et necessaire pour reagir a un changement de mode
        # sans changement de nombre).
        need_rebuild = (mode_m != 'personnalise'
                        or count_montant_fields(tab_m.children) != int_m.value)
        if need_rebuild:
            existing_montants = read_montants_from_ui(tab_m.children, count_montant_fields(tab_m.children))
            rebuild_montant_axis_fields(
                tab_m.children, int_m.value, get_largeur_mm(inputs), existing_montants,
                mode_m, get_ep_panneau_mm(inputs))
        # (Plus de volet a deplier/replier : Montant intermediaire est
        # desormais un onglet a part entiere, toujours visible.)
    dd_mode_ef = inputs.itemById('dropdownEtageresFixeMode')
    mode_ef_label_to_code2 = {
        'Axe égal': 'axe_egal',
        'Intérieur niche': 'hauteur_colonne',
        'Personnalisé': 'personnalise',
    }
    mode_ef2 = 'hauteur_colonne'
    if dd_mode_ef and dd_mode_ef.selectedItem:
        mode_ef2 = mode_ef_label_to_code2.get(dd_mode_ef.selectedItem.name, 'hauteur_colonne')
    group_ef = inputs.itemById('groupEtageresFixeColonnes')
    if group_ef and int_m:
        need_rebuild_ef = (mode_ef2 != 'personnalise'
                           or count_etageres_fixe_colonne_fields(group_ef.children) != int_m.value + 1)
        if need_rebuild_ef:
            existing_ef = read_etageres_fixes_colonnes_from_ui(
                inputs, count_etageres_fixe_colonne_fields(inputs))
            _chk_socle2 = inputs.itemById('checkSocleActif')
            _champ_socle2 = inputs.itemById('champSocle')
            _socle_mm2 = (cm_to_mm(_champ_socle2.value)
                          if (_champ_socle2 and _chk_socle2 and _chk_socle2.value) else 0)
            rebuild_etageres_fixe_colonne_groups(
                group_ef.children, int_m.value + 1, existing_ef,
                hauteur_mm_max=get_hauteur_mm(inputs) - _socle_mm2,
                ep_panneau_mm=get_ep_panneau_mm(inputs),
                mode=mode_ef2)
    # Percage 32 / Etageres mobile / Portes sont TOUJOURS reconstruits
    # (pas seulement si le nombre de colonnes change) : leurs champs
    # par niche dependent aussi du nombre d'etageres fixe de chaque
    # colonne, qui peut avoir change sans que le nombre de colonnes
    # bouge. Ce sont des champs plats (jamais de volet imbrique), donc
    # sans risque du bug de volet fantome evite ailleurs. Chaque
    # rebuild relit d'abord les valeurs actuellement affichees, donc
    # aucune saisie n'est perdue.
    group_p32 = inputs.itemById('groupPercage32Colonnes')
    if group_p32 and int_m:
        existing_p32 = read_percage32_tables(group_p32.children, int_m.value + 1, inputs=inputs)
        rebuild_percage32_tables(
            group_p32.children, int_m.value + 1, existing_p32, inputs=inputs)
    group_etageres = inputs.itemById('groupEtageresColonnes')
    if group_etageres and int_m:
        existing_e = read_etageres_tables(
            group_etageres.children, int_m.value + 1, inputs=inputs)
        rebuild_etageres_tables(
            group_etageres.children, int_m.value + 1, existing_e, inputs=inputs)
    group_portes = inputs.itemById('groupPortesColonnes')
    if group_portes and int_m:
        existing_portes = read_portes_tables(
            group_portes.children, int_m.value + 1, inputs=inputs)
        rebuild_portes_tables(
            group_portes.children, int_m.value + 1, existing_portes, inputs=inputs)
    group_tiroirs = inputs.itemById('groupTiroirsColonnes')
    if group_tiroirs and int_m:
        existing_tiroirs = read_tiroirs_tables(
            group_tiroirs.children, int_m.value + 1, inputs=inputs)
        rebuild_tiroirs_tables(
            group_tiroirs.children, int_m.value + 1, existing_tiroirs, inputs=inputs,
            portes_colonnes=existing_portes if group_portes and int_m else None)
    tab_prise_main = inputs.itemById('tabPriseMain')
    if tab_prise_main and int_m:
        existing_portes_pm = read_portes_tables(inputs, int_m.value + 1, inputs=inputs)
        existing_tiroirs_pm = read_tiroirs_tables(inputs, int_m.value + 1, inputs=inputs)
        existing_pm_portes, existing_pm_tiroirs = read_prise_main_table(inputs, int_m.value + 1)
        rebuild_prise_main_table(
            tab_prise_main.children, int_m.value + 1,
            existing_portes_pm, existing_tiroirs_pm,
            existing_pm_portes, existing_pm_tiroirs, inputs=inputs)
    update_field_visibility(inputs)

    # Bouton ponctuel : on le décoche aussitôt après usage pour permettre de
    # le recliquer (même comportement que les autres boutons de ce type).
    # Ne s'applique que si l'appel vient bien du clic du bouton (et non d'un
    # changement de Nombre de montants, où il n'y a rien à décocher).
    bouton = inputs.itemById('buttonRafraichir')
    if bouton and bouton.value:
        bouton.value = False


def save_current_as_default(inputs):
    """Enregistre tous les paramètres actuellement saisis dans la boîte de
    dialogue (via collect_values_mm, donc aussi bien les paramètres existants
    que ceux qui seront ajoutés plus tard) comme nouvelles valeurs par défaut
    pour tout prochain « Nouveau meuble »."""
    values = collect_values_mm(inputs)
    save_defaults_to_disk(values)
    if ui:
        ui.messageBox('Les paramètres actuels ont été enregistrés comme valeurs par défaut '
                       'pour les prochains nouveaux meubles.')

    # Bouton ponctuel : on le décoche aussitôt après usage pour permettre de
    # le recliquer (même comportement que Rafraîchir).
    bouton = inputs.itemById('buttonEnregistrerDefaut')
    if bouton and bouton.value:
        bouton.value = False


def _refresh_preset_dropdown(inputs, select_name=None):
    dd_p = inputs.itemById('dropdownPreset')
    if not dd_p:
        return
    dd_p.listItems.clear()
    presets = load_presets()
    dd_p.listItems.add(NO_PRESET_LABEL, select_name is None)
    for _pname in sorted(presets.keys()):
        dd_p.listItems.add(_pname, _pname == select_name)


def save_current_preset(inputs):
    """Enregistre les valeurs actuelles du dialogue sous le nom saisi dans
    'Nom du preset', pour rechargement ulterieur via le menu 'Preset de
    valeurs'."""
    champ_nom = inputs.itemById('champPresetNom')
    nom = champ_nom.value.strip() if champ_nom else ''
    bouton = inputs.itemById('checkPresetSaveAs')
    if not nom:
        if ui:
            ui.messageBox("Saisir d'abord un nom dans 'Nom du preset à "
                           "enregistrer/supprimer'.")
    else:
        values = collect_values_mm(inputs)
        presets = load_presets()
        presets[nom] = values
        save_presets(presets)
        _refresh_preset_dropdown(inputs, select_name=nom)
        if ui:
            ui.messageBox("Preset '{}' enregistré.".format(nom))
    if bouton and bouton.value:
        bouton.value = False


def delete_current_preset(inputs):
    """Supprime le preset dont le nom est saisi dans 'Nom du preset'
    (ou selectionne dans le menu deroulant si le champ est vide)."""
    champ_nom = inputs.itemById('champPresetNom')
    nom = champ_nom.value.strip() if champ_nom else ''
    if not nom:
        dd_p = inputs.itemById('dropdownPreset')
        if dd_p and dd_p.selectedItem and dd_p.selectedItem.name != NO_PRESET_LABEL:
            nom = dd_p.selectedItem.name
    bouton = inputs.itemById('checkPresetDelete')
    if not nom:
        if ui:
            ui.messageBox("Saisir ou sélectionner d'abord un preset à supprimer.")
    else:
        presets = load_presets()
        if nom in presets:
            del presets[nom]
            save_presets(presets)
            _refresh_preset_dropdown(inputs)
            if ui:
                ui.messageBox("Preset '{}' supprimé.".format(nom))
        elif ui:
            ui.messageBox("Aucun preset nommé '{}'.".format(nom))
    if bouton and bouton.value:
        bouton.value = False


def apply_changes_to_existing_meuble(design, root, values, meuble_name, progress=None):
    """Regénère la géométrie du meuble existant nommé `meuble_name` avec les
    paramètres `values`. Renvoie True si le meuble a été trouvé et regénéré,
    False s'il n'existe plus (par ex. supprimé entre-temps dans l'arbre)."""
    target_occ = None
    for occ in list_existing(root, MEUBLE_PREFIX):
        if occ.component.name == meuble_name:
            target_occ = occ
            break
    if not target_occ:
        return False

    comp = target_occ.component
    try:
        start_index = design.timeline.count
    except Exception:
        start_index = None
    delete_linked_doors(comp)
    clear_component_geometry(comp)

    # Transform ACTUEL de l'occurrence, quel qu'il soit (le caisson lui-même
    # n'est jamais retouché ici : sa géométrie est reconstruite dans son
    # propre repère local, donc il reste où l'utilisateur l'a éventuellement
    # déplacé) ; on le transmet pour que les portes, elles, soient recréées à
    # la bonne place plutôt que de retomber sur un positionnement par défaut.
    generate_meuble(root, design, values, comp, target_occ.transform, progress)
    # Regroupe aussi les etapes d'une regeneration (Modifier) sous un
    # groupe timeline au nom du meuble, comme pour une creation.
    if start_index is not None:
        group_timeline_range(design, start_index, comp.name)
    return True
    return True


def apply_button_clicked(args):
    """Bouton « Appliquer » : regénère immédiatement le meuble sélectionné
    dans la liste déroulante avec les valeurs actuellement saisies dans la
    boîte de dialogue, sans fermer celle-ci. Ne fait rien pour « Nouveau
    meuble », qui n'a pas encore de composant existant à modifier.

    Important : la régénération ne doit PAS être faite directement ici. Tant
    que la commande reste ouverte (dialogue affiché), toute modification de
    géométrie faite depuis un gestionnaire inputChanged est traitée par
    Fusion comme un aperçu temporaire et se trouve annulée dès que Fusion
    rafraîchit cet aperçu — ce qui donnait l'impression que le meuble
    « revenait instantanément à son état d'origine ». La bonne méthode est de
    déclencher le véritable événement execute de la commande (le même que le
    bouton OK) via Command.doExecute(terminate=False) : les changements sont
    alors bien commités dans l'historique, tout en gardant le dialogue
    ouvert, exactement comme les commandes Fusion natives qui permettent
    d'appliquer plusieurs fois de suite (ex. placement de lignes de croquis)."""
    inputs = args.inputs
    dd = inputs.itemById('dropdownMeuble')
    selection = dd.selectedItem.name if (dd and dd.selectedItem) else NOUVEAU_MEUBLE
    if selection == NOUVEAU_MEUBLE:
        if ui:
            ui.messageBox('Sélectionne un meuble existant dans la liste « Meuble » pour '
                           'appliquer les changements. Pour créer un nouveau meuble, utilise '
                           'le bouton OK habituel.')
    else:
        cmd = args.firingEvent.sender
        cmd.doExecute(False)

    # Bouton ponctuel : on le décoche aussitôt après usage pour permettre de
    # le recliquer (même comportement que Rafraîchir / Enregistrer par défaut).
    bouton = inputs.itemById('buttonAppliquer')
    if bouton and bouton.value:
        bouton.value = False


# ---------------------------------------------------------------------------
# Construction de la boîte de dialogue (champs communs Créer / Modifier)
# ---------------------------------------------------------------------------

def add_meuble_fields(inputs, cur_mm_func):
    """Construit la boîte de dialogue en 4 volets (onglets) : Caisson (avec
    deux sous-volets rabattables Dimensions et Étagères), Portes, Tiroirs et
    Options avancées."""

    # --- Volet 1 : Caisson ---------------------------------------------
    tab_caisson = inputs.addTabCommandInput('tabCaisson', 'Caisson')
    tc = tab_caisson.children

    group_dimensions = tc.addGroupCommandInput('groupDimensions', 'Dimensions')
    group_dimensions.isExpanded = True
    gd = group_dimensions.children
    for field_id, key, default_mm, min_mm, max_mm, label in FIELDS_CAISSON:
        add_value_field(gd, field_id, label, mm_to_cm(cur_mm_func(key, default_mm)), min_mm, max_mm)
    gd.addBoolValueInput(
        'checkCoupeOnglet', "Coupe d'onglet", True, '',
        bool(cur_mm_func('coupe_onglet', False)))

    group_socle = tc.addGroupCommandInput('groupSocle', 'Socle')
    group_socle.isExpanded = True
    gs = group_socle.children
    gs.addBoolValueInput('checkSocleActif', 'Activer Socle', True, '',
                          bool(cur_mm_func('socle_actif', True)))
    add_value_field(gs, 'champSocle', 'Hauteur socle',
                     mm_to_cm(cur_mm_func('socle', 20)), 0, 300)
    add_value_field(gs, 'champRetraitPlinthe', 'Retrait plinthe',
                     mm_to_cm(cur_mm_func('retrait_plinthe', 5)), 0, 500)

    # --- Volet Montant intermédiaire (deplace hors de Caisson) ----------
    tab_montants = inputs.addTabCommandInput('tabMontants', 'Montant intermédiaire')
    tm = tab_montants.children
    mode_montants_actuel = cur_mm_func('montants_mode', 'axe_egal')
    dd_mode_montants = tm.addDropDownCommandInput(
        'dropdownMontantsMode', 'Mode de calcul',
        adsk.core.DropDownStyles.TextListDropDownStyle)
    dd_mode_montants.listItems.add(
        'Axe égal', mode_montants_actuel == 'axe_egal')
    dd_mode_montants.listItems.add(
        'Intérieur Colonne', mode_montants_actuel == 'largeur_colonne')
    dd_mode_montants.listItems.add(
        'Personnalisé', mode_montants_actuel == 'personnalise')
    cur_nb_m = int(cur_mm_func('nb_montants', 1))
    tm.addIntegerSpinnerCommandInput(
        'intNbMontants', 'Nombre de montants', 0, MAX_COMPTEUR, 1, cur_nb_m)
    largeur_courante = cur_mm_func('largeur', 1000)
    rebuild_montant_axis_fields(
        tm, cur_nb_m, largeur_courante, cur_mm_func('montants', None),
        mode_montants_actuel, cur_mm_func('ep_panneau', 19))
    add_value_field(tm, 'champRetraitMontant', 'Retrait',
                     mm_to_cm(cur_mm_func('retrait_montant', 0)), 0, 100)
    add_value_field(tm, 'champEpMontant', 'Épaisseur',
                     mm_to_cm(cur_mm_func('ep_montant', cur_mm_func('ep_panneau', 19))), 8, 40)

    # --- Volet 2 : Étagères fixe --------------------------------------------
    tab_etageres_fixe = inputs.addTabCommandInput('tabEtageresFixe', 'Étagères fixe')
    gef = tab_etageres_fixe.children
    # Un volet rabattable par colonne (compartiment d'étagère, même
    # correspondance que Perçage 32 / Étagères mobile), chacun avec son
    # propre Nombre d'étagères et une Hauteur (mm, axe de l'étagère depuis le
    # bas du Dessous) par étagère de cette colonne. Retrait commun a
    # toutes les colonnes (comme Étagères mobile).
    values_ef_actuelles = {'etageres_fixes_colonnes': cur_mm_func('etageres_fixes_colonnes', None)}
    mode_ef_actuel = cur_mm_func('etageres_fixe_mode', 'hauteur_colonne')
    dd_mode_ef = gef.addDropDownCommandInput(
        'dropdownEtageresFixeMode', 'Mode de calcul',
        adsk.core.DropDownStyles.TextListDropDownStyle)
    dd_mode_ef.listItems.add(
        'Axe égal', mode_ef_actuel == 'axe_egal')
    dd_mode_ef.listItems.add(
        'Intérieur niche', mode_ef_actuel == 'hauteur_colonne')
    dd_mode_ef.listItems.add(
        'Personnalisé', mode_ef_actuel == 'personnalise')
    add_value_field(gef, 'champRetraitEtagereFixe', 'Retrait',
                     mm_to_cm(cur_mm_func('retrait_etagere_fixe', 0)), 0, 100)
    add_value_field(gef, 'champEpEtagereFixe', 'Épaisseur',
                     mm_to_cm(cur_mm_func('ep_etagere_fixe', cur_mm_func('ep_panneau', 19))), 8, 40)
    group_ef = gef.addGroupCommandInput('groupEtageresFixeColonnes', 'Étagères')
    group_ef.isExpanded = True
    rebuild_etageres_fixe_colonne_groups(
        group_ef.children, cur_nb_m + 1,
        normalized_etageres_fixes_colonnes(values_ef_actuelles, cur_nb_m + 1),
        hauteur_mm_max=(cur_mm_func('hauteur', 1200)
                        - (cur_mm_func('socle', 0) if cur_mm_func('socle_actif', True) else 0)),
        ep_panneau_mm=cur_mm_func('ep_panneau', 19),
        mode=mode_ef_actuel)

    # --- Volet 3 : Perçage 32 ----------------------------------------------
    tab_percage32 = inputs.addTabCommandInput('tabPercage32', 'Perçage 32')
    gp32 = tab_percage32.children
    # Retrait façade et Référence perçage : communs à toutes les colonnes.
    add_value_field(gp32, 'champPercage32Retrait', 'Retrait façade',
                     mm_to_cm(cur_mm_func('percage32_retrait', 37)), 0, 500)
    add_value_field(gp32, 'champPercage32MargeBas', 'Référence perçage',
                     mm_to_cm(cur_mm_func('percage32_marge_bas', 9.5)), 0, 1000)
    # Un volet rabattable par colonne (compartiment d'étagère), chacun avec
    # son propre Système 32/64 et son propre masquage bas/haut.
    values_p32_actuelles = {
        'percage32_colonnes': cur_mm_func('percage32_colonnes', None),
        'percage32_actif': cur_mm_func('percage32_actif', True),
        'percage64_actif': cur_mm_func('percage64_actif', False),
        'percage32_masquer_bas': cur_mm_func('percage32_masquer_bas', 0),
        'percage32_masquer_haut': cur_mm_func('percage32_masquer_haut', 0),
    }
    group_p32 = gp32.addGroupCommandInput('groupPercage32Colonnes', 'Colonnes')
    group_p32.isExpanded = True
    rebuild_percage32_tables(
        group_p32.children, cur_nb_m + 1,
        values_p32_actuelles.get('percage32_colonnes'), inputs=inputs)

    # --- Volet 4 : Étagères mobile -------------------------------------------
    tab_etageres = inputs.addTabCommandInput('tabEtageres', 'Étagères mobile')
    ge = tab_etageres.children
    # Retrait : commun à toutes les colonnes.
    add_value_field(ge, 'champRetraitEtagere', 'Retrait',
                     mm_to_cm(cur_mm_func('retrait_etagere', 0)), 0, 100)
    add_value_field(ge, 'champEpEtagereMobile', 'Épaisseur',
                     mm_to_cm(cur_mm_func('ep_etagere_mobile', cur_mm_func('ep_panneau', 19))), 8, 40)
    # Un volet rabattable par colonne (même correspondance colonne <->
    # compartiment que Perçage 32), chacun avec son propre Nombre d'étagères
    # et son propre Mode de calcul.
    values_etageres_actuelles = {
        'etageres_colonnes': cur_mm_func('etageres_colonnes', None),
        'nb_etageres': cur_mm_func('nb_etageres', 0),
        'etageres_mode': cur_mm_func('etageres_mode', 'hauteur_colonne'),
    }
    group_etageres = ge.addGroupCommandInput('groupEtageresColonnes', 'Étagères')
    group_etageres.isExpanded = True
    rebuild_etageres_tables(
        group_etageres.children, cur_nb_m + 1,
        values_etageres_actuelles.get('etageres_colonnes'), inputs=inputs)

    # --- Volet 5 : Portes -------------------------------------------------
    tab_portes = inputs.addTabCommandInput('tabPortes', 'Portes')
    gp = tab_portes.children
    mode_portes_actuel = cur_mm_func('portes_mode', 'applique')
    dd_mode_portes = gp.addDropDownCommandInput(
        'dropdownPortesMode', 'Type de pose',
        adsk.core.DropDownStyles.TextListDropDownStyle)
    dd_mode_portes.listItems.add('En applique', mode_portes_actuel != 'encastre')
    dd_mode_portes.listItems.add('Encastré', mode_portes_actuel == 'encastre')
    add_value_field(gp, 'champJeuPorte', 'Jeu périphérique porte',
                     mm_to_cm(cur_mm_func('jeu_porte', 2)), 0.5, 10)
    add_value_field(gp, 'champEpPorte', 'Épaisseur porte',
                     mm_to_cm(cur_mm_func('ep_porte', cur_mm_func('ep_panneau', 19))), 8, 40)
    group_charniere_embase = gp.addGroupCommandInput(
        'groupCharniereEmbase', 'Charnière/Embase')
    group_charniere_embase.isExpanded = True
    gce = group_charniere_embase.children
    montage_portes_actuel = cur_mm_func('portes_montage', 'inserta')
    dd_montage_portes = gce.addDropDownCommandInput(
        'dropdownPortesMontage', 'Montage charnière',
        adsk.core.DropDownStyles.TextListDropDownStyle)
    dd_montage_portes.listItems.add(
        'Inserta/à frapper', montage_portes_actuel not in ('visser', 'off'))
    dd_montage_portes.listItems.add('À visser', montage_portes_actuel == 'visser')
    dd_montage_portes.listItems.add('Off', montage_portes_actuel == 'off')
    montage_embase_actuel = cur_mm_func('portes_montage_embase', 'eurovis')
    dd_montage_embase = gce.addDropDownCommandInput(
        'dropdownPortesMontageEmbase', 'Montage embase',
        adsk.core.DropDownStyles.TextListDropDownStyle)
    dd_montage_embase.listItems.add(
        'Eurovis', montage_embase_actuel not in ('visser', 'off'))
    dd_montage_embase.listItems.add('À visser', montage_embase_actuel == 'visser')
    dd_montage_embase.listItems.add('Off', montage_embase_actuel == 'off')
    # Percage des charnieres Inserta Blum (godet + chevilles), ancre sur
    # le systeme 32 -- voir hinge_positions_locales_mm dans meuble_layout.
    add_value_field(gce, 'champCharniereAxeBasse', 'Axe charnière basse',
                     mm_to_cm(cur_mm_func('charniere_axe_basse', 100)), 30, 400)
    add_value_field(gce, 'champCharniereAxeHaute', 'Axe charnière haute',
                     mm_to_cm(cur_mm_func('charniere_axe_haute', 100)), 30, 400)
    charniere_auto_actuel = cur_mm_func('charniere_auto', True)
    gce.addBoolValueInput(
        'checkCharniereAuto', 'Nombre de charnières automatique',
        True, '', bool(charniere_auto_actuel))
    gce.addIntegerSpinnerCommandInput(
        'intCharniereNbInter', "Charnières intermédiaires (si manuel)", 0, 6, 1,
        int(cur_mm_func('charniere_nb_inter', 0)))
    # Un volet rabattable par colonne (meme correspondance que Percage 32 /
    # Etageres), chacune avec son propre choix Off / Gauche / Droite / 2
    # Portes. Voir compute_layout pour la geometrie exacte (bornes de
    # colonne, jeu, recoupe sur etageres fixe).
    values_portes_actuelles = {'portes_colonnes': cur_mm_func('portes_colonnes', None)}
    group_portes = gp.addGroupCommandInput('groupPortesColonnes', 'Portes')
    group_portes.isExpanded = True
    rebuild_portes_tables(
        group_portes.children, cur_nb_m + 1,
        values_portes_actuelles.get('portes_colonnes'), inputs=inputs)

    # --- Volet 6 : Tiroirs -------------------------------------------------
    tab_tiroirs = inputs.addTabCommandInput('tabTiroirs', 'Tiroirs')
    gt = tab_tiroirs.children
    mode_tiroirs_actuel = cur_mm_func('tiroirs_mode', 'applique')
    dd_mode_tiroirs = gt.addDropDownCommandInput(
        'dropdownTiroirsMode', 'Type de pose',
        adsk.core.DropDownStyles.TextListDropDownStyle)
    dd_mode_tiroirs.listItems.add('En applique', mode_tiroirs_actuel != 'encastre')
    dd_mode_tiroirs.listItems.add('Encastré', mode_tiroirs_actuel == 'encastre')
    add_value_field(gt, 'champRetraitPercageCoulisse',
                     'Décalage en profondeur',
                     mm_to_cm(cur_mm_func('retrait_percage_coulisse', 0)), 0, 100)
    add_value_field(gt, 'champJeuTiroir', 'Jeu périphérique façade',
                     mm_to_cm(cur_mm_func('jeu_tiroir', 2)), 0.5, 10)
    add_value_field(gt, 'champEpFaceTiroir', 'Épaisseur façade',
                     mm_to_cm(cur_mm_func('ep_face_tiroir', cur_mm_func('ep_panneau', 19))), 8, 40)
    add_value_field(gt, 'champEpFondTiroir', 'Épaisseur fond tiroir',
                     mm_to_cm(cur_mm_func('ep_fond_tiroir', cur_mm_func('ep_fond', 8))), 3, 19)
    add_value_field(gt, 'champEpPanneauTiroir', 'Épaisseur panneaux tiroir',
                     mm_to_cm(cur_mm_func('ep_panneau_tiroir', cur_mm_func('ep_panneau', 19))), 8, 40)
    group_coulisse = gt.addGroupCommandInput('groupCoulisse', 'Coulisse')
    group_coulisse.isExpanded = True
    gco = group_coulisse.children
    montage_coulisse_actuel = cur_mm_func('tiroirs_montage_coulisse', 'eurovis')
    dd_montage_coulisse = gco.addDropDownCommandInput(
        'dropdownTiroirsMontageCoulisse', 'Montage coulisse',
        adsk.core.DropDownStyles.TextListDropDownStyle)
    dd_montage_coulisse.listItems.add('Eurovis', montage_coulisse_actuel != 'visser')
    dd_montage_coulisse.listItems.add('À visser', montage_coulisse_actuel == 'visser')
    # Un volet rabattable par colonne, meme construction que Etageres
    # mobile (Colonne NN / Nombre de tiroirs / Mode de calcul), une
    # entree par niche si la colonne en a plusieurs.
    values_tiroirs_actuelles = {'tiroirs_colonnes': cur_mm_func('tiroirs_colonnes', None)}
    group_tiroirs = gt.addGroupCommandInput('groupTiroirsColonnes', 'Façades')
    group_tiroirs.isExpanded = True
    rebuild_tiroirs_tables(
        group_tiroirs.children, cur_nb_m + 1,
        values_tiroirs_actuelles.get('tiroirs_colonnes'), inputs=inputs,
        portes_colonnes=cur_mm_func('portes_colonnes', None))

    # --- Volet 6bis : Prise de main -----------------------------------
    tab_prise_main = inputs.addTabCommandInput('tabPriseMain', 'Prise de main')
    gpm = tab_prise_main.children
    rebuild_prise_main_table(
        gpm, cur_nb_m + 1,
        cur_mm_func('portes_colonnes', None), cur_mm_func('tiroirs_colonnes', None),
        cur_mm_func('prise_main_portes', None), cur_mm_func('prise_main_tiroirs', None),
        inputs=inputs)

    # --- Volet 7 : Options avancées ----------------------------------------
    tab_avance = inputs.addTabCommandInput('tabAvance', 'Preset')
    ga = tab_avance.children

    # Presets : charge/enregistre/supprime un ensemble de valeurs nomme
    # (independant du meuble selectionne). Choisir un preset dans la
    # liste applique aussitot ses valeurs a tous les champs du
    # dialogue -- pratique pour repartir d'un modele courant sans avoir
    # a re-saisir chaque valeur.
    presets = load_presets()
    dd_preset = ga.addDropDownCommandInput(
        'dropdownPreset', 'Preset de valeurs', adsk.core.DropDownStyles.TextListDropDownStyle)
    dd_preset.listItems.add(NO_PRESET_LABEL, True)
    for _pname in sorted(presets.keys()):
        dd_preset.listItems.add(_pname, False)
    ga.addBoolValueInput('checkPresetDelete', 'Supprimer le preset',
                          False, RESOURCE_FOLDER_SUPPRIMER_PRESET, False)
    ga.addStringValueInput('champPresetNom', 'Nouveau preset', '')
    ga.addBoolValueInput('checkPresetSaveAs', 'Enregistrer le preset',
                          False, RESOURCE_FOLDER_SAVE_DEFAULT, False)

    # Bouton ajouté en tout dernier : Fusion ne permet pas d'insérer un bouton
    # personnalisé dans la barre native OK/Annuler, mais en l'ajoutant comme
    # dernier input il se retrouve juste au-dessus de cette barre, au plus
    # près visuellement. Un bouton-icône dupliqué près de Nombre de montants
    # et de chaque Nombre d'étagères par colonne a été essayé (imbriqué dans
    # un volet rabattable), mais ne réagissait pas au clic dans Fusion, y
    # compris une fois converti en case à cocher classique : le bouton
    # Rafraîchir reste donc unique, ici, comme avant.
    inputs.addBoolValueInput('buttonRafraichir', 'Rafraîchir',
                              False, RESOURCE_FOLDER_REFRESH, False)
    inputs.addBoolValueInput('buttonEnregistrerDefaut', 'Enregistrer par défaut',
                              False, RESOURCE_FOLDER_SAVE_DEFAULT, False)
    inputs.addBoolValueInput('buttonAppliquer', 'Appliquer',
                              False, RESOURCE_FOLDER_APPLIQUER, False)

    # Reverifie ici (pas seulement au demarrage de Fusion) pour que
    # l'ouverture du dialogue detecte une eventuelle publication faite
    # PENDANT que Fusion tournait deja.
    try:
        _check_and_apply_updates()
    except Exception:
        pass
    _version_detectee = _extract_version(
        os.path.join(SCRIPT_DIR, 'MeubleParametrique.py')) or 'inconnue'
    if _version_detectee != ADDIN_VERSION:
        inputs.addTextBoxCommandInput(
            'textMiseAJour', '',
            'Mise à jour détectée : V ' + _version_detectee + '\n\n'
            "Pour relancer l'add-in : Utilitaires > Compléments > "
            "Scripts et compléments (ou Maj+S) > MeubleParametrique > "
            "Arrêter > Exécuter (ou redémarrer Fusion 360).",
            6, True)


def _copie_apercu_html_unique():
    """Copie apercu_meuble.html sous un nom unique (horodate) dans un
    sous-dossier temporaire, et renvoie son chemin (avec des '/').
    Necessaire car Fusion rejette les suffixes ?/# anti-cache sur une
    url de fichier local, et re-attribuer la MEME url ne recharge pas
    le contenu si le navigateur interne l'a deja en cache. Nettoie
    aussi les anciennes copies pour ne pas accumuler de fichiers."""
    import shutil
    dossier_tmp = os.path.join(SCRIPT_DIR, '_apercu_tmp')
    os.makedirs(dossier_tmp, exist_ok=True)
    for _f in os.listdir(dossier_tmp):
        try:
            os.remove(os.path.join(dossier_tmp, _f))
        except Exception:
            pass
    nom_unique = 'apercu_{}.html'.format(int(time.time() * 1000))
    dest = os.path.join(dossier_tmp, nom_unique)
    shutil.copy2(os.path.join(SCRIPT_DIR, 'apercu_meuble.html'), dest)
    return dest.replace(os.sep, '/')


def _panneaux_vue_face(layout):
    """Convertit layout['panels'] + layout['doors'] en une liste de
    volumes 2D+profondeur (x0,x1,y0,y1,z0,z1 en mm) pour les 3 vues de
    l'aperçu SVG (face/cote/dessus). Chaque entree : type
    ('panneau'/'porte'/'tiroir'/'etagere'), nom, sens eventuel.
    Tolerant : une entree individuelle mal formee est simplement
    ignoree plutot que de faire echouer tout l'apercu."""
    rects = []
    for p in layout.get('panels', []):
        try:
            if p[0] == 'XZ':
                _, x0, x1, z0, z1, y0, yext, nom = p[:8]
                y1 = y0 + yext
            else:
                x0, x1, y0, y1, z0, zext, nom = p[:7]
                z1 = z0 + zext
            if 'Étagère' in nom:
                typ = 'etagere'
            elif 'Façade' in nom:
                typ = 'tiroir'
            else:
                typ = 'panneau'
            rects.append({
                'x0': cm_to_mm(x0), 'x1': cm_to_mm(x1),
                'y0': cm_to_mm(y0), 'y1': cm_to_mm(y1),
                'z0': cm_to_mm(z0), 'z1': cm_to_mm(z1),
                'type': typ, 'nom': nom})
        except Exception:
            continue
    for d in layout.get('doors', []):
        try:
            x0, z0, largeur, hauteur, ep, sens = d[0], d[1], d[2], d[3], d[4], d[5]
            rects.append({
                'x0': cm_to_mm(x0), 'x1': cm_to_mm(x0 + largeur),
                'y0': 0.0, 'y1': cm_to_mm(ep),
                'z0': cm_to_mm(z0), 'z1': cm_to_mm(z0 + hauteur),
                'type': 'porte', 'nom': 'Porte', 'sens': sens})
        except Exception:
            continue
    return rects


def update_apercu(inputs):
    """Recalcule le meuble a partir de l'etat ACTUEL du dialogue et
    envoie une vue de face simplifiee (SVG) a la palette d'apercu, si
    elle est ouverte. Echoue silencieusement (dialogue incomplet,
    palette fermee, etc.) sans jamais interrompre le reste du
    dialogue."""
    try:
        pal = ui.palettes.itemById(APERCU_PALETTE_ID) if ui else None
        if not pal or not pal.isVisible:
            return
        values = collect_values_mm(inputs)
        layout = compute_layout(values)
        donnees = {
            'L_mm': values.get('largeur', 0),
            'H_mm': values.get('hauteur', 0),
            'P_mm': values.get('profondeur', 0),
            'panneaux': _panneaux_vue_face(layout),
        }
        pal.sendInfoToHTML('apercu', json.dumps(donnees))
    except Exception:
        pass


def collect_values_mm(inputs):
    def val_mm(field_id, default_mm):
        ci = inputs.itemById(field_id)
        if not ci:
            return default_mm
        return cm_to_mm(ci.value)

    def int_val(field_id, default_n):
        ci = inputs.itemById(field_id)
        if ci is None:
            return default_n
        try:
            return int(ci.value)
        except Exception:
            return default_n

    values = {}
    values['largeur'] = val_mm('champLargeur', 1000)
    values['hauteur'] = val_mm('champHauteur', 1200)
    values['profondeur'] = val_mm('champProfondeur', 500)
    values['ep_panneau'] = val_mm('champEpPanneau', 19)
    values['ep_fond'] = val_mm('champEpFond', 8)
    chk_socle = inputs.itemById('checkSocleActif')
    values['socle_actif'] = chk_socle.value if chk_socle else True
    values['socle'] = val_mm('champSocle', 20)
    values['retrait_plinthe'] = val_mm('champRetraitPlinthe', 5)
    chk_onglet = inputs.itemById('checkCoupeOnglet')
    values['coupe_onglet'] = chk_onglet.value if chk_onglet else False

    values['retrait_etagere'] = val_mm('champRetraitEtagere', 0)
    values['ep_etagere_mobile'] = val_mm(
        'champEpEtagereMobile', values['ep_panneau'])
    values['retrait_montant'] = val_mm('champRetraitMontant', 0)
    values['ep_montant'] = val_mm('champEpMontant', values['ep_panneau'])
    values['retrait_etagere_fixe'] = val_mm('champRetraitEtagereFixe', 0)
    values['ep_etagere_fixe'] = val_mm('champEpEtagereFixe', values['ep_panneau'])

    dd_mode_m = inputs.itemById('dropdownMontantsMode')
    mode_label_to_code = {
        'Axe égal': 'axe_egal',
        'Intérieur Colonne': 'largeur_colonne',
        'Personnalisé': 'personnalise',
    }
    if dd_mode_m and dd_mode_m.selectedItem:
        values['montants_mode'] = mode_label_to_code.get(dd_mode_m.selectedItem.name, 'axe_egal')
    else:
        values['montants_mode'] = 'axe_egal'

    values['nb_montants'] = int_val('intNbMontants', 1)
    nb_m = values['nb_montants']
    pas_defaut = values['largeur'] / (nb_m + 1) if nb_m > 0 else 0
    montants = []
    for i in range(1, nb_m + 1):
        axe_mm = val_mm('champAxeMontant{:02d}'.format(i), pas_defaut * i)
        dd_ref = inputs.itemById('dropdownRefMontant{:02d}'.format(i))
        if dd_ref and dd_ref.selectedItem and dd_ref.selectedItem.name == 'Extérieur droit':
            ref = 'droite'
        else:
            ref = 'gauche'
        montants.append({'axe': axe_mm, 'ref': ref})
    values['montants'] = montants

    # Étagères fixe : même correspondance colonne <-> compartiment que
    # Perçage 32 / Étagères mobile. Chaque volet rabattable a son propre
    # Nombre d'étagères et une Hauteur (mm) par étagère de cette colonne.
    etageres_fixes_colonnes = []
    for i in range(1, nb_m + 2):
        int_nb_ef = inputs.itemById('intEtageresFixeColonne{:02d}NbEtageres'.format(i))
        nb_ef = int(int_nb_ef.value) if int_nb_ef else 0
        hauteurs_ef = [val_mm('champEtageresFixeColonne{:02d}Hauteur{:02d}'.format(i, k), 0)
                       for k in range(1, nb_ef + 1)]
        etageres_fixes_colonnes.append({'nb_etageres': nb_ef, 'hauteurs': hauteurs_ef})
    values['etageres_fixes_colonnes'] = etageres_fixes_colonnes
    dd_mode_ef = inputs.itemById('dropdownEtageresFixeMode')
    mode_ef_label_to_code = {
        'Axe égal': 'axe_egal',
        'Intérieur niche': 'hauteur_colonne',
        'Personnalisé': 'personnalise',
    }
    if dd_mode_ef and dd_mode_ef.selectedItem:
        values['etageres_fixe_mode'] = mode_ef_label_to_code.get(
            dd_mode_ef.selectedItem.name, 'hauteur_colonne')
    else:
        values['etageres_fixe_mode'] = 'hauteur_colonne'

    # Une colonne (compartiment d'étagère) de plus que le nombre de montants
    # intermédiaires : chaque volet rabattable a son propre Système 32/64 et
    # son propre masquage (bas/haut) de perçage. Retrait et référence restent
    # communs (lus une seule fois plus bas).
    # read_percage32_colonnes_from_ui gere aussi bien le repli simple
    # (dict par colonne) que le mode par niche (liste de dicts) si la
    # case 'Regler par niche' de cette colonne est cochee.
    values['percage32_colonnes'] = read_percage32_tables(inputs, nb_m + 1, inputs=inputs)
    values['percage32_retrait'] = val_mm('champPercage32Retrait', 37)
    values['percage32_marge_bas'] = val_mm('champPercage32MargeBas', 9.5)

    # Étagères : même correspondance colonne <-> compartiment que Perçage 32 ;
    # chaque volet rabattable a son propre Nombre d'étagères et son propre
    # Mode de calcul (Retrait déjà lu ci-dessus, commun à toutes les colonnes).
    values['etageres_colonnes'] = read_etageres_tables(inputs, nb_m + 1, inputs=inputs)

    dd_mode_portes = inputs.itemById('dropdownPortesMode')
    if dd_mode_portes and dd_mode_portes.selectedItem and dd_mode_portes.selectedItem.name.startswith('Encastr'):
        values['portes_mode'] = 'encastre'
    else:
        values['portes_mode'] = 'applique'
    dd_montage_portes = inputs.itemById('dropdownPortesMontage')
    _sel_mp = (dd_montage_portes.selectedItem.name
               if dd_montage_portes and dd_montage_portes.selectedItem else '')
    if _sel_mp == 'Off':
        values['portes_montage'] = 'off'
    elif _sel_mp == 'À visser':
        values['portes_montage'] = 'visser'
    else:
        values['portes_montage'] = 'inserta'
    dd_montage_embase = inputs.itemById('dropdownPortesMontageEmbase')
    _sel_me = (dd_montage_embase.selectedItem.name
               if dd_montage_embase and dd_montage_embase.selectedItem else '')
    if _sel_me == 'Off':
        values['portes_montage_embase'] = 'off'
    elif _sel_me == 'À visser':
        values['portes_montage_embase'] = 'visser'
    else:
        values['portes_montage_embase'] = 'eurovis'
    values['jeu_porte'] = val_mm('champJeuPorte', 2)
    values['ep_porte'] = val_mm('champEpPorte', values['ep_panneau'])
    values['charniere_axe_basse'] = val_mm('champCharniereAxeBasse', 100)
    values['charniere_axe_haute'] = val_mm('champCharniereAxeHaute', 100)
    values['charniere_nb_inter'] = int_val('intCharniereNbInter', 0)
    chk_charniere_auto = inputs.itemById('checkCharniereAuto')
    values['charniere_auto'] = bool(chk_charniere_auto.value) if chk_charniere_auto else True
    # Portes : meme correspondance colonne <-> compartiment que Percage 32 /
    # Etageres ; chaque volet rabattable a son propre choix Off/Gauche/
    # Droite/2 Portes (voir compute_layout pour la geometrie).
    values['portes_colonnes'] = read_portes_tables(inputs, nb_m + 1, inputs=inputs)

    dd_mode_tiroirs = inputs.itemById('dropdownTiroirsMode')
    if (dd_mode_tiroirs and dd_mode_tiroirs.selectedItem
            and dd_mode_tiroirs.selectedItem.name.startswith('Encastr')):
        values['tiroirs_mode'] = 'encastre'
    else:
        values['tiroirs_mode'] = 'applique'
    values['jeu_tiroir'] = val_mm('champJeuTiroir', 2)
    values['ep_face_tiroir'] = val_mm('champEpFaceTiroir', values['ep_panneau'])
    values['ep_fond_tiroir'] = val_mm('champEpFondTiroir', values.get('ep_fond', 8))
    values['ep_panneau_tiroir'] = val_mm(
        'champEpPanneauTiroir', values.get('ep_panneau', 19))
    values['retrait_percage_coulisse'] = val_mm('champRetraitPercageCoulisse', 0)
    dd_montage_coulisse = inputs.itemById('dropdownTiroirsMontageCoulisse')
    if (dd_montage_coulisse and dd_montage_coulisse.selectedItem
            and dd_montage_coulisse.selectedItem.name == 'À visser'):
        values['tiroirs_montage_coulisse'] = 'visser'
    else:
        values['tiroirs_montage_coulisse'] = 'eurovis'
    # Tiroirs : meme correspondance colonne <-> compartiment que Percage
    # 32 / Etageres ; chaque volet rabattable a son propre Nombre de
    # tiroirs et son propre Mode de calcul.
    values['tiroirs_colonnes'] = read_tiroirs_tables(inputs, nb_m + 1, inputs=inputs)

    values['prise_main_portes'], values['prise_main_tiroirs'] = (
        read_prise_main_table(inputs, nb_m + 1))

    values['gap_mm'] = DEFAULT_GAP_MM
    return values


def update_field_visibility(inputs):
    chk_charniere_auto = inputs.itemById('checkCharniereAuto')
    champ_charniere_manuel = inputs.itemById('intCharniereNbInter')
    if chk_charniere_auto and champ_charniere_manuel:
        champ_charniere_manuel.isVisible = not chk_charniere_auto.value
    # Retrait reste commun et toujours visible (Nombre
    # d'étagères et Mode de calcul sont désormais réglés par colonne). Dans
    # chaque volet « Colonne NN » du volet Étagères, le Mode de calcul n'a de
    # sens que si cette colonne a au moins une étagère demandée.
    # Le tableau Etageres mobile n'a plus besoin de logique de
    # visibilite conditionnelle : toutes les cellules restent visibles.

    chk_socle = inputs.itemById('checkSocleActif')
    champ_socle = inputs.itemById('champSocle')
    champ_retrait_plinthe = inputs.itemById('champRetraitPlinthe')
    chk_onglet = inputs.itemById('checkCoupeOnglet')
    # Le socle n'est pas compatible avec la coupe d'onglet (voir
    # compute_layout) : desactive et decoche automatiquement la case
    # Socle des que Coupe d'onglet est cochee.
    if chk_onglet and chk_onglet.value and chk_socle:
        if chk_socle.value:
            chk_socle.value = False
        chk_socle.isEnabled = False
    elif chk_socle:
        chk_socle.isEnabled = True
    if chk_socle:
        if champ_socle:
            champ_socle.isVisible = chk_socle.value
        if champ_retrait_plinthe:
            champ_retrait_plinthe.isVisible = chk_socle.value

    # Retrait façade / Référence perçage restent communs et toujours visibles
    # (l'activation/désactivation est désormais un réglage par colonne). Dans
    # chaque volet « Colonne NN », le masquage bas/haut n'a de sens que si
    # cette colonne n'est pas réglée sur Off.
    # Le tableau Percçage 32 n'a plus besoin de logique de visibilite
    # conditionnelle : toutes les cellules restent visibles en permanence.


    # Jeu/Epaisseur facade restent toujours visibles desormais (le
    # Nombre de tiroirs est par colonne/niche, plus un champ global
    # unique dont l'etat pourrait piloter cette visibilite).


# ---------------------------------------------------------------------------
# run / stop
# ---------------------------------------------------------------------------

# Dossier Google Drive ou l'utilisateur depose les nouvelles versions
# (voir _check_and_apply_updates). Copie simplement les .py plus
# recents (mtime) vers le dossier de l'add-in ; ne prend effet
# qu'apres un arret/relancement de l'add-in (le code deja charge en
# memoire pour CETTE session ne peut pas etre change a chaud).
UPDATE_FILES = (
    'MeubleParametrique.py', 'meuble_layout.py', 'meuble_geometry.py',
    'meuble_persistence.py')


def _extract_version(file_path):
    """Extrait le numero de version (ADDIN_VERSION = 'N.NN') d'un
    fichier MeubleParametrique.py donne, sans l'importer (simple
    lecture texte + regex). Renvoie None si non trouve/illisible."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            contenu = f.read()
        m = re.search(r"ADDIN_VERSION\s*=\s*['\"]([\d.]+)['\"]", contenu)
        return m.group(1) if m else None
    except Exception:
        return None


def _find_drive_update_folder():
    """Cherche le dossier Google Drive ADD-IN/MeubleParametrique, sur
    Windows (lettre de lecteur, generalement H: ou G:) ou sur Mac
    (Google Drive Desktop monte sous ~/Library/CloudStorage/
    GoogleDrive-<compte>/Mon Drive ou .../My Drive). Renvoie le
    premier chemin trouve, ou None si aucun n'existe."""
    candidats = []
    for lettre in ('H', 'G', 'I', 'F'):
        candidats.append(
            lettre + ':\\Mon Drive\\ADD-IN\\MeubleParametrique')
    cloud_storage = os.path.expanduser('~/Library/CloudStorage')
    if os.path.isdir(cloud_storage):
        for entry in os.listdir(cloud_storage):
            if entry.startswith('GoogleDrive'):
                for sous_dossier in ('Mon Drive', 'My Drive'):
                    candidats.append(os.path.join(
                        cloud_storage, entry, sous_dossier,
                        'ADD-IN', 'MeubleParametrique'))
    for c in candidats:
        if os.path.isdir(c):
            return c
    return None


GITHUB_RAW_BASE = (
    'https://raw.githubusercontent.com/atelier529ebeniste-byte/'
    'MeubleParametrique/main/MeubleParametrique/')
GITHUB_ETAG_MARKER = os.path.join(SCRIPT_DIR, '.github_sync_state.json')


def _check_and_apply_updates_drive():
    """Compare la date de modification de chaque fichier source entre
    le dossier Drive et le dossier de l'add-in ; copie les versions
    plus recentes trouvees sur le Drive. Renvoie la liste des noms de
    fichiers effectivement mis a jour (liste vide si rien a faire, ou
    si aucun dossier Drive synchronise n'est trouve localement)."""
    import shutil
    updated = []
    drive_folder = _find_drive_update_folder()
    if not drive_folder:
        return updated
    for fname in UPDATE_FILES:
        src = os.path.join(drive_folder, fname)
        dst = os.path.join(SCRIPT_DIR, fname)
        try:
            if not os.path.isfile(src):
                continue
            if (not os.path.isfile(dst)
                    or os.path.getmtime(src) > os.path.getmtime(dst) + 1.0):
                shutil.copy2(src, dst)
                updated.append(fname)
        except Exception:
            pass
    return updated


def _check_and_apply_updates_github():
    """Repli utilise quand aucun dossier Google Drive synchronise
    n'est trouve localement (ex. machine sans Google Drive Desktop) :
    telecharge chaque fichier source depuis le depot GitHub public et
    compare son ETag (empreinte de version HTTP) au dernier ETag deja
    synchronise pour ce fichier (fichier marqueur local JSON) -- ne
    remplace QUE si l'ETag a change depuis la derniere synchro, pour
    eviter de re-ecrire inutilement a chaque demarrage. Renvoie la
    liste des fichiers mis a jour (vide si rien a faire ou si pas
    d'acces internet)."""
    import urllib.request
    import json
    updated = []
    etat = {}
    if os.path.isfile(GITHUB_ETAG_MARKER):
        try:
            with open(GITHUB_ETAG_MARKER, 'r', encoding='utf-8') as f:
                etat = json.load(f)
        except Exception:
            etat = {}
    for fname in UPDATE_FILES:
        dst = os.path.join(SCRIPT_DIR, fname)
        try:
            req = urllib.request.Request(GITHUB_RAW_BASE + fname)
            with urllib.request.urlopen(req, timeout=4) as resp:
                etag_distant = resp.headers.get('ETag', '')
                distant = resp.read()
            if not distant:
                continue
            deja_synchro = etat.get(fname) == etag_distant
            if not deja_synchro:
                with open(dst, 'wb') as f:
                    f.write(distant)
                etat[fname] = etag_distant
                updated.append(fname)
        except Exception:
            pass
    if updated:
        try:
            with open(GITHUB_ETAG_MARKER, 'w', encoding='utf-8') as f:
                json.dump(etat, f)
        except Exception:
            pass
    return updated


def _check_and_apply_updates():
    """Point d'entree unique : essaie d'abord un dossier Google Drive
    synchronise localement (rapide, pas de reseau) ; si aucun n'est
    trouve, se rabat sur le depot GitHub public via internet."""
    if _find_drive_update_folder():
        return _check_and_apply_updates_drive()
    return _check_and_apply_updates_github()


def run(context):
    global app, ui
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        _maj = _check_and_apply_updates()
        if _maj:
            ui.messageBox(
                'Nouvelle version trouvee sur le Drive et copiee :\n'
                + '\n'.join(_maj)
                + "\n\nArretez puis relancez l'add-in (Scripts et "
                + "complements) pour l'appliquer.")

        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        panel_create = workspace.toolbarPanels.itemById(PANEL_ID_CREATE)

        cmd_defs = ui.commandDefinitions

        existing = cmd_defs.itemById(CMD_ID_CREATE)
        if existing:
            existing.deleteMe()
        cmd_def_create = cmd_defs.addButtonDefinition(
            CMD_ID_CREATE, CMD_NAME_CREATE, CMD_TOOLTIP_CREATE, RESOURCE_FOLDER_MEUBLE)
        on_created_create = CreateCommandCreatedHandler()
        cmd_def_create.commandCreated.add(on_created_create)
        handlers.append(on_created_create)
        control_create = panel_create.controls.itemById(CMD_ID_CREATE)
        if not control_create:
            control_create = panel_create.controls.addCommand(cmd_def_create)
        control_create.isPromoted = True
        control_create.isPromotedByDefault = True

    except Exception:
        if ui:
            ui.messageBox('Erreur au démarrage :\n{}'.format(traceback.format_exc()))


def stop(context):
    try:
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        panel_create = workspace.toolbarPanels.itemById(PANEL_ID_CREATE)
        control = panel_create.controls.itemById(CMD_ID_CREATE)
        if control:
            control.deleteMe()
        cmd_def = ui.commandDefinitions.itemById(CMD_ID_CREATE)
        if cmd_def:
            cmd_def.deleteMe()
    except Exception:
        if ui:
            ui.messageBox("Erreur à l'arrêt :\n{}".format(traceback.format_exc()))
    finally:
        # Purge les sous-modules du cache Python (sys.modules) : sans
        # cela, un simple 'Recharger l'add-in' dans Fusion reutilise le
        # bytecode DEJA charge de meuble_layout/meuble_geometry/
        # meuble_persistence, meme si les fichiers .py ont change sur le
        # disque -- seul MeubleParametrique.py lui-meme est effectivement
        # relu. Bug reel constate : des corrections dans ces sous-
        # modules n'avaient aucun effet apres un simple rechargement.
        for _mod_name in ('meuble_layout', 'meuble_geometry', 'meuble_persistence'):
            if _mod_name in sys.modules:
                del sys.modules[_mod_name]


# ---------------------------------------------------------------------------
# Commande Créer / Modifier (unifiée) : la liste déroulante Meuble choisit
# entre « Nouveau meuble » et un meuble déjà généré à modifier.
# ---------------------------------------------------------------------------

class CreateCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        try:
            cmd = args.command
            inputs = cmd.commandInputs
            design = get_design()
            root = design.rootComponent if design else None

            try:
                _check_and_apply_updates()
            except Exception:
                pass
            _logo_path = os.path.join(SCRIPT_DIR, 'resources', 'logo_atelier_10pct.png')
            if os.path.isfile(_logo_path):
                inputs.addImageCommandInput('imageLogoAtelier', '', _logo_path)
            # ADDIN_VERSION = version du code ACTUELLEMENT actif (celui
            # charge en memoire pour cette session) ; peut differer de
            # la version sur disque si une MAJ vient d'etre telechargee
            # mais que l'add-in n'a pas encore ete relance.
            _version_disque_ici = _extract_version(
                os.path.join(SCRIPT_DIR, 'MeubleParametrique.py'))
            _libelle_version = 'Meuble Paramétrique V ' + ADDIN_VERSION
            if _version_disque_ici and _version_disque_ici != ADDIN_VERSION:
                _libelle_version += ' (Mise à jour disponible)'
            _txt_version = inputs.addTextBoxCommandInput(
                'textVersionAddin', '', _libelle_version, 1, True)
            inputs.addBoolValueInput(
                'buttonApercu', 'Aperçu (aff./masq.)', False, '', False)

            dd_meuble = inputs.addDropDownCommandInput(
                'dropdownMeuble', 'Meuble', adsk.core.DropDownStyles.TextListDropDownStyle)
            dd_meuble.listItems.add(NOUVEAU_MEUBLE, True)
            existing_meubles = list_existing(root, MEUBLE_PREFIX) if root else []
            for occ in existing_meubles:
                dd_meuble.listItems.add(occ.component.name, False)

            # 'Nouveau meuble' doit repartir des valeurs par defaut
            # personnalisees (bouton 'Enregistrer par defaut'), pas de
            # valeurs codees en dur : default_values_dict() les fusionne
            # deja avec les defauts de base.
            _dflt_nouveau = default_values_dict()

            def cur_mm(key, fallback):
                return _dflt_nouveau.get(key, fallback)

            add_meuble_fields(inputs, cur_mm)

            on_input_changed = CreateInputChangedHandler()
            cmd.inputChanged.add(on_input_changed)
            handlers.append(on_input_changed)

            on_execute = CreateExecuteHandler()
            cmd.execute.add(on_execute)
            handlers.append(on_execute)

            on_destroy = DestroyHandler()
            cmd.destroy.add(on_destroy)
            handlers.append(on_destroy)

            update_field_visibility(inputs)
        except Exception:
            if ui:
                ui.messageBox('Erreur création commande :\n{}'.format(traceback.format_exc()))


class CreateInputChangedHandler(adsk.core.InputChangedEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        try:
            full_inputs = args.firingEvent.sender.commandInputs
            if args.input.id == 'dropdownMeuble':
                apply_meuble_selection(full_inputs)
            elif args.input.id == 'dropdownPreset':
                dd_p = full_inputs.itemById('dropdownPreset')
                if dd_p and dd_p.selectedItem and dd_p.selectedItem.name != NO_PRESET_LABEL:
                    _presets = load_presets()
                    _data = _presets.get(dd_p.selectedItem.name)
                    if _data:
                        apply_meuble_selection(full_inputs, override_values=_data)
            elif args.input.id in ('buttonRafraichir', 'intNbMontants', 'dropdownMontantsMode',
                                   'dropdownEtageresFixeMode'):
                # Reconstruction complète (montants + les 3 onglets par
                # colonne), déclenchée par le bouton Rafraîchir unique (en
                # bas de la boîte de dialogue) ou par un changement réel du
                # Nombre de montants.
                refresh_computed_fields(full_inputs)
                update_apercu(full_inputs)
            elif args.input.id == 'buttonEnregistrerDefaut':
                save_current_as_default(full_inputs)
            elif args.input.id == 'buttonApercu':
                pal = ui.palettes.itemById(APERCU_PALETTE_ID)
                if not pal:
                    pal = ui.palettes.add(
                        APERCU_PALETTE_ID, 'Aperçu meuble', APERCU_HTML_PATH,
                        True, True, True, 420, 560)
                    pal.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
                if pal.isVisible:
                    pal.isVisible = False
                else:
                    # Force un rechargement complet du HTML (le
                    # navigateur interne de la palette met le fichier
                    # en cache et ne reprend pas les modifications tant
                    # que l'URL ne change pas ; les suffixes ?/# sont
                    # refuses par Fusion pour un fichier local) : on
                    # copie le html sous un nom UNIQUE a chaque
                    # ouverture, pour garantir une url differente et
                    # donc un vrai rechargement.
                    try:
                        pal.htmlFileURL = _copie_apercu_html_unique()
                    except Exception:
                        pass
                    pal.isVisible = True
                    update_apercu(full_inputs)
                _btn_apercu = full_inputs.itemById('buttonApercu')
                if _btn_apercu and _btn_apercu.value:
                    _btn_apercu.value = False
            elif args.input.id == 'checkPresetSaveAs':
                save_current_preset(full_inputs)
            elif args.input.id == 'checkPresetDelete':
                delete_current_preset(full_inputs)
            elif args.input.id == 'buttonAppliquer':
                apply_button_clicked(args)
            elif args.input.id in ('checkSocleActif', 'checkCharniereAuto', 'checkCoupeOnglet'):
                update_field_visibility(full_inputs)
            elif (args.input.id.startswith('dropdownPercage32Colonne')
                  and args.input.id.endswith('Systeme')):
                update_field_visibility(full_inputs)
            elif (args.input.id.startswith('tablePortesCol')
                  and 'Choix' in args.input.id):
                update_field_visibility(full_inputs)
                # Une porte active interdit les tiroirs sur la meme
                # niche : reconstruit le tableau Tiroirs pour refleter
                # immediatement ce changement (pas besoin de cliquer
                # Rafraichir).
                _int_m_pc = full_inputs.itemById('intNbMontants')
                _group_portes_pc = full_inputs.itemById('groupPortesColonnes')
                _group_tiroirs_pc = full_inputs.itemById('groupTiroirsColonnes')
                if _int_m_pc and _group_portes_pc and _group_tiroirs_pc:
                    _existing_portes_pc = read_portes_tables(
                        _group_portes_pc.children, _int_m_pc.value + 1, inputs=full_inputs)
                    _existing_tiroirs_pc = read_tiroirs_tables(
                        _group_tiroirs_pc.children, _int_m_pc.value + 1, inputs=full_inputs)
                    rebuild_tiroirs_tables(
                        _group_tiroirs_pc.children, _int_m_pc.value + 1,
                        _existing_tiroirs_pc, inputs=full_inputs,
                        portes_colonnes=_existing_portes_pc)
                # Le tableau Prise de main liste aussi les portes
                # (colonne/niche) : le reconstruire pour refleter
                # immediatement ce changement de Choix.
                _tab_pm_pc = full_inputs.itemById('tabPriseMain')
                if _tab_pm_pc and _int_m_pc:
                    _epp_pc = read_portes_tables(
                        full_inputs, _int_m_pc.value + 1, inputs=full_inputs)
                    _ept_pc = read_tiroirs_tables(
                        full_inputs, _int_m_pc.value + 1, inputs=full_inputs)
                    _pmp_pc, _pmt_pc = read_prise_main_table(
                        full_inputs, _int_m_pc.value + 1)
                    rebuild_prise_main_table(
                        _tab_pm_pc.children, _int_m_pc.value + 1,
                        _epp_pc, _ept_pc, _pmp_pc, _pmt_pc, inputs=full_inputs)
            elif (args.input.id.startswith('intEtageresColonne')
                  and args.input.id.endswith('NbEtageres')):
                update_field_visibility(full_inputs)
            elif (args.input.id.startswith('intEtageresFixeColonne')
                  and args.input.id.endswith('NbEtageres')):
                try:
                    col_i = int(args.input.id[len('intEtageresFixeColonne'):-len('NbEtageres')])
                except ValueError:
                    col_i = None
                if col_i:
                    refresh_etagere_fixe_colonne(full_inputs, col_i)
            elif (args.input.id.startswith('tableTirCol')
                  and (args.input.id[13:].startswith('Nb')
                       or args.input.id[13:].startswith('Mode'))):
                # Reconstruction CIBLEE (tableau 3 uniquement, voir
                # refresh_tiroirs_niche_detail) : ne touche jamais au
                # tableau qui contient le controle declencheur lui-meme.
                try:
                    suffixe = args.input.id[13:]
                    col_i_t = int(args.input.id[11:13])
                    if suffixe.startswith('Nb'):
                        niche_idx_t = int(suffixe[len('Nb'):])
                    else:
                        niche_idx_t = int(suffixe[len('Mode'):])
                except ValueError:
                    col_i_t = None
                    niche_idx_t = None
                if col_i_t is not None and niche_idx_t is not None:
                    refresh_tiroirs_niche_detail(full_inputs, col_i_t, niche_idx_t)
                # Idem : le nombre de tiroirs d'une niche change la
                # liste des facades listees dans Prise de main.
                _int_m_td = full_inputs.itemById('intNbMontants')
                _tab_pm_td = full_inputs.itemById('tabPriseMain')
                if _tab_pm_td and _int_m_td:
                    _epp_td = read_portes_tables(
                        full_inputs, _int_m_td.value + 1, inputs=full_inputs)
                    _ept_td = read_tiroirs_tables(
                        full_inputs, _int_m_td.value + 1, inputs=full_inputs)
                    _pmp_td, _pmt_td = read_prise_main_table(
                        full_inputs, _int_m_td.value + 1)
                    rebuild_prise_main_table(
                        _tab_pm_td.children, _int_m_td.value + 1,
                        _epp_td, _ept_td, _pmp_td, _pmt_td, inputs=full_inputs)
        except Exception:
            if ui:
                ui.messageBox("Erreur mise à jour de l'interface :\n{}".format(traceback.format_exc()))


class CreateExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        progress = None
        new_occ_for_cleanup = None
        try:
            inputs = args.command.commandInputs
            design = get_design()
            if not design:
                return
            root = design.rootComponent
            dd = inputs.itemById('dropdownMeuble')
            selection = dd.selectedItem.name if (dd and dd.selectedItem) else NOUVEAU_MEUBLE

            if not apply_design_mode(design, 'parametrique'):
                return

            values = collect_values_mm(inputs)

            # Garde-fou : valide la combinaison de valeurs AVANT de toucher a
            # l'arbre (creation ou effacement de geometrie), pour ne jamais
            # laisser un meuble a moitie construit ou vide sur une erreur.
            try:
                compute_layout(values)
            except MeubleLayoutError as e:
                if ui:
                    ui.messageBox(str(e), 'Valeurs incompatibles')
                return

            if ui:
                progress = ui.createProgressDialog()
                progress.isCancelButtonShown = True
                progress.show('Meuble Paramétrique', 'Génération en cours...', 0, 1, 0)

            if selection == NOUVEAU_MEUBLE:
                existing_max_x_cm = next_x_offset_cm(root, MEUBLE_PREFIX)
                if existing_max_x_cm is None:
                    x_offset_cm = 0.0
                else:
                    x_offset_cm = existing_max_x_cm + mm_to_cm(values['gap_mm'])

                transform = adsk.core.Matrix3D.create()
                transform.translation = adsk.core.Vector3D.create(x_offset_cm, 0, 0)

                try:
                    start_index = design.timeline.count
                except Exception:
                    start_index = None

                new_occ = root.occurrences.addNewComponent(transform)
                comp = new_occ.component
                comp.name = next_component_name(root, MEUBLE_PREFIX)
                new_occ_for_cleanup = new_occ

                generate_meuble(root, design, values, comp, transform, progress)
                new_occ_for_cleanup = None

                if start_index is not None:
                    group_timeline_range(design, start_index, comp.name)
            else:
                if not apply_changes_to_existing_meuble(design, root, values, selection, progress):
                    return

            if ui:
                app.activeViewport.refresh()
        except GenerationCancelled:
            if new_occ_for_cleanup is not None:
                try:
                    new_occ_for_cleanup.deleteMe()
                except Exception:
                    pass
                if ui:
                    ui.messageBox('Génération annulée.', 'Meuble Paramétrique')
            else:
                if ui:
                    ui.messageBox(
                        'Génération annulée : le meuble modifié est temporairement '
                        'vide. Utilise Annuler (Ctrl+Z) pour restaurer sa géométrie '
                        'précédente si besoin.', 'Meuble Paramétrique')
        except Exception:
            if ui:
                ui.messageBox("Erreur à l'exécution :\n{}".format(traceback.format_exc()))
        finally:
            if progress is not None:
                try:
                    progress.hide()
                except Exception:
                    pass


class DestroyHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        pass
