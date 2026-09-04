# meuble_geometry.py -- creation de geometrie Fusion (esquisses/extrusions)
# et parametres utilisateur Fusion. Depend de l'API Fusion (adsk) et de
# meuble_layout (compute_layout, mm_to_cm).

import adsk.core
import adsk.fusion
import re

import math

from meuble_layout import compute_layout, mm_to_cm, ANGLE_OUVERTURE_PORTE_DEG

# Charnieres Inserta Blum : memes cotes par defaut que l'add-in Porte
# Inserta Blum (FIELDS_AVANCES), pour rester compatible avec le materiel
# reellement utilise.
CHARNIERE_DIST_BOITIER_MM = 5.0
CHARNIERE_DIAM_MM = 35.0
CHARNIERE_PROF_MM = 13.0
CHARNIERE_DIAM_INSERTA_MM = 8.0
# Entraxe reel du materiel Inserta Blum (voir add-in Porte Inserta
# Blum), independant du pas systeme 32. L'AXE de la charniere
# (perÃ§age 35mm), lui, est ancre a mi-chemin entre 2 trous systeme 32
# adjacents -- voir hinge_positions_locales_mm dans meuble_layout --
# mais les 2 chevilles Inserta a +/-22.5mm de cet axe ne tombent pas
# necessairement sur des trous systeme 32 elles-memes.
CHARNIERE_ENTREAXE_INSERTA_MM = 45.0
CHARNIERE_DIST_INSERTA_MM = 9.5

# Materiau physique applique par defaut a tous les panneaux/portes
# generes (bibliotheque Fusion standard).
MATERIAU_PAR_DEFAUT_LIB = 'Bibliothèque de matériaux Fusion'
MATERIAU_PAR_DEFAUT_NOM = 'Aggloméré'
_materiau_cache = {}


def _get_or_add_material(design, lib_name, mat_name):
    # Recupere le materiau deja copie dans le document si present, sinon
    # le copie depuis la bibliotheque Fusion vers le document (une
    # seule fois, mis en cache ensuite). N'echoue jamais silencieusement
    # que sur un document/bibliotheque introuvable : l'appelant doit
    # tolerer un retour None (pas de materiau applique).
    key = (id(design), mat_name)
    if key in _materiau_cache:
        return _materiau_cache[key]
    try:
        existing = design.materials.itemByName(mat_name)
        if existing:
            _materiau_cache[key] = existing
            return existing
        app = adsk.core.Application.get()
        lib = app.materialLibraries.itemByName(lib_name)
        if not lib:
            return None
        lib_mat = lib.materials.itemByName(mat_name)
        if not lib_mat:
            return None
        added = design.materials.addByCopy(lib_mat, mat_name)
        _materiau_cache[key] = added
        return added
    except Exception:
        return None


def apply_default_material(body):
    # Applique le materiau par defaut (voir MATERIAU_PAR_DEFAUT_NOM) au
    # corps donne. N'echoue jamais la generation si le materiau ou le
    # document ne sont pas accessibles pour une raison quelconque.
    try:
        design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
        if not design:
            return
        mat = _get_or_add_material(design, MATERIAU_PAR_DEFAUT_LIB, MATERIAU_PAR_DEFAUT_NOM)
        if mat:
            body.material = mat
    except Exception:
        pass


class GenerationCancelled(Exception):
    # Leve depuis _tick quand l'utilisateur clique Annuler sur la barre de
    # progression (voir CreateExecuteHandler cote MeubleParametrique.py).
    pass


def _tick(progress, step, label=None):
    # progress est un adsk.core.ProgressDialog (ou None : generation sans
    # barre de progression, ex. depuis les tests ou un import direct du
    # module). Ne fait jamais planter la generation pour une raison liee a
    # la barre elle-meme.
    if progress is None:
        return
    try:
        if label:
            progress.message = label
        progress.progressValue = step
        cancelled = progress.wasCancelled
    except Exception:
        return
    if cancelled:
        raise GenerationCancelled()


# ---------------------------------------------------------------------------
# Briques géométriques de base
#
# Toutes les esquisses sont posées sur des plans PARALLÈLES aux plans de base
# du composant (XY ou XZ), avec un décalage de départ d'extrusion
# (OffsetStartDefinition) plutôt que des plans de construction : cela évite
# de multiplier les plans dans l'arbre de construction.
#
# Vérifié empiriquement dans Fusion : sur le plan XZ, l'axe V de l'esquisse
# correspond à -Z (et non +Z) ; add_panel_xz compense ce signe en interne.
# ---------------------------------------------------------------------------

def _largest_profile(sketch):
    """Renvoie le profil de plus grande aire dans 'sketch' (au lieu du
    1er, sketch.profiles.item(0)) : quand plusieurs esquisses coincident
    sur le meme plan de construction partage (ex. plusieurs fonds de
    tiroir empiles, meme empreinte XY, tous sur le plan XY a z=0), le
    profil peut se fragmenter et le 1er profil trouve n'est pas
    forcement le rectangle complet voulu -- l'extrusion suivante ne
    couvre alors pas toute la geometrie attendue."""
    best = sketch.profiles.item(0)
    best_area = best.areaProperties().area
    for i in range(1, sketch.profiles.count):
        p = sketch.profiles.item(i)
        a = p.areaProperties().area
        if a > best_area:
            best, best_area = p, a
    return best


def cut_miter_wedge_xz(comp, target_body, tri_xz, y0, y1, name):
    """Coupe un prisme triangulaire (coupe d'onglet 45 degres) dans
    'target_body' : profil triangulaire 'tri_xz' = liste de 3 (x,z)
    (cm), extrude sur toute la plage Y [y0,y1] (profondeur). Plan
    xZConstructionPlane, mapping mesure empiriquement : esquisse
    (u,v) -> monde (x=u, y=0, z=-v)."""
    def uv(x, z):
        return adsk.core.Point3D.create(x, -z, 0)
    sketch = comp.sketches.add(comp.xZConstructionPlane)
    sketch.name = name
    lines = sketch.sketchCurves.sketchLines
    p1 = uv(*tri_xz[0])
    p2 = uv(*tri_xz[1])
    p3 = uv(*tri_xz[2])
    lines.addByTwoPoints(p1, p2)
    lines.addByTwoPoints(p2, p3)
    lines.addByTwoPoints(p3, p1)
    profile = _largest_profile(sketch)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.CutFeatureOperation)
    ext_input.participantBodies = [target_body]
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(y0))
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(y1 - y0))
    extrudes.add(ext_input)


def _supprimer_esquisses_homonymes(comp, name):
    """Supprime toute esquisse existante portant deja ce nom, avant
    d'en creer une nouvelle. Filet de securite : si un nettoyage
    precedent (clear_component_geometry) a echoue partiellement a
    supprimer une esquisse (ex. reference perdue apres modification
    manuelle), une esquisse residuelle homonyme peut fausser la
    detection de profil (_largest_profile) ou faire echouer
    l'extrusion avec une erreur generique."""
    for i in range(comp.sketches.count - 1, -1, -1):
        sk = comp.sketches.item(i)
        if sk.name == name:
            try:
                sk.deleteMe()
            except Exception:
                pass


def add_panel_xy(comp, x0, x1, y0, y1, z_start, z_extent, name, thickness_param=None):
    """Esquisse un rectangle (x0,y0)-(x1,y1) sur le plan XY et l'extrude
    selon Z, à partir de z_start sur une hauteur z_extent. Coordonnées en cm."""
    _supprimer_esquisses_homonymes(comp, name)
    sketch = comp.sketches.add(comp.xYConstructionPlane)
    sketch.name = name
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(x0, y0, 0), adsk.core.Point3D.create(x1, y1, 0))
    profile = _largest_profile(sketch)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(z_start))
    if thickness_param:
        ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByString(thickness_param))
    else:
        ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(z_extent))
    try:
        extrude = extrudes.add(ext_input)
    except Exception:
        # Repli : si la reference au parametre Fusion (thickness_param)
        # pose probleme (ex. parametre incoherent apres modification
        # d'un meuble existant), retente avec une valeur numerique
        # directe -- le panneau garde alors sa bonne epaisseur mais
        # perd le pilotage direct par le parametre Fusion (redevient
        # correct au prochain Appliquer complet).
        if thickness_param:
            ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(z_extent))
            extrude = extrudes.add(ext_input)
        else:
            raise
    body = extrude.bodies.item(0)
    body.name = name
    apply_default_material(body)
    return body


def add_panel_xz(comp, x0, x1, z0, z1, y_start, y_extent, name, thickness_param=None):
    """Esquisse un rectangle sur le plan XZ (u=X, v=-Z) et l'extrude selon Y,
    à partir de y_start sur une profondeur y_extent. Coordonnées en cm."""
    _supprimer_esquisses_homonymes(comp, name)
    sketch = comp.sketches.add(comp.xZConstructionPlane)
    sketch.name = name
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(x0, -z1, 0), adsk.core.Point3D.create(x1, -z0, 0))
    profile = _largest_profile(sketch)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(y_start))
    if thickness_param:
        ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByString(thickness_param))
    else:
        ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(y_extent))
    try:
        extrude = extrudes.add(ext_input)
    except Exception:
        if thickness_param:
            ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(y_extent))
            extrude = extrudes.add(ext_input)
        else:
            raise
    body = extrude.bodies.item(0)
    body.name = name
    apply_default_material(body)
    return body


def drill_hole_x(comp, x_plane, sign, y_center, z_center, diam, depth, name):
    """Perce un trou cylindrique borgne axé selon X (assemblage montant/
    traverse), de diamètre 'diam' et profondeur 'depth', centré en
    (y_center, z_center), démarrant au plan x=x_plane et creusant vers +X
    (sign=+1) ou -X (sign=-1). Coordonnées en cm.

    Vérifié empiriquement dans Fusion : sur le plan YZ, l'axe U de l'esquisse
    correspond à -Z et l'axe V à +Y (comme pour add_panel_xz sur le plan XZ,
    un seul axe est inversé par rapport à l'intuition)."""
    sketch = comp.sketches.add(comp.yZConstructionPlane)
    sketch.name = name
    r = diam / 2.0
    sketch.sketchCurves.sketchCircles.addByCenterRadius(
        adsk.core.Point3D.create(-z_center, y_center, 0), r)
    profile = _largest_profile(sketch)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.CutFeatureOperation)
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(x_plane))
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(sign * depth))
    extrudes.add(ext_input)


def cut_groove_x(comp, x_plane, sign, y0, y1, z0, z1, depth, name, target_body=None):
    """Usine une rainure rectangulaire axee selon X (fond de tiroir
    capte dans un cote), de profondeur 'depth', couvrant (y0,y1) x
    (z0,z1), demarrant au plan x=x_plane et creusant vers +X (sign=+1)
    ou -X (sign=-1). Coordonnees en cm. Meme convention d'esquisse que
    drill_hole_x (plan YZ, U=-Z, V=+Y), juste un rectangle au lieu d'un
    cercle. Si 'target_body' est fourni, la coupe est restreinte a CE
    seul corps (participantBodies) : sans cette restriction, Fusion
    coupe TOUS les corps qui touchent/recoupent l'outil de coupe, ce
    qui grignotait aussi le fond du tiroir (coincident avec le fond de
    la rainure) en plus du cote vise."""
    sketch = comp.sketches.add(comp.yZConstructionPlane)
    sketch.name = name
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(-z0, y0, 0), adsk.core.Point3D.create(-z1, y1, 0))
    profile = _largest_profile(sketch)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.CutFeatureOperation)
    if target_body is not None:
        ext_input.participantBodies = [target_body]
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(x_plane))
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(sign * depth))
    extrudes.add(ext_input)


# Prise de doigt : profil exact analyse sur le modele de reference
# (esquisse dediee) -- 2 cercles relies par une tangente commune : le
# petit ("boudin", R1,5, cote face avant) forme une BOUCLE (grand arc,
# plus de 180 degres, la matiere s'enroule autour), le grand (R5, cote
# fond de la rainure/plafond) prend le petit arc normal.
PRISE_DOIGT_R1_CM = 0.15   # boudin (cote face avant)
PRISE_DOIGT_R1_Y_CM = 0.15   # distance de son centre a la face avant
PRISE_DOIGT_R1_Z_CM = 1.45   # distance de son centre au chant
PRISE_DOIGT_R2_CM = 0.5    # grand cercle (cote plafond de la rainure)
PRISE_DOIGT_R2_Y_CM = 1.0    # distance de son centre a la face avant
PRISE_DOIGT_R2_Z_CM = 2.0    # distance de son centre au chant


def _tangente_interne_cercles(c1, r1, c2, r2, cote):
    """Points de tangence (T1 sur le cercle 1, T2 sur le cercle 2)
    d'une tangente commune INTERNE (les 2 cercles de part et d'autre de
    la tangente) a 2 cercles de centres/rayons differents. C'est cette
    configuration (pas la tangente externe) qui produit un profil en S
    classique (ogee) : un cercle concave, l'autre convexe. 'cote' = +1
    ou -1 choisit laquelle des 2 tangentes internes possibles."""
    dx, dy = c2[0] - c1[0], c2[1] - c1[1]
    d = math.hypot(dx, dy)
    phi = math.atan2(dy, dx)
    alpha = math.asin((r1 + r2) / d)
    beta = phi + cote * (math.pi / 2.0 - alpha)
    t1 = (c1[0] + r1 * math.cos(beta), c1[1] + r1 * math.sin(beta))
    t2 = (c2[0] - r2 * math.cos(beta), c2[1] - r2 * math.sin(beta))
    return t1, t2


def _angle_vers(centre, point):
    return math.atan2(point[1] - centre[1], point[0] - centre[0])


def _sweep_court(a_debut, a_fin):
    da = a_fin - a_debut
    while da <= -math.pi:
        da += 2 * math.pi
    while da > math.pi:
        da -= 2 * math.pi
    return da


def _sweep_long(a_debut, a_fin):
    court = _sweep_court(a_debut, a_fin)
    return court - 2 * math.pi * (1 if court > 0 else -1)


def _profil_prise_doigt(y_front, z_edge, sens, cote=1):
    """Calcule le profil complet de la prise de doigt (en (y, z)
    absolus) : chant/face avant, tangent au boudin (grand arc), tangente
    commune, tangent au grand cercle (petit arc), jusqu'au chant/plafond,
    et retour au chant. 'z_edge' est la position Z du chant ; 'sens'
    vaut -1 pour un chant du HAUT (creuse vers le bas) ou +1 pour un
    chant du BAS (creuse vers le haut). 'cote' choisit laquelle des 2
    tangentes internes (voir _tangente_interne_cercles) -- change quel
    cercle apparait convexe/concave selon le plan de projection utilise
    ensuite. Renvoie un dict de points/centres/angles pretes pour
    addByCenterStartSweep."""
    def pt(y, z_off):
        return (y, z_edge + sens * z_off)
    c1 = (y_front + PRISE_DOIGT_R1_Y_CM, z_edge + sens * PRISE_DOIGT_R1_Z_CM)
    c2 = (y_front + PRISE_DOIGT_R2_Y_CM, z_edge + sens * PRISE_DOIGT_R2_Z_CM)
    t1, t2 = _tangente_interne_cercles(c1, PRISE_DOIGT_R1_CM, c2, PRISE_DOIGT_R2_CM, cote)
    p_front = pt(y_front, PRISE_DOIGT_R1_Z_CM)
    p_ceil = pt(y_front + PRISE_DOIGT_R2_CM + PRISE_DOIGT_R2_Y_CM, PRISE_DOIGT_R2_Z_CM)
    a1_debut = _angle_vers(c1, p_front)
    a1_fin = _angle_vers(c1, t1)
    a2_debut = _angle_vers(c2, t2)
    a2_fin = _angle_vers(c2, p_ceil)
    return {
        'edge_front': pt(y_front, 0),
        'p_front': p_front,
        'c1': c1, 'r1': PRISE_DOIGT_R1_CM,
        'sweep1': _sweep_court(a1_debut, a1_fin) * (-sens),
        't1': t1, 't2': t2,
        'c2': c2, 'r2': PRISE_DOIGT_R2_CM,
        'sweep2': _sweep_court(a2_debut, a2_fin) * (-sens),
        'p_ceil': p_ceil,
        'edge_ceil': pt(y_front + PRISE_DOIGT_R2_CM + PRISE_DOIGT_R2_Y_CM, 0),
    }


def cut_prise_doigt_x(comp, x_plane, largeur, y_front, z_edge, sens, name, target_body=None):
    """Usine une prise de doigt sur un chant HORIZONTAL (facade de
    tiroir : toujours le chant du haut), en creusant selon X depuis
    x_plane sur 'largeur' (peut etre negative). 'y_front' est la
    position Y de la face avant du panneau ; 'z_edge'/'sens' voir
    _profil_prise_doigt. Esquisse sur le plan YZ, convention (u,v) =
    (-z, y) (comme cut_groove_x/drill_hole_x). Coupe restreinte a
    'target_body' si fourni."""
    prof = _profil_prise_doigt(y_front, z_edge, sens, cote=-sens)
    def uv(p):
        return adsk.core.Point3D.create(-p[1], p[0], 0)
    sketch = comp.sketches.add(comp.yZConstructionPlane)
    sketch.name = name
    lines = sketch.sketchCurves.sketchLines
    arcs = sketch.sketchCurves.sketchArcs
    lines.addByTwoPoints(uv(prof['edge_front']), uv(prof['p_front']))
    arcs.addByCenterStartSweep(uv(prof['c1']), uv(prof['p_front']), -sens * prof['sweep1'])
    lines.addByTwoPoints(uv(prof['t1']), uv(prof['t2']))
    arcs.addByCenterStartSweep(uv(prof['c2']), uv(prof['t2']), -sens * prof['sweep2'])
    lines.addByTwoPoints(uv(prof['p_ceil']), uv(prof['edge_ceil']))
    lines.addByTwoPoints(uv(prof['edge_ceil']), uv(prof['edge_front']))
    profile = _largest_profile(sketch)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.CutFeatureOperation)
    if target_body is not None:
        ext_input.participantBodies = [target_body]
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(x_plane))
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(largeur))
    extrudes.add(ext_input)


def cut_prise_doigt_porte_horizontal(comp, largeur, y_edge, sens, ep_cm, name, target_body=None):
    """Prise de main Haut/Bas sur une PORTE (composant imbrique de
    build_door_component, repere LOCAL : X=largeur, Y=hauteur
    (0=haut, croissant vers le bas), Z=epaisseur (0=face ARRIERE
    cote caisson, -ep_cm=face AVANT visible en applique)). Creuse
    selon X sur toute la largeur (0 -> largeur), depuis la face
    AVANT (-ep_cm) vers l'interieur. 'y_edge' = 0 pour le chant du
    HAUT (sens=+1, creuse vers le bas) ou hauteur_cm pour le chant
    du BAS (sens=-1, creuse vers le haut)."""
    prof = _profil_prise_doigt(-ep_cm, y_edge, sens, cote=-sens)
    def uv(p):
        return adsk.core.Point3D.create(-p[0], p[1], 0)
    sketch = comp.sketches.add(comp.yZConstructionPlane)
    sketch.name = name
    lines = sketch.sketchCurves.sketchLines
    arcs = sketch.sketchCurves.sketchArcs
    lines.addByTwoPoints(uv(prof['edge_front']), uv(prof['p_front']))
    arcs.addByCenterStartSweep(uv(prof['c1']), uv(prof['p_front']), sens * prof['sweep1'])
    lines.addByTwoPoints(uv(prof['t1']), uv(prof['t2']))
    arcs.addByCenterStartSweep(uv(prof['c2']), uv(prof['t2']), sens * prof['sweep2'])
    lines.addByTwoPoints(uv(prof['p_ceil']), uv(prof['edge_ceil']))
    lines.addByTwoPoints(uv(prof['edge_ceil']), uv(prof['edge_front']))
    profile = _largest_profile(sketch)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.CutFeatureOperation)
    if target_body is not None:
        ext_input.participantBodies = [target_body]
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(0.0))
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(largeur))
    extrudes.add(ext_input)


def cut_prise_doigt_porte_vertical(comp, hauteur, x_edge, sens, ep_cm, name, target_body=None):
    """Prise de main 'Oppose charnieres' sur une PORTE (repere LOCAL,
    voir cut_prise_doigt_porte_horizontal) : creuse selon Y (hauteur)
    sur toute la hauteur (0 -> hauteur), depuis la face AVANT
    (-ep_cm) vers l'interieur. 'x_edge' = 0 pour le chant GAUCHE
    (sens=+1, creuse vers la droite) ou largeur_cm pour le chant
    DROIT (sens=-1, creuse vers la gauche)."""
    prof = _profil_prise_doigt(-ep_cm, x_edge, sens, cote=-sens)
    def uv(p):
        return adsk.core.Point3D.create(p[1], -p[0], 0)
    sketch = comp.sketches.add(comp.xZConstructionPlane)
    sketch.name = name
    lines = sketch.sketchCurves.sketchLines
    arcs = sketch.sketchCurves.sketchArcs
    lines.addByTwoPoints(uv(prof['edge_front']), uv(prof['p_front']))
    arcs.addByCenterStartSweep(uv(prof['c1']), uv(prof['p_front']), -sens * prof['sweep1'])
    lines.addByTwoPoints(uv(prof['t1']), uv(prof['t2']))
    arcs.addByCenterStartSweep(uv(prof['c2']), uv(prof['t2']), -sens * prof['sweep2'])
    lines.addByTwoPoints(uv(prof['p_ceil']), uv(prof['edge_ceil']))
    lines.addByTwoPoints(uv(prof['edge_ceil']), uv(prof['edge_front']))
    profile = _largest_profile(sketch)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.CutFeatureOperation)
    if target_body is not None:
        ext_input.participantBodies = [target_body]
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(0.0))
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(hauteur))
    extrudes.add(ext_input)


def cut_prise_doigt_z(comp, z_plane, hauteur, y_front, x_edge, sens, name, target_body=None):
    """Usine une prise de doigt sur un chant VERTICAL (porte : cote
    oppose aux charnieres par defaut), en creusant selon Z depuis
    z_plane sur 'hauteur' (peut etre negative). 'y_front' est la
    position Y de la face avant du panneau ; 'x_edge' est la position X
    du chant (bord gauche ou droit de la porte) ; 'sens' vaut -1 si la
    rainure creuse vers les X decroissants (chant de DROITE) ou +1 si
    elle creuse vers les X croissants (chant de GAUCHE). Meme profil
    que cut_prise_doigt_x (voir _profil_prise_doigt), juste reoriente :
    esquisse sur le plan XY, mapping direct (u,v) = (x, y) (comme
    drill_hole_z). Coupe restreinte a 'target_body' si fourni."""
    prof = _profil_prise_doigt(y_front, x_edge, sens)
    def uv(p):
        return adsk.core.Point3D.create(p[1], p[0], 0)
    sketch = comp.sketches.add(comp.xYConstructionPlane)
    sketch.name = name
    lines = sketch.sketchCurves.sketchLines
    arcs = sketch.sketchCurves.sketchArcs
    lines.addByTwoPoints(uv(prof['edge_front']), uv(prof['p_front']))
    arcs.addByCenterStartSweep(uv(prof['c1']), uv(prof['p_front']), -prof['sweep1'])
    lines.addByTwoPoints(uv(prof['t1']), uv(prof['t2']))
    arcs.addByCenterStartSweep(uv(prof['c2']), uv(prof['t2']), -prof['sweep2'])
    lines.addByTwoPoints(uv(prof['p_ceil']), uv(prof['edge_ceil']))
    lines.addByTwoPoints(uv(prof['edge_ceil']), uv(prof['edge_front']))
    profile = _largest_profile(sketch)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.CutFeatureOperation)
    if target_body is not None:
        ext_input.participantBodies = [target_body]
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(z_plane))
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(hauteur))
    extrudes.add(ext_input)


def drill_hole_z(comp, z_plane, sign, x_center, y_center, diam, depth, name):
    """Perce un trou cylindrique borgne axé selon Z (assemblage montant
    intermédiaire/traverse), de diamètre 'diam' et profondeur 'depth', centré
    en (x_center, y_center), démarrant au plan z=z_plane et creusant vers +Z
    (sign=+1) ou -Z (sign=-1). Coordonnées en cm."""
    sketch = comp.sketches.add(comp.xYConstructionPlane)
    sketch.name = name
    r = diam / 2.0
    sketch.sketchCurves.sketchCircles.addByCenterRadius(
        adsk.core.Point3D.create(x_center, y_center, 0), r)
    profile = _largest_profile(sketch)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.CutFeatureOperation)
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(z_plane))
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(sign * depth))
    extrudes.add(ext_input)


def drill_holes_batch(comp, axis, plane, sign, diam, depth, centers, name):
    """Perce plusieurs trous cylindriques identiques (même diamètre, même
    profondeur, même face/plan/sens) en une seule esquisse (plusieurs
    cercles) et une seule extrusion-coupe (toutes les coupes sélectionnées à
    la fois), au lieu d'une paire esquisse/extrusion par trou. Fusion
    recalcule tout l'historique à chaque fonctionnalité ajoutée : regrouper
    ainsi les perçages identiques réduit fortement le nombre de
    fonctionnalités et accélère beaucoup la reconstruction sur les meubles à
    nombreux trous (perçage système 32 notamment). 'axis' vaut 'X' ou 'Z' ;
    centers est une liste de tuples (u_center, v_center) en cm — (y_center,
    z_center) pour l'axe X, (x_center, y_center) pour l'axe Z — mêmes
    conventions de plan que drill_hole_x / drill_hole_z."""
    r = diam / 2.0
    if axis == 'X':
        sketch = comp.sketches.add(comp.yZConstructionPlane)
        sketch.name = name
        for u_center, v_center in centers:
            sketch.sketchCurves.sketchCircles.addByCenterRadius(
                adsk.core.Point3D.create(-v_center, u_center, 0), r)
    else:
        sketch = comp.sketches.add(comp.xYConstructionPlane)
        sketch.name = name
        for u_center, v_center in centers:
            sketch.sketchCurves.sketchCircles.addByCenterRadius(
                adsk.core.Point3D.create(u_center, v_center, 0), r)
    profiles = adsk.core.ObjectCollection.create()
    for i in range(sketch.profiles.count):
        profiles.add(sketch.profiles.item(i))
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profiles, adsk.fusion.FeatureOperations.CutFeatureOperation)
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(plane))
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(sign * depth))
    extrudes.add(ext_input)


def clear_component_geometry(comp):
    """Supprime toutes les extrusions puis toutes les esquisses du composant,
    pour permettre une reconstruction propre (utilisé par Modifier).
    Tolerant aux references perdues (ex. modification manuelle du
    meuble entre 2 passages dans l'add-in, qui peut casser une
    fonction Combiner/RemoveBody plus loin dans l'historique) : une
    fonction qui echoue a se supprimer est ignoree (pas d'arret
    brutal), et les corps orphelins restants sont retires
    directement en dernier recours."""
    features = comp.features.extrudeFeatures
    for i in range(features.count - 1, -1, -1):
        try:
            features.item(i).deleteMe()
        except Exception:
            pass
    sketches = comp.sketches
    for i in range(sketches.count - 1, -1, -1):
        try:
            sketches.item(i).deleteMe()
        except Exception:
            pass
    # Dernier recours : si des fonctions n'ont pas pu etre supprimees
    # (reference perdue), leurs corps peuvent trainer -- on les
    # retire directement pour eviter les doublons a la reconstruction.
    bodies = comp.bRepBodies
    for i in range(bodies.count - 1, -1, -1):
        try:
            bodies.item(i).deleteMe()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Parametres utilisateur Fusion (visibles dans le panneau Parametres) : pour
# certaines epaisseurs UNIQUEMENT (celles qui correspondent directement a une
# distance d'extrusion, pas a une cote cachee dans les coordonnees d'un
# croquis), on cree un vrai parametre Fusion par meuble et on le reference
# depuis l'extrusion. Attention : modifier un de ces parametres directement
# dans le panneau Parametres (sans repasser par la boite de dialogue Meuble
# Parametrique) ne met a jour QUE les panneaux listes dans son commentaire --
# les cotes, montants et le fond restent pilotes par la reconstruction
# complete (bouton Appliquer/OK), et redeviennent coherents des le prochain
# clic sur Appliquer.
# ---------------------------------------------------------------------------

THICKNESS_PARAM_DEFS = {
    'EpPanneau': ('ep_panneau',
                  'Epaisseur des panneaux (mm). Pilote en direct : Dessus, '
                  'Dessous, etageres (fixes et mobiles), Plinthe. Ne pilote '
                  'PAS les cotes, montants intermediaires ni le fond (leur '
                  'epaisseur est encodee dans leur croquis) : pour une '
                  'epaisseur coherente partout, modifie Epaisseur panneaux '
                  'dans la boite de dialogue Meuble Parametrique puis '
                  'clique Appliquer.'),
    'EpFond': ('ep_fond_tiroir',
               'Epaisseur du fond de tiroir (mm), distincte de "Epaisseur '
               'fond" du caisson. Pilote uniquement le panneau Tiroir N '
               'Fond bas de chaque tiroir. Ne pilote pas le fond du '
               'caisson (panneau Fond).'),
    'EpFaceTiroir': ('ep_face_tiroir',
                      'Epaisseur des facades de tiroir (mm).'),
    'EpPorte': ('ep_porte', 'Epaisseur des portes (mm).'),
}


def sanitize_param_prefix(name):
    s = re.sub(r'[^A-Za-z0-9_]', '_', name or '')
    if not s or not s[0].isalpha():
        s = 'M_' + s
    return s


def ensure_meuble_parameters(design, param_prefix, values):
    resolved = {}
    try:
        user_params = design.userParameters
    except Exception:
        return resolved
    for short_key, (values_key, comment) in THICKNESS_PARAM_DEFS.items():
        full_name = '{}_{}'.format(param_prefix, short_key)
        value_mm = values.get(values_key, values.get('ep_panneau', 19))
        try:
            existing = user_params.itemByName(full_name)
            if existing:
                existing.expression = '{} mm'.format(value_mm)
                existing.comment = comment
            else:
                user_params.add(full_name,
                                 adsk.core.ValueInput.createByReal(mm_to_cm(value_mm)),
                                 'mm', comment)
            resolved[short_key] = full_name
        except Exception:
            continue
    return resolved


def build_meuble_body(comp, values, thickness_params=None, progress=None):
    """Construit dans le composant (vide) 'comp' tous les panneaux du
    caisson/étagères/tiroirs. Renvoie la liste des spécifications de portes
    (en cm, coordonnées locales au meuble) à créer séparément."""
    thickness_params = thickness_params or {}
    layout = compute_layout(values)
    panels = layout['panels']
    bodies_by_name = {}
    for i, p in enumerate(panels):
        if p[0] == 'XZ':
            _, x0, x1, z0, z1, y_start, y_extent, name, tkey = p
            param_name = thickness_params.get(tkey) if tkey else None
            body = add_panel_xz(comp, x0, x1, z0, z1, y_start, y_extent, name, thickness_param=param_name)
        else:
            x0, x1, y0, y1, z_start, z_extent, name, tkey = p
            param_name = thickness_params.get(tkey) if tkey else None
            body = add_panel_xy(comp, x0, x1, y0, y1, z_start, z_extent, name, thickness_param=param_name)
        bodies_by_name[name] = body
        _tick(progress, i + 1, name)
    # Rainures (fond de tiroir capte dans les cotes) : creusees APRES
    # que tous les panneaux existent (dont le cote lui-meme, deja en
    # place a ce stade), et restreintes explicitement au corps du
    # cote vise (voir cut_groove_x) pour ne jamais grignoter le fond,
    # coincident avec le fond de la rainure.
    for g in layout.get('grooves', []):
        axis, x_plane, sign, y0, y1, z0, z1, depth, name, target_name = g
        if axis == 'x':
            target_body = bodies_by_name.get(target_name)
            cut_groove_x(comp, x_plane, sign, y0, y1, z0, z1, depth, name, target_body=target_body)
    # Prises de main (portes/facades de tiroir) : creusees APRES que
    # tous les panneaux existent, restreintes chacune a son propre
    # panneau cible.
    for pm in layout.get('prises_main', []):
        pm_target_body = bodies_by_name.get(pm[-1])
        if pm[0] == 'x':
            _, x_plane, largeur, y_front, z_edge, sens, name, _target = pm
            cut_prise_doigt_x(
                comp, x_plane, largeur, y_front, z_edge, sens, name, target_body=pm_target_body)
        else:
            _, z_plane, hauteur, y_front, x_edge, sens, name, _target = pm
            cut_prise_doigt_z(
                comp, z_plane, hauteur, y_front, x_edge, sens, name, target_body=pm_target_body)
    # Coupes d'onglet 45deg (cotes/dessus/dessous) : effectuees une
    # fois tous les panneaux en place.
    for mc in layout.get('miter_cuts', []):
        tri_xz, y0, y1, mc_name, mc_target = mc
        mc_target_body = bodies_by_name.get(mc_target)
        if mc_target_body:
            cut_miter_wedge_xz(comp, mc_target_body, tri_xz, y0, y1, mc_name)
    # Perçages (Lamello + système 32) : effectués une fois tous les panneaux
    # en place, pour creuser dans la matière déjà présente (montants/
    # traverses). Regroupés par (axe, plan, sens, diamètre, profondeur)
    # identiques et percés en une seule fonctionnalité par groupe (au lieu
    # d'une par trou) : un meuble avec perçage système 32 peut compter
    # plusieurs centaines de trous, et Fusion recalcule tout l'historique à
    # chaque fonctionnalité ajoutée, donc ce regroupement accélère beaucoup
    # la génération/reconstruction.
    if progress is not None:
        try:
            progress.maximumValue = len(panels) + len(layout.get('holes', [])) + len(layout.get('doors', [])) + 1
        except Exception:
            pass
    groups = {}
    order = []
    for h in layout.get('holes', []):
        axis, plane, sign, u, v, diam, depth, name = h
        key = (axis, round(plane, 6), sign, round(diam, 6), round(depth, 6))
        if key not in groups:
            groups[key] = {'centers': [], 'name': name}
            order.append(key)
        groups[key]['centers'].append((u, v))
    for gi, key in enumerate(order):
        axis, plane, sign, diam, depth = key
        data = groups[key]
        centers = data['centers']
        group_name = (data['name'] if len(centers) == 1
                      else '{} (+{} autres)'.format(data['name'], len(centers) - 1))
        drill_holes_batch(comp, axis, plane, sign, diam, depth, centers, group_name)
        _tick(progress, len(panels) + gi + 1, group_name)
    return layout['doors']


def _transform_porte_ouverte(transform_ferme, x0_local, z0_local, largeur_cm,
                              hauteur_cm, ep_cm, sens, mode, offset_mm):
    # Cinematique d'ouverture visuelle (case 'Porte ouverte') : la porte
    # tourne d'abord de 110 degres autour de l'arete EXTERIEURE cote
    # charniere (a sa position fermee), PUIS la porte deja tournee est
    # translatee : epaisseur de la porte vers le fond du meuble, puis
    # offset_mm (12/21.5/30mm selon le cas, voir compute_layout) vers le
    # cote oppose aux charnieres. Tout est exprime dans le repere LOCAL
    # du meuble (voir build_door_component pour la correspondance entre
    # ce repere et celui, local, du composant Porte).
    x_charniere = x0_local if sens == 'gauche' else x0_local + largeur_cm
    # Face AVANT du panneau (celle SANS la charniere, cote visible) :
    # y=-ep_cm en applique (le panneau s'etend vers l'avant depuis
    # y=0, qui est lui la face arriere/charniere) ; y=0 en encastre
    # (le panneau s'etend vers l'interieur depuis cette face avant
    # jusqu'a y=ep_cm, la face arriere/charniere).
    y_avant = -ep_cm if mode != 'encastre' else 0.0

    axis_point = adsk.core.Point3D.create(x_charniere, y_avant, z0_local)
    axis_dir = adsk.core.Vector3D.create(0, 0, 1)
    # Sens choisi pour que la porte s'ouvre vers l'EXTERIEUR du meuble
    # (Y decroissant, oppose au fond), jamais vers l'interieur du
    # caisson.
    angle_rad = math.radians(ANGLE_OUVERTURE_PORTE_DEG)
    if sens == 'gauche':
        angle_rad = -angle_rad
    rot = adsk.core.Matrix3D.create()
    rot.setToRotation(angle_rad, axis_dir, axis_point)

    dy_fond_cm = ep_cm
    dx_oppose_cm = mm_to_cm(offset_mm) if sens == 'gauche' else -mm_to_cm(offset_mm)
    trans = adsk.core.Matrix3D.create()
    trans.translation = adsk.core.Vector3D.create(dx_oppose_cm, dy_fond_cm, 0)

    resultat = transform_ferme.copy()
    resultat.transformBy(rot)
    resultat.transformBy(trans)
    return resultat


def build_door_component(meuble_comp, door_spec, name, thickness_param=None):
    # Cree la porte comme occurrence IMBRIQUEE dans meuble_comp (pas au
    # niveau racine) : elle suit donc automatiquement tout deplacement du
    # meuble, et apparait sous lui dans l'arbre. Rompt volontairement la
    # compatibilite avec l'add-in Porte Inserta Blum (qui ne scanne que le
    # niveau racine), abandonnee sciemment au profit d'une arborescence
    # propre.
    (x0_local, z0_local, largeur_cm, hauteur_cm, ep_cm, sens, mode, hinges_mm,
     ouverte, offset_ouverture_mm, prise_main_code, montage_code) = door_spec
    # 'sens' ('gauche'/'droite', voir compute_layout) determine sur quel
    # bord (droit ou gauche du panneau) sont percees les charnieres,
    # meme convention que l'add-in Porte Inserta Blum (une porte
    # 'gauche' a ses charnieres sur son bord DROIT, X+). 'mode'
    # ('applique' ou 'encastre') determine le sens d'extrusion : voir
    # plus bas. 'hinges_mm' : positions Y (mm, depuis le bas de cette
    # porte) de chaque charniere, deja calculees et ancrees sur le
    # systeme 32 (voir hinge_positions_locales_mm dans meuble_layout).

    # Transform verifie empiriquement dans Fusion : avec ces 3 vecteurs,
    # l'axe X du composant reste l'axe X du meuble, l'epaisseur (extrusion
    # locale -Z) sort bien vers -Y (en facade, devant le caisson), mais
    # l'axe de hauteur local (v) se retrouve inverse par rapport au meuble
    # -> on prend donc le HAUT de la porte comme origine Z. Ce repere est
    # exprime directement dans le referentiel LOCAL du meuble (plus besoin
    # de composer avec un transform monde : l'occurrence est nichee dans
    # meuble_comp, Fusion applique deja le transform du meuble a ses
    # enfants automatiquement).
    origin = adsk.core.Point3D.create(x0_local, 0, z0_local + hauteur_cm)
    x_axis = adsk.core.Vector3D.create(1, 0, 0)
    y_axis = adsk.core.Vector3D.create(0, 0, -1)
    z_axis = adsk.core.Vector3D.create(0, 1, 0)
    transform = adsk.core.Matrix3D.create()
    transform.setWithCoordinateSystem(origin, x_axis, y_axis, z_axis)

    if ouverte:
        transform = _transform_porte_ouverte(
            transform, x0_local, z0_local, largeur_cm, hauteur_cm, ep_cm,
            sens, mode, offset_ouverture_mm)

    occ = meuble_comp.occurrences.addNewComponent(transform)
    comp = occ.component
    comp.name = name

    sketch = comp.sketches.add(comp.xYConstructionPlane)
    sketch.name = 'Porte'
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(0, 0, 0), adsk.core.Point3D.create(largeur_cm, hauteur_cm, 0))
    profile = _largest_profile(sketch)
    extrudes = comp.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    # 'applique' (defaut) : la porte est DEVANT le caisson, comme avant
    # (extrusion vers -Y depuis la face avant y=0). 'encastre' : la porte
    # reste affleurante a la face avant mais s'etend VERS L'INTERIEUR du
    # caisson (extrusion vers +Y), pour occuper de la profondeur utile a
    # l'avant (voir la reduction correspondante des etageres mobiles dans
    # compute_layout).
    signe = '' if mode == 'encastre' else '-'
    if thickness_param:
        ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByString(signe + thickness_param))
    else:
        ext_input.setDistanceExtent(
            False, adsk.core.ValueInput.createByReal(ep_cm if mode == 'encastre' else -ep_cm))
    extrude = extrudes.add(ext_input)
    body = extrude.bodies.item(0)
    body.name = comp.name
    apply_default_material(body)

    if hinges_mm and montage_code != 'off':
        _drill_hinges_inserta(comp, largeur_cm, hauteur_cm, ep_cm, sens, mode, hinges_mm, montage_code)

    # Prise de main : reperes LOCAUX (voir
    # cut_prise_doigt_porte_horizontal/vertical). 'sens' = 'gauche' ->
    # charnieres sur le bord DROIT -> oppose = bord GAUCHE (x_edge=0).
    # 'sens' = 'droite' -> charnieres a GAUCHE -> oppose = bord DROIT.
    # La face AVANT visible est a Z=-ep_cm en applique (la porte
    # s'extrude vers l'exterieur depuis Z=0, cote caisson) mais a
    # Z=0 en encastre (la porte s'extrude vers l'INTERIEUR depuis sa
    # propre face avant, affleurante). L'epaisseur passee a la
    # prise de main doit refleter cette face avant reelle.
    ep_avant_cm = 0.0 if mode == 'encastre' else ep_cm
    if prise_main_code == 'haut':
        cut_prise_doigt_porte_horizontal(
            comp, largeur_cm, 0.0, 1, ep_avant_cm, comp.name + ' Prise de main', target_body=body)
    elif prise_main_code == 'bas':
        cut_prise_doigt_porte_horizontal(
            comp, largeur_cm, hauteur_cm, -1, ep_avant_cm, comp.name + ' Prise de main', target_body=body)
    elif prise_main_code == 'oppose':
        # Charnieres percees cote X- pour 'gauche', X+ pour 'droite'
        # (voir convention VOLONTAIREMENT inversee dans
        # _drill_hinges_inserta) : l'oppose est donc l'autre cote.
        if sens == 'gauche':
            cut_prise_doigt_porte_vertical(
                comp, hauteur_cm, largeur_cm, -1, ep_avant_cm, comp.name + ' Prise de main', target_body=body)
        else:
            cut_prise_doigt_porte_vertical(
                comp, hauteur_cm, 0.0, 1, ep_avant_cm, comp.name + ' Prise de main', target_body=body)
    return occ


def _drill_hinges_inserta(comp, largeur_cm, hauteur_cm, ep_cm, sens, mode, hinges_mm,
                           montage_code='inserta'):
    # Godets de charniere (Ã35 x 13mm de profondeur par defaut) + trous
    # borgnes pour les chevilles Inserta (Ã8mm, entraxe 45mm), memes
    # cotes que l'add-in Porte Inserta Blum (build_door_geometry). Les
    # deux jeux de percages sont crees dans le meme repere local que le
    # panneau de la porte (0,0 = bas du bord gauche).
    dist_boitier_cm = mm_to_cm(CHARNIERE_DIST_BOITIER_MM)
    diam_charniere_cm = mm_to_cm(CHARNIERE_DIAM_MM)
    prof_charniere_cm = mm_to_cm(CHARNIERE_PROF_MM)
    # 'A visser' : les 2 trous secondaires (chevilles Inserta en
    # standard) deviennent de simples avant-trous de vis, plus
    # petits et peu profonds -- 'Inserta/a frapper' ne change rien.
    if montage_code == 'visser':
        diam_inserta_cm = mm_to_cm(5.0)
        prof_inserta_cm = mm_to_cm(1.0)
    else:
        diam_inserta_cm = mm_to_cm(CHARNIERE_DIAM_INSERTA_MM)
        prof_inserta_cm = prof_charniere_cm
    entreaxe_inserta_cm = mm_to_cm(CHARNIERE_ENTREAXE_INSERTA_MM)
    dist_inserta_cm = mm_to_cm(CHARNIERE_DIST_INSERTA_MM)

    edge_offset_cup_cm = dist_boitier_cm + diam_charniere_cm / 2.0
    edge_offset_inserta_cm = dist_boitier_cm + diam_charniere_cm / 2.0 + dist_inserta_cm
    # Garde-fou : une porte trop etroite (ex. feuille d'une paire '2
    # Portes' dans une colonne serree) ne peut pas recevoir cette
    # geometrie de charniere -- on l'ignore plutot que de faire
    # echouer toute la generation (aucun corps a couper si le cercle
    # sort du panneau).
    if largeur_cm < edge_offset_inserta_cm + diam_inserta_cm / 2.0 + mm_to_cm(2):
        return
    # Convention VOLONTAIREMENT inversee par rapport a Porte Inserta Blum
    # d'origine : le choix 'Gauche' du dialogue percait cote X+ (bord
    # droit du panneau), ce qui ne correspondait pas au sens attendu par
    # l'utilisateur -- desormais 'Gauche' perce cote X- (bord gauche).
    if sens == 'gauche':
        cup_offset_cm = edge_offset_cup_cm
        inserta_offset_cm = edge_offset_inserta_cm
    else:
        cup_offset_cm = largeur_cm - edge_offset_cup_cm
        inserta_offset_cm = largeur_cm - edge_offset_inserta_cm
    cup_radius_cm = diam_charniere_cm / 2.0
    inserta_radius_cm = diam_inserta_cm / 2.0

    cup_sketch = comp.sketches.add(comp.xYConstructionPlane)
    cup_sketch.name = 'Perçage charnières'
    for y_mm in hinges_mm:
        # L'axe Y local du composant est INVERSE par rapport au monde (voir
        # commentaire dans build_door_component : Y local = 0 correspond au
        # HAUT de la porte) : hinges_mm est mesure depuis le BAS, donc
        # inverser ici pour retrouver la bonne coordonnee locale.
        y_cm = hauteur_cm - mm_to_cm(y_mm)
        cup_sketch.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(cup_offset_cm, y_cm, 0), cup_radius_cm)
    cup_profiles = adsk.core.ObjectCollection.create()
    for i in range(cup_sketch.profiles.count):
        cup_profiles.add(cup_sketch.profiles.item(i))
    extrudes = comp.features.extrudeFeatures
    # La charniere se percE TOUJOURS depuis la face ARRIERE du panneau
    # (celle qui fait face a l'interieur du caisson), jamais depuis la
    # face visible. En applique, cette face arriere est a z=0 (le corps
    # s'etend vers z negatif, en facade). En encastre, le corps s'etend
    # au contraire vers z positif (voir build_door_component) : la face
    # arriere est donc a z=ep_cm, pas z=0 -- sans ce decalage de depart,
    # la coupe visait la face avant (visible), constate en pratique.
    start_offset_cm = ep_cm if mode == 'encastre' else 0.0
    cup_input = extrudes.createInput(cup_profiles, adsk.fusion.FeatureOperations.CutFeatureOperation)
    cup_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(start_offset_cm))
    cup_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(-prof_charniere_cm))
    extrudes.add(cup_input)

    inserta_sketch = comp.sketches.add(comp.xYConstructionPlane)
    inserta_sketch.name = 'Perçage Inserta'
    for y_mm in hinges_mm:
        y_cm = hauteur_cm - mm_to_cm(y_mm)
        inserta_sketch.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(inserta_offset_cm, y_cm - entreaxe_inserta_cm / 2.0, 0),
            inserta_radius_cm)
        inserta_sketch.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(inserta_offset_cm, y_cm + entreaxe_inserta_cm / 2.0, 0),
            inserta_radius_cm)
    inserta_profiles = adsk.core.ObjectCollection.create()
    for i in range(inserta_sketch.profiles.count):
        inserta_profiles.add(inserta_sketch.profiles.item(i))
    inserta_input = extrudes.createInput(
        inserta_profiles, adsk.fusion.FeatureOperations.CutFeatureOperation)
    inserta_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(start_offset_cm))
    inserta_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(-prof_inserta_cm))
    extrudes.add(inserta_input)
