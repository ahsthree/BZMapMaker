#!/usr/bin/env python3
"""BZ Map Maker - a BZFlag .bzw editor with a 2D layout view and a live 3D preview.

Run:  pip install PyQt6   then   python bzmapmaker.py [optional_map.bzw]
Works on Windows, macOS and Linux. The 3D preview is drawn in software (QPainter), so no
OpenGL is required. Textures are applied to box faces with Qt's 2D projective quadToQuad
transform, which is an exact planar homography, not an OpenGL approximation.
"""
import sys, os, math, json, hashlib
from dataclasses import dataclass, field, asdict, replace
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QPointF, QRectF, QSettings
from PyQt6.QtGui import (QPainter, QColor, QPen, QBrush, QPolygonF, QPainterPath, QAction,
                          QActionGroup, QKeySequence, QPixmap, QTransform)
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QSplitter, QDockWidget, QFormLayout,
                              QDoubleSpinBox, QSpinBox, QComboBox, QFileDialog, QMessageBox, QDialog,
                              QListWidget, QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout, QLabel,
                              QDialogButtonBox, QInputDialog)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
DEF = {'box': (10, 10, 9.4), 'pyramid': (8.2, 8.2, 8.2), 'base': (20, 20, 0),
       'teleporter': (.5, 4, 10), 'arc': (10, 10, 10), 'cone': (10, 10, 10), 'group': (1, 1, 1), 'mesh': (0, 0, 0)}
TEAM = ['', '#c8453b', '#3c9a5a', '#3a6fc4', '#8a55b5']
TEAM_NAMES = ['', 'Red', 'Green', 'Blue', 'Purple']
COL = {'box': '#8d939a', 'pyramid': '#b08a4e', 'teleporter': '#d0702a', 'arc': '#5c8a7a',
       'cone': '#7a6a9a', 'group': '#a0a4ad', 'mesh': '#9aa0a8'}
TSWAP = {1: 2, 2: 1, 3: 4, 4: 3}  # fixed pairing used by 2-fold symmetry: red<->green, blue<->purple
# index -> (mode, fold). mode 'rot' = N-way rotation about the world center; the two mirror
# modes are always a simple left-right or top-bottom reflection (fold is always 2 for those).
SYM_DEFS = [('', 0), ('rot', 2), ('rot', 3), ('rot', 4), ('mirror_lr', 2), ('mirror_tb', 2)]
SYM_LABELS = ['Symmetry: off', 'Symmetry: 2-team rotation (180\u00b0)', 'Symmetry: 3-team rotation (120\u00b0)',
              'Symmetry: 4-team rotation (90\u00b0)', 'Symmetry: mirror left-right', 'Symmetry: mirror top-bottom']
# (bzw keyword, label) per object type. Arc keywords come from wiki.bzflag.org/Arc.
FACES = {'box': [('top', 'Top'), ('sides', 'Sides'), ('bottom', 'Bottom')],
         'arc': [('top', 'Top'), ('bottom', 'Bottom'), ('inside', 'Inside'), ('outside', 'Outside'),
                 ('startside', 'Start side'), ('endside', 'End side')]}
ACC = QColor('#0f6b6b')


@dataclass
class Obj:
    t: str
    x: float = 0; y: float = 0; z: float = 0
    sx: float = 1; sy: float = 1; sz: float = 1
    r: float = 0; team: int = 1; name: str = ''; link: str = ''; blink: str = ''
    matref: str = ''
    mats: dict = field(default_factory=dict)  # per-face materials (box, arc): face key -> material name
    mesh: str = ''  # key into MESHDATA: the mesh block's original text, kept verbatim
    divisions: int = 16; angle: float = 360.0; ratio: float = 0.0  # arc / cone only
    fam: str = ''; fam_k: int = 0; fold: int = 0; mode: str = ''   # symmetry family
    uid: int = 0

    def __post_init__(s):
        s.mats = dict(s.mats)  # never share the dict between clones


@dataclass
class Material:
    name: str
    texture: str = ''
    color: tuple = (1.0, 1.0, 1.0, 1.0)
    extra: list = field(default_factory=list)  # every other line (addtexture, texmat, ambient...), kept as-is
    raw: list = field(default_factory=list)    # the block's original lines; written back unchanged until edited


def num(v):
    return ('%.3f' % v).rstrip('0').rstrip('.') or '0'


# ---- symmetry family transforms -------------------------------------------------
def _xform(k, x, y, r, mode, fold):
    """Given the canonical (family index 0) x, y, r, return sibling k's x, y, r."""
    if mode == 'rot' and k:
        a = math.radians(k * 360.0 / fold)
        c, s = math.cos(a), math.sin(a)
        return x * c - y * s, x * s + y * c, (r + k * 360.0 / fold) % 360
    if mode == 'mirror_lr' and k == 1:
        return -x, y, (180 - r) % 360
    if mode == 'mirror_tb' and k == 1:
        return x, -y, (-r) % 360
    return x, y, r


def _inv(k, x, y, r, mode, fold):
    """Inverse of _xform: recover the canonical x, y, r from sibling k's values."""
    if mode == 'rot' and k:
        return _xform((-k) % fold, x, y, r, mode, fold)
    if mode in ('mirror_lr', 'mirror_tb') and k == 1:
        return _xform(1, x, y, r, mode, fold)  # these reflections are their own inverse
    return x, y, r


def _team_fwd(k, team, mode, fold):
    if not team: return team
    if mode == 'rot' and fold >= 3: return ((team - 1 + k) % 4) + 1
    if k == 1: return TSWAP[team]
    return team


def _team_inv(k, team, mode, fold):
    if not team: return team
    if mode == 'rot' and fold >= 3: return ((team - 1 - k) % 4) + 1
    if k == 1: return TSWAP[team]
    return team


# ---- .bzw reading -----------------------------------------------------------------
MESHDATA = {}  # key -> the mesh block's original lines (normals, texcoords, phydrv, drawinfo... untouched)
_MESHGEO = {}


def mesh_register(body):
    key = hashlib.sha1('\n'.join(body).encode('utf-8', 'replace')).hexdigest()[:16]
    MESHDATA[key] = list(body)
    return key


def _spin(v, ang, ax):
    n = math.sqrt(sum(a * a for a in ax))
    if not n: return v
    ux, uy, uz = (a / n for a in ax); t = math.radians(ang); c, sn = math.cos(t), math.sin(t)
    x, y, z = v; d = ux * x + uy * y + uz * z
    return [x * c + (uy * z - uz * y) * sn + ux * d * (1 - c),
            y * c + (uz * x - ux * z) * sn + uy * d * (1 - c),
            z * c + (ux * y - uy * x) * sn + uz * d * (1 - c)]


def mesh_geom(key):
    """(vertices after the mesh's own shift/scale/spin, [(vertex indices, material name)]).
    Only used for drawing; the saved file always uses the original text. Shear is ignored."""
    g = _MESHGEO.get(key)
    if g is not None: return g
    verts, faces, xf, cur, face, depth = [], [], [], '', None, 0
    for ln in MESHDATA.get(key, []):
        p = ln.split(); k = p[0].lower()
        if depth:  # drawinfo holds its own vertex lists, so skip it
            if k in ('lod', 'radarlod'): depth += 1
            elif k == 'end': depth -= 1
            continue
        try:
            if face is not None:
                if k == 'vertices': face[0] = [int(a) for a in p[1:]]
                elif k == 'matref' and len(p) > 1: face[1] = p[1]
                elif k == 'endface': faces.append((face[0], face[1])); face = None
            elif k == 'vertex':
                v = [float(a) for a in p[1:4]]
                if len(v) == 3: verts.append(v)
            elif k in ('shift', 'scale', 'spin'): xf.append((k, [float(a) for a in p[1:]]))
            elif k == 'matref' and len(p) > 1: cur = p[1]
            elif k == 'face': face = [[], cur]
            elif k == 'drawinfo': depth = 1
        except ValueError:
            pass
    for k, a in xf:
        if k == 'shift' and len(a) >= 3: verts = [[v[0] + a[0], v[1] + a[1], v[2] + a[2]] for v in verts]
        elif k == 'scale' and len(a) >= 3: verts = [[v[0] * a[0], v[1] * a[1], v[2] * a[2]] for v in verts]
        elif k == 'spin' and len(a) >= 4: verts = [_spin(v, a[0], a[1:4]) for v in verts]
    g = _MESHGEO[key] = ([tuple(v) for v in verts], faces)
    return g


def mesh_world(o):
    """World-space vertices of a mesh object: (v + pre-shift) turned by r about Z, then shifted."""
    verts, faces = mesh_geom(o.mesh)
    a = math.radians(o.r); c, sn = math.cos(a), math.sin(a)
    out = []
    for x, y, z in verts:
        x += o.sx; y += o.sy; z += o.sz
        out.append((x * c - y * sn + o.x, x * sn + y * c + o.y, z + o.z))
    return out, faces


def parse(text):
    """Read world, box, pyramid, base, teleporter, arc, cone, mesh, material, define/group
    and link blocks. Meshes and materials keep their original text; any other block
    (physics, zones, dynamic colors...) is returned verbatim in `extras` so it isn't lost."""
    lines = [ln.split('#', 1)[0].rstrip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    i = 0
    nw, objs, materials, defines, links, extras = None, [], {}, {}, [], []
    TOP = {'world', 'material', 'define', 'enddef', 'group', 'box', 'pyramid', 'base', 'teleporter', 'arc',
           'cone', 'mesh', 'meshbox', 'meshpyr', 'tetra', 'sphere', 'link', 'physics', 'zone', 'weapon',
           'options', 'dynamiccolor', 'texturematrix', 'waterlevel', 'wall', 'transform'}

    def nm(a, d):
        out = []
        for idx, val in enumerate(d):
            try: out.append(float(a[idx]))
            except Exception: out.append(val)
        return out

    def read_block():
        nonlocal i
        v = {}
        while i < len(lines):
            p = lines[i].split(); i += 1
            if p[0].lower() == 'end': break
            v[p[0].lower()] = p[1:]
        return v

    def read_raw_block():
        # A block closes at an 'end' followed by another top-level keyword (or end of file),
        # so nested 'end's, such as inside drawinfo, stay part of the block.
        nonlocal i
        body = []
        while i < len(lines):
            line = lines[i].strip(); i += 1
            if line.split()[0].lower() == 'end':
                nxt = lines[i].split()[0].lower() if i < len(lines) else ''
                if not nxt or nxt in TOP: return body
            body.append(line)
        return body

    while i < len(lines):
        p = lines[i].split(); i += 1
        k = p[0].lower()
        if k == 'world':
            v = read_block()
            try: nw = float(v['size'][0])
            except Exception: pass
        elif k == 'material':
            body = read_raw_block()
            name, tex, color, extra = '', '', (1.0, 1.0, 1.0, 1.0), []
            for ln in body:
                q = ln.split(); kk = q[0].lower()
                if kk == 'name' and len(q) > 1: name = q[1]
                elif kk == 'texture' and len(q) > 1: tex = q[1]
                elif kk in ('diffuse', 'color'):  # 'color' is a synonym for 'diffuse'
                    try: color = tuple(float(c) for c in (q[1:] + ['1', '1', '1', '1'])[:4])
                    except ValueError: pass
                else: extra.append(ln)
            name = name or (p[1] if len(p) > 1 else 'mat%d' % (len(materials) + 1))
            materials[name] = Material(name, tex, color, extra, list(body))
        elif k == 'mesh':
            body = read_raw_block()
            name = next((b.split()[1] for b in body if b.lower().startswith('name ') and len(b.split()) > 1), '')
            o = Obj('mesh', 0, 0, 0, 0, 0, 0, 0, 1, name); o.mesh = mesh_register(body); objs.append(o)
        elif k == 'link':
            v = read_block(); links.append(((v.get('from') or [''])[0], (v.get('to') or [''])[0]))
        elif k == 'define':
            name = p[1] if len(p) > 1 else 'group%d' % (len(defines) + 1)
            sub = []
            while i < len(lines):
                if lines[i].split()[0].lower() == 'enddef': i += 1; break
                sub.append(lines[i]); i += 1
            _, kids, _, _, _ = parse('\n'.join(sub))
            defines[name] = kids
        elif k == 'group':
            v = read_block()
            name = p[1] if len(p) > 1 else ''
            x, y, z = nm(v.get('shift'), (0, 0, 0))
            r = nm(v.get('rotation'), (0,))[0]
            team = int(nm(v.get('team'), (0,))[0])
            objs.append(Obj('group', x, y, z, 1, 1, 1, r, team, name))
        elif k in ('box', 'pyramid', 'base', 'teleporter', 'arc', 'cone'):
            v = read_block()
            # Old maps often name a teleporter right on its opening line ('teleporter home')
            # instead of a 'name' line inside the block; accept either.
            name = (v.get('name') or [p[1] if len(p) > 1 and k == 'teleporter' else ''])[0]
            x, y, z = nm(v.get('position', v.get('pos')), (0, 0, 0))
            sx, sy, sz = nm(v.get('size'), DEF[k])
            r = nm(v.get('rotation', v.get('rot')), (0,))[0]
            team = int(nm(v.get('color'), (1,))[0])
            o = Obj(k, x, y, z, sx, sy, sz, r, team if 1 <= team <= 4 else 1, name)
            o.matref = (v.get('matref') or [''])[0]
            for fk, _ in FACES.get(k, []):
                vv = v.get(fk)
                if vv and len(vv) >= 2 and vv[0].lower() == 'matref': o.mats[fk] = vv[1]
            if k == 'arc':
                o.divisions = int(nm(v.get('divisions'), (16,))[0])
                o.angle = nm(v.get('angle'), (360,))[0]
                o.ratio = nm(v.get('ratio'), (0,))[0]
            if k == 'cone':
                o.divisions = int(nm(v.get('divisions'), (16,))[0])
            objs.append(o)
        elif k in ('end', 'enddef'):
            continue  # stray terminator
        else:  # a block we don't edit: keep it word for word
            opener = lines[i - 1].strip(); body = read_raw_block()
            extras.append('\n'.join([opener] + ['  ' + b for b in body] + ['end']))
    # Keep every explicit, unique name exactly as given -- only blank or duplicate names
    # get replaced, so re-opening a map you already built here won't rename anything.
    seen = set()
    for o in objs:
        if o.t == 'teleporter':
            if not o.name or o.name in seen: o.name = ''
            else: seen.add(o.name)
    j = 1
    for o in objs:
        if o.t == 'teleporter' and not o.name:
            while 't%d' % j in seen: j += 1
            o.name = 't%d' % j; seen.add(o.name)

    # Older maps often link teleporters by plain 0-based index instead of name, and/or
    # write 'from'/'to' with no ':f'/':b' face suffix at all. Handle both.
    tele_order = [o for o in objs if o.t == 'teleporter']

    def resolve(ident, default_face):
        name, sep, face = ident.rpartition(':')
        if not sep: name, face = ident, default_face
        face = face.lower() or default_face
        if name.isdigit() and 0 <= int(name) < len(tele_order): name = tele_order[int(name)].name
        return name, face

    for f, t in links:
        fn, fface = resolve(f, 'f'); tn, _ = resolve(t, 'b')
        for o in objs:
            if o.t == 'teleporter' and o.name == fn:
                if fface == 'f': o.link = tn
                elif fface == 'b': o.blink = tn
    return nw, objs, materials, defines, extras


def obj_block(o):
    if o.t == 'mesh':
        ln = ['mesh'] + ['  ' + b for b in MESHDATA.get(o.mesh, [])]
        if o.x or o.y or o.z: ln.append('  shift %s %s %s' % (num(o.x), num(o.y), num(o.z)))
        return ln + ['end']
    if o.t == 'group':
        ln = ['group %s' % o.name, '  shift %s %s %s' % (num(o.x), num(o.y), num(o.z)),
              '  rotation %s' % num(o.r)]
        if o.team: ln.append('  team %d' % o.team)
        return ln + ['end']
    ln = [o.t]
    if o.t == 'teleporter' and o.name: ln.append('  name %s' % o.name)
    ln += ['  position %s %s %s' % (num(o.x), num(o.y), num(o.z)),
           '  size %s %s %s' % (num(o.sx), num(o.sy), num(o.sz)), '  rotation %s' % num(o.r)]
    if o.t == 'base': ln.append('  color %d' % o.team)
    if o.t == 'arc':
        ln.append('  divisions %d' % o.divisions)
        if o.angle != 360: ln.append('  angle %s' % num(o.angle))
        if o.ratio: ln.append('  ratio %s' % num(o.ratio))
    if o.t == 'cone': ln.append('  divisions %d' % o.divisions)
    if o.matref: ln.append('  matref %s' % o.matref)
    if o.t in FACES and o.mats:
        # BZFlag ignores partial per-face lists on arcs, so unset arc faces fall back to the
        # all-faces material (or the first face material chosen).
        fb = o.matref or next((o.mats[k] for k, _ in FACES[o.t] if o.mats.get(k)), '')
        for fk, _ in FACES[o.t]:
            name = o.mats.get(fk) or (fb if o.t == 'arc' else '')
            if name: ln.append('  %s matref %s' % (fk, name))
    return ln + ['end']


def material_block(m):
    if m.raw: return ['material'] + ['  ' + b for b in m.raw] + ['end']  # untouched since import
    ln = ['material', '  name %s' % m.name]
    if m.texture: ln.append('  texture %s' % m.texture)
    ln.append('  color %s %s %s %s' % tuple(num(c) for c in m.color))
    return ln + ['  ' + e for e in m.extra] + ['end']


class Model(QObject):
    changed = pyqtSignal(); selected = pyqtSignal(); reset = pyqtSignal(); warn = pyqtSignal(str)

    def __init__(s):
        super().__init__()
        s.W, s.objs, s.sel, s.msel, s.undo, s.path = 400, [], None, [], [], None
        s.materials, s.defines, s.texcache, s.extras = {}, {}, {}, []
        s.nid, s.sym = 1, 0

    def push(s):
        d = {'W': s.W, 'objs': [asdict(o) for o in s.objs],
             'materials': {k: asdict(v) for k, v in s.materials.items()},
             'defines': {k: [asdict(o) for o in v] for k, v in s.defines.items()}, 'extras': s.extras}
        s.undo.append(json.dumps(d)); del s.undo[:-80]

    def pop(s):
        if not s.undo: return
        d = json.loads(s.undo.pop())
        s.W, s.objs, s.sel, s.msel = d['W'], [Obj(**o) for o in d['objs']], None, []
        s.materials = {k: Material(**v) for k, v in d['materials'].items()}
        s.defines = {k: [Obj(**o) for o in v] for k, v in d['defines'].items()}
        s.extras = d.get('extras', [])
        s.nid = max([s.nid] + [o.uid + 1 for o in s.objs])
        s.selected.emit(); s.changed.emit()

    def add(s, t, x, y):
        o = Obj(t, x, y, 0, *DEF[t]); o.uid = s.nid; s.nid += 1
        if t == 'teleporter':
            i = 1
            while any(q.name == 't%d' % i for q in s.objs): i += 1
            o.name = 't%d' % i
        s.objs.append(o); return o

    def select(s, o):
        s.sel = o; s.selected.emit()

    def toggle_msel(s, o):
        if o in s.msel: s.msel.remove(o)
        else: s.msel.append(o)
        s.select(o)

    def group_bounds(s, name):
        kids = s.defines.get(name, [])
        if not kids: return 10, 10
        xs = [abs(k.x) + k.sx for k in kids]; ys = [abs(k.y) + k.sy for k in kids]
        return max(xs + [5]), max(ys + [5])

    # ---- symmetry families ---------------------------------------------------
    def make_family(s, o):
        if not s.sym or o.fam or o.t == 'mesh': return
        mode, fold = SYM_DEFS[s.sym]
        s.nid += 0
        fam = 'f%d' % o.uid
        o.fam, o.fam_k, o.fold, o.mode = fam, 0, fold, mode
        created = [o]
        for k in range(1, fold):
            q = replace(o); q.uid = s.nid; s.nid += 1; q.fam_k = k; q.link = q.blink = ''
            if q.t == 'teleporter':
                j = 1
                while any(z.name == 't%d' % j for z in s.objs + created): j += 1
                q.name = 't%d' % j
            s.objs.append(q); created.append(q)
        s._sync(o, created)
        keep, seenxy = [o], {(round(o.x, 2), round(o.y, 2))}
        for q in created[1:]:
            key = (round(q.x, 2), round(q.y, 2))
            if key in seenxy: s.objs.remove(q)
            else: seenxy.add(key); keep.append(q)
        if len(keep) < 2:
            o.fam = o.mode = ''; o.fold = 0
        else:
            if o.t == 'teleporter': s._relink(keep)
            s._bounds_check(keep)

    def sync_family(s, o):
        if not o.fam: return
        fam = [q for q in s.objs if q.fam == o.fam]
        s._sync(o, fam)
        if o.t == 'teleporter': s._relink(sorted(fam, key=lambda q: q.fam_k))
        s._bounds_check(fam)

    def _bounds_check(s, fam):
        if any(abs(q.x) > s.W or abs(q.y) > s.W for q in fam):
            s.warn.emit('A symmetry sibling landed outside the world bounds. Only 90\u00b0 and '
                        '180\u00b0 symmetry are guaranteed to stay inside a square world; for 120\u00b0 '
                        '(3-team) symmetry, keep new objects within about the world half-width of the center.')

    def _sync(s, o, fam):
        mode, fold = o.mode, o.fold
        x0, y0, r0 = _inv(o.fam_k, o.x, o.y, o.r, mode, fold)
        team0 = _team_inv(o.fam_k, o.team, mode, fold)
        if mode == 'rot' and fold == 3:
            # Every sibling sits at the same distance from 0,0. A 120-degree turn can push a point
            # outside a square world unless that distance is at most the half-width (minus the
            # object's own reach), so limit the shared distance.
            limit = max(0.0, s.W - math.hypot(o.sx, o.sy)); rad = math.hypot(x0, y0)
            if rad > limit and rad > 0:
                x0, y0 = x0 * limit / rad, y0 * limit / rad
                o.x, o.y, _ = _xform(o.fam_k, x0, y0, r0, mode, fold)
                s.warn.emit('3-team symmetry: pulled toward the center so all three copies stay inside the world.')
        for q in fam:
            if q is o: continue
            q.x, q.y, q.r = _xform(q.fam_k, x0, y0, r0, mode, fold)
            for f in ('z', 'sx', 'sy', 'sz', 'angle', 'ratio', 'divisions', 'matref'):
                setattr(q, f, getattr(o, f))
            q.mats = dict(o.mats)
            q.team = _team_fwd(q.fam_k, team0, mode, fold)
        if o.t == 'teleporter' and o.mode in ('mirror_lr', 'mirror_tb'):
            for q in fam:
                if q is not o: q.r = (o.r + 180) % 360  # forces front/back to line up on a mirror

    def _relink(s, ordered):
        n = len(ordered)
        for i, m in enumerate(ordered):
            m.link = ordered[(i + 1) % n].name; m.blink = ordered[(i - 1) % n].name

    def family_of(s, o):
        return [q for q in s.objs if o.fam and q.fam == o.fam]

    def delete(s):
        if not s.sel: return
        s.push()
        dead = s.family_of(s.sel) or [s.sel]
        names = {d.name for d in dead if d.name}
        s.objs = [o for o in s.objs if o not in dead]
        for o in s.objs:
            if o.link in names: o.link = ''
            if o.blink in names: o.blink = ''
        s.sel = None; s.msel = []; s.selected.emit(); s.changed.emit()

    def duplicate(s):
        """Clone only the selected object (not its symmetry siblings) and, if a symmetry
        mode is on, build it a fresh family -- rotational symmetry is always centered on
        the world origin, so a translated copy of a whole family would no longer line up."""
        if not s.sel: return
        s.push()
        c = replace(s.sel); c.uid = s.nid; s.nid += 1; c.x += 10; c.y -= 10
        c.fam = c.fam_k = c.fold = 0; c.mode = ''; c.link = c.blink = ''
        if c.t == 'teleporter':
            j = 1
            while any(z.name == 't%d' % j for z in s.objs): j += 1
            c.name = 't%d' % j
        s.objs.append(c); s.make_family(c); s.select(c); s.changed.emit()

    def group_selection(s):
        """Turn the multi-selected objects into a named prefab (a 'define') plus one
        instance ('group') placed at their centroid -- BZFlag's group only stores a shift
        and rotation, so the prefab's own contents are stored relative to that midpoint."""
        objs = [o for o in s.msel if o.t != 'group'] or ([s.sel] if s.sel and s.sel.t != 'group' else [])
        if len(objs) < 2: return None
        s.push()
        cx = sum(o.x for o in objs) / len(objs); cy = sum(o.y for o in objs) / len(objs)
        kids = []
        for o in objs:
            k = replace(o); k.x -= cx; k.y -= cy; k.fam = k.fam_k = k.fold = 0; k.mode = ''
            kids.append(k)
        name = 'group%d' % (len(s.defines) + 1)
        s.defines[name] = kids
        s.objs = [o for o in s.objs if o not in objs]
        g = Obj('group', cx, cy, 0, 1, 1, 1, 0, 0, name); g.uid = s.nid; s.nid += 1
        s.objs.append(g); s.msel = []; s.select(g); s.changed.emit()
        return name

    def load(s, text, folder=None):
        nw, objs, materials, defines, extras = parse(text)
        s.push(); s.W = nw or s.W; s.objs = objs; s.materials = materials; s.defines = defines
        s.extras = extras
        s.sel = None; s.msel = []
        for o in s.objs: o.uid = s.nid; s.nid += 1
        s.texcache = {}
        s.reset.emit(); s.selected.emit(); s.changed.emit()

    def text(s):
        out = ['# Made with BZ Map Maker', 'world', '  size %s' % num(s.W), 'end', '']
        for c in s.extras: out += [c, '']  # physics, dynamic colors... they may be referenced below
        for m in s.materials.values(): out += material_block(m) + ['']
        for name, kids in s.defines.items():
            out.append('define %s' % name)
            for kd in kids: out += ['  ' + ln for ln in obj_block(kd)]
            out += ['enddef', '']
        for o in s.objs: out += obj_block(o) + ['']
        for o in s.objs:
            if o.t == 'teleporter':
                for face, tgt, other in (('f', o.link, 'b'), ('b', o.blink, 'f')):
                    if tgt: out += ['link', '  from %s:%s' % (o.name, face), '  to %s:%s' % (tgt, other), 'end', '']
        return '\n'.join(out)


def expand(model):
    """Yield (leaf, source) pairs: for a plain object, leaf is o itself; for a group
    instance, one leaf per child in its prefab, transformed into world space. Groups
    cannot contain other groups."""
    out = []
    for o in model.objs:
        if o.t == 'group':
            a = math.radians(o.r); c, sn = math.cos(a), math.sin(a)
            for kd in model.defines.get(o.name, []):
                leaf = replace(kd)
                leaf.x, leaf.y = o.x + kd.x * c - kd.y * sn, o.y + kd.x * sn + kd.y * c
                leaf.z, leaf.r = o.z + kd.z, (kd.r + o.r) % 360
                if leaf.t == 'base' and o.team: leaf.team = o.team
                if leaf.t == 'mesh':  # kept as: (mesh + its own shift), turned by the group, then shifted
                    leaf.sx, leaf.sy, leaf.sz = kd.x, kd.y, kd.z
                    leaf.x, leaf.y, leaf.z = o.x, o.y, o.z
                out.append((leaf, o))
        else:
            out.append((o, o))
    return out


# ---------------------------------------------------------------------------
# 2D layout view
# ---------------------------------------------------------------------------
def cpen(color, w=1, style=Qt.PenStyle.SolidLine):
    p = QPen(QColor(color), w, style); p.setCosmetic(True); return p


HINT = {'select': 'Click to select and drag to move. Ctrl+click to multi-select for grouping. '
                   'Drag empty space or use the right mouse button to pan; scroll to zoom.',
        'box': 'Drag corner to corner, or click for a default box.',
        'pyramid': 'Drag corner to corner, or click for a default pyramid.',
        'base': 'Click to place a base, then set its team in the panel.',
        'teleporter': 'Click to place a teleporter, then set its links in the panel.',
        'arc': 'Drag corner to corner, or click for a default arc. Set sweep angle and hollow ratio in the panel.',
        'cone': 'Drag corner to corner, or click for a default cone.'}


class Editor(QWidget):
    def __init__(s, m):
        super().__init__()
        s.m, s.tool, s.snap, s.v, s.drag, s.space, s.fitted = m, 'select', True, [0, 0, 1.0], None, False, False
        s.setFocusPolicy(Qt.FocusPolicy.StrongFocus); s.setMinimumSize(320, 240)
        m.changed.connect(s.update); m.selected.connect(s.update); m.reset.connect(s.fit)

    def fit(s):
        s.v = [0, 0, min(s.width(), s.height()) / (s.m.W * 2.2)]; s.update()

    def showEvent(s, e):
        if not s.fitted: s.fitted = True; s.fit()

    def sn(s, v): return round(v / 5) * 5 if s.snap else v

    def tow(s, p):
        return (p.x() - s.width() / 2 - s.v[0]) / s.v[2], -(p.y() - s.height() / 2 - s.v[1]) / s.v[2]

    def hit(s, wx, wy):
        pad = 4 / s.v[2]
        for leaf, src in reversed(expand(s.m)):
            if leaf.t == 'mesh':
                W, _ = mesh_world(leaf)
                if W and min(v[0] for v in W) - pad <= wx <= max(v[0] for v in W) + pad \
                        and min(v[1] for v in W) - pad <= wy <= max(v[1] for v in W) + pad: return src
                continue
            hx, hy = (max(leaf.sx, leaf.sy, 5) if leaf.t == 'group' else leaf.sx), leaf.sy
            if leaf.t == 'group': hx, hy = s.m.group_bounds(leaf.name)
            a = math.radians(leaf.r); dx, dy = wx - leaf.x, wy - leaf.y
            lx = dx * math.cos(a) + dy * math.sin(a); ly = -dx * math.sin(a) + dy * math.cos(a)
            if abs(lx) <= max(hx, 1) + pad and abs(ly) <= hy + pad: return src

    def draw_leaf(s, p, leaf, sel):
        S = s.v[2]
        if leaf.t == 'mesh':  # meshes are drawn straight in world coordinates
            W, faces = mesh_world(leaf)
            c = QColor(COL['mesh']); c.setAlpha(70); p.setPen(cpen('#4a5058')); p.setBrush(c)
            for idx, _m in faces:
                pts = [QPointF(W[i][0], W[i][1]) for i in idx if 0 <= i < len(W)]
                if len(pts) >= 3: p.drawPolygon(QPolygonF(pts))
            if sel and W:
                pad = 3 / S; x0, x1 = min(v[0] for v in W), max(v[0] for v in W)
                y0, y1 = min(v[1] for v in W), max(v[1] for v in W)
                p.setPen(cpen(ACC, 2.5, Qt.PenStyle.DashLine)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad))
            return
        p.save(); p.translate(leaf.x, leaf.y); p.rotate(leaf.r)  # the view is y-up, so +rotation is counter-clockwise, same as the 3D view
        if leaf.t == 'group':
            hx, hy = s.m.group_bounds(leaf.name)
            p.setPen(cpen('#98a0a8', 1, Qt.PenStyle.DashLine)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(-hx, -hy, 2 * hx, 2 * hy))
        elif leaf.t in ('arc', 'cone'):
            path = QPainterPath()
            if leaf.t == 'cone' or leaf.angle >= 360:
                path.addEllipse(QRectF(-leaf.sx, -leaf.sy, 2 * leaf.sx, 2 * leaf.sy))
            else:
                path.moveTo(0, 0); path.arcTo(QRectF(-leaf.sx, -leaf.sy, 2 * leaf.sx, 2 * leaf.sy), 0, -leaf.angle)
                path.closeSubpath()
            c = QColor(COL[leaf.t]); p.setPen(cpen('#22262b')); p.setBrush(c); p.drawPath(path)
            if leaf.t == 'arc' and leaf.ratio:
                ir = 1 - leaf.ratio
                p.setBrush(QColor('#f6f5f0')); p.drawEllipse(QRectF(-leaf.sx * ir, -leaf.sy * ir, 2 * leaf.sx * ir, 2 * leaf.sy * ir))
            if leaf.t == 'cone':
                p.setPen(cpen('#22262b')); p.drawEllipse(QRectF(-leaf.sx * .12, -leaf.sy * .12, leaf.sx * .24, leaf.sy * .24))
        else:
            a, b = max(leaf.sx, 1.2 / S if leaf.t == 'teleporter' else 0), leaf.sy
            c = QColor(TEAM[leaf.team] if leaf.t == 'base' else COL[leaf.t])
            if leaf.t == 'base': c.setAlpha(140)
            p.setPen(cpen('#22262b')); p.setBrush(c); p.drawRect(QRectF(-a, -b, 2 * a, 2 * b))
            if leaf.t == 'pyramid':
                p.drawLine(QPointF(-a, -b), QPointF(a, b)); p.drawLine(QPointF(a, -b), QPointF(-a, b))
        if sel:
            pad = 3 / S
            hx = s.m.group_bounds(leaf.name)[0] if leaf.t == 'group' else max(leaf.sx, 1)
            hy = s.m.group_bounds(leaf.name)[1] if leaf.t == 'group' else leaf.sy
            p.setPen(cpen(ACC, 2.5, Qt.PenStyle.DashLine)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(-hx - pad, -hy - pad, 2 * hx + 2 * pad, 2 * hy + 2 * pad))
        p.restore()

    def paintEvent(s, _):
        p = QPainter(s); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = s.m.W * 2; h = s.m.W; S = s.v[2]
        p.fillRect(s.rect(), QColor('#e9e7e0'))
        p.translate(s.width() / 2 + s.v[0], s.height() / 2 + s.v[1]); p.scale(S, -S)
        p.fillRect(QRectF(-h, -h, W, W), QColor('#f6f5f0'))
        p.setPen(cpen('#dedbd1')); g = 10 if S * 10 >= 8 else 50; v = -h
        while v <= h + .01:
            p.drawLine(QPointF(v, -h), QPointF(v, h)); p.drawLine(QPointF(-h, v), QPointF(h, v)); v += g
        p.setPen(cpen('#666d74', 1.5)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawRect(QRectF(-h, -h, W, W))
        p.setPen(cpen('#d0702a', 1, Qt.PenStyle.DashLine))
        for leaf, src in expand(s.m):
            if leaf.t == 'teleporter':
                for tgt in (leaf.link, leaf.blink):
                    q = next((z for z, _ in expand(s.m) if z.t == 'teleporter' and tgt and z.name == tgt), None)
                    if q: p.drawLine(QPointF(leaf.x, leaf.y), QPointF(q.x, q.y))
        for leaf, src in expand(s.m):
            twin = src is not s.m.sel and s.m.sel and src.fam and src.fam == s.m.sel.fam
            s.draw_leaf(p, leaf, src is s.m.sel)
            if twin:
                p.save(); p.translate(leaf.x, leaf.y); p.rotate(leaf.r)  # the view is y-up, so +rotation is counter-clockwise, same as the 3D view
                pad = 3 / S; a, b = max(leaf.sx, 1), leaf.sy
                p.setPen(cpen(ACC, 1.5, Qt.PenStyle.DotLine)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(-a - pad, -b - pad, 2 * a + 2 * pad, 2 * b + 2 * pad)); p.restore()
            if src in s.m.msel:
                p.save(); p.translate(leaf.x, leaf.y)
                p.setPen(cpen('#c8453b', 2)); p.drawEllipse(QPointF(0, 0), 3 / S, 3 / S); p.restore()
        d = s.drag
        if d and d[0] == 'make':
            (ax, ay), (bx, by) = d[1], d[2]; p.setPen(cpen(ACC, 1.5, Qt.PenStyle.DashLine)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(min(ax, bx), min(ay, by), abs(ax - bx), abs(ay - by)))
        if S > 1:
            p.resetTransform(); p.setPen(QColor('#22262b'))
            for o in s.m.objs:
                if o.t == 'teleporter':
                    p.drawText(QPointF(s.width() / 2 + s.v[0] + (o.x + o.sx + 3 / S) * S,
                                        s.height() / 2 + s.v[1] - o.y * S), o.name)
        p.resetTransform(); s.draw_compass(p)

    def draw_compass(s, p):
        """This view never rotates -- up is always +Y, right is always +X -- so the compass is static."""
        cx, cy, R = 46, 46, 27
        p.setPen(QPen(QColor('#22262b', ), 1)); p.setBrush(QColor(246, 245, 240, 210))
        p.drawEllipse(QPointF(cx, cy), R + 15, R + 15)
        for dx, dy, label, col in ((1, 0, '+X', '#c8453b'), (-1, 0, '−X', '#c8453b'),
                                    (0, -1, '+Y', '#3c9a5a'), (0, 1, '−Y', '#3c9a5a')):
            p.setPen(QPen(QColor(col), 2))
            p.drawLine(QPointF(cx, cy), QPointF(cx + dx * R, cy + dy * R))
            p.setPen(QColor('#22262b'))
            p.drawText(QRectF(cx + dx * R - 12 + (0 if dx else 0), cy + dy * R - 9, 24, 18),
                       Qt.AlignmentFlag.AlignCenter, label)

    def mousePressEvent(s, e):
        s.setFocus(); p = e.position(); wx, wy = s.tow(p); b = e.button()
        pan = ('pan', p.x(), p.y(), s.v[0], s.v[1])
        ctrl = e.modifiers() & Qt.KeyboardModifier.ControlModifier
        if b in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton) or s.space:
            s.drag = pan; return
        if b != Qt.MouseButton.LeftButton: return
        m = s.m
        if s.tool == 'select':
            o = s.hit(wx, wy)
            if ctrl and o:
                m.toggle_msel(o); return
            if not ctrl: m.msel = []
            m.select(o)
            if o: m.push(); s.drag = ('move', o, o.x - wx, o.y - wy)
            else: s.drag = pan
        elif s.tool in ('box', 'pyramid', 'arc', 'cone'):
            a = (s.sn(wx), s.sn(wy)); s.drag = ['make', a, a, p.x(), p.y()]
        else:
            m.push(); o = m.add(s.tool, s.sn(wx), s.sn(wy)); m.make_family(o); m.select(o); m.changed.emit()

    def mouseMoveEvent(s, e):
        d = s.drag
        if not d: return
        p = e.position(); wx, wy = s.tow(p)
        if d[0] == 'pan': s.v[0] = d[3] + p.x() - d[1]; s.v[1] = d[4] + p.y() - d[2]; s.update()
        elif d[0] == 'move':
            d[1].x = s.sn(wx + d[2]); d[1].y = s.sn(wy + d[3]); s.m.sync_family(d[1]); s.m.changed.emit()
        else: d[2] = (s.sn(wx), s.sn(wy)); s.update()

    def mouseReleaseEvent(s, e):
        d = s.drag; s.drag = None
        if d and d[0] == 'make':
            (ax, ay), (bx, by) = d[1], d[2]; p = e.position()
            s.m.push(); o = s.m.add(s.tool, ax, ay)
            if math.hypot(p.x() - d[3], p.y() - d[4]) > 4 and ax != bx and ay != by:
                o.x, o.y, o.sx, o.sy = (ax + bx) / 2, (ay + by) / 2, abs(ax - bx) / 2, abs(ay - by) / 2
            s.m.make_family(o); s.m.select(o)
        s.m.changed.emit()

    def wheelEvent(s, e):
        p = e.position(); wx, wy = s.tow(p)
        S = min(14, max(.15, s.v[2] * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15)))
        s.v = [p.x() - s.width() / 2 - wx * S, p.y() - s.height() / 2 + wy * S, S]; s.update()

    def keyPressEvent(s, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat(): s.space = True

    def keyReleaseEvent(s, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat(): s.space = False


# ---------------------------------------------------------------------------
# 3D preview (software-rendered; textures via Qt's 2D projective quadToQuad)
# ---------------------------------------------------------------------------
class Preview(QWidget):
    LIGHT = (.4, -.5, .77)

    def __init__(s, m):
        super().__init__()
        s.m, s.yaw, s.pitch, s.dist, s.last = m, .5, .55, 1100., None
        s.setMinimumSize(320, 240)
        m.changed.connect(s.update); m.selected.connect(s.update)

    def basis(s):
        cp = math.cos(s.pitch); n = s.dist
        pos = (n * cp * math.sin(s.yaw), -n * cp * math.cos(s.yaw), n * math.sin(s.pitch))
        f = (-pos[0] / n, -pos[1] / n, -pos[2] / n); l = math.hypot(f[1], f[0]) or 1e-6
        r = (f[1] / l, -f[0] / l, 0)
        u = (r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0])
        return pos, f, r, u

    def proj(s, B, q):
        pos, f, r, u = B; d = [q[i] - pos[i] for i in range(3)]
        zc = sum(d[i] * f[i] for i in range(3))
        if zc < 5: return None
        k = s.height() * .9 / zc
        return QPointF(s.width() / 2 + sum(d[i] * r[i] for i in range(3)) * k,
                       s.height() / 2 - sum(d[i] * u[i] for i in range(3)) * k)

    def solids(s):
        """Return (points, color, source_obj, texinfo) faces, texinfo=None or (pixmap, uv0..uv3)."""
        F = []
        for leaf, src in expand(s.m):
            o = leaf
            if o.t == 'mesh':
                F += s.mesh_faces(o, src); continue
            a = math.radians(o.r); c, sn = math.cos(a), math.sin(a)
            P = lambda lx, ly, z: (o.x + lx * c - ly * sn, o.y + lx * sn + ly * c, z)
            col = QColor(TEAM[o.team] if o.t == 'base' else COL.get(o.t, '#8d939a'))
            C = lambda face, o=o, col=col: s.face_color(o, face, col)
            z0 = o.z; z1 = z0 + max(o.sz, .3)
            if o.t == 'arc':
                F += s.arc_faces(o, P, z0, z1, C); continue
            if o.t == 'cone':
                F += s.cone_faces(o, P, z0, z1, C('')); continue
            b = [P(-o.sx, -o.sy, z0), P(o.sx, -o.sy, z0), P(o.sx, o.sy, z0), P(-o.sx, o.sy, z0)]
            if o.t == 'pyramid':
                ap = P(0, 0, z1)
                F += [([b[i], b[(i + 1) % 4], ap], C(''), o, None) for i in range(4)] + [(b, C(''), o, None)]
            else:
                t = [(x, y, z1) for x, y, _ in b]
                F.append((b, C('bottom'), o, None)); F.append((t, C('top'), o, None))
                for i in range(4):
                    F.append(([b[i], b[(i + 1) % 4], t[(i + 1) % 4], t[i]], C('sides'), o, None))
        h = s.m.W; n = 8; wc = QColor(122, 133, 144, 120)
        for i in range(n):
            a2, b2 = -h + 2 * h * i / n, -h + 2 * h * (i + 1) / n
            for e1, e2 in (((a2, -h), (b2, -h)), ((a2, h), (b2, h)), ((-h, a2), (-h, b2)), ((h, a2), (h, b2))):
                F.append(([(*e1, 0), (*e2, 0), (*e2, 6), (*e1, 6)], wc, None, None))
        return F

    def mesh_faces(s, o, src):
        W, faces = mesh_world(o); out = []
        for idx, mat in faces:
            pts = [W[i] for i in idx if 0 <= i < len(W)]
            if len(pts) < 3: continue
            col = QColor(COL['mesh'])
            if mat in s.m.materials:
                c = s.m.materials[mat].color
                col = QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255), int(c[3] * 255))
            out.append((pts, col, src, None))
        return out

    def face_color(s, o, face, default):
        """Color of one face: its own material, else the object's all-faces material, else default."""
        name = o.mats.get(face) or o.matref
        if name in s.m.materials:
            c = s.m.materials[name].color
            return QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255), int(c[3] * 255))
        return default

    def arc_faces(s, o, P, z0, z1, C):
        N, A, ratio = max(3, o.divisions), o.angle, o.ratio
        full = A >= 360
        pts = N + 1 if not full else N
        ang = lambda i: math.radians(A * i / N)
        outer = [P(o.sx * math.cos(ang(i)), o.sy * math.sin(ang(i)), 0) for i in range(pts)]
        segs = pts if full else pts - 1
        V = lambda q, z: (q[0], q[1], z)
        F = []
        for i in range(segs):
            j = (i + 1) % pts
            F.append(([V(outer[i], z0), V(outer[j], z0), V(outer[j], z1), V(outer[i], z1)], C('outside'), o, None))
        if ratio <= 0:
            cb, ct = P(0, 0, z0), P(0, 0, z1)
            for i in range(segs):
                j = (i + 1) % pts
                F.append(([cb, V(outer[i], z0), V(outer[j], z0)], C('bottom'), o, None))
                F.append(([ct, V(outer[i], z1), V(outer[j], z1)], C('top'), o, None))
            if not full:
                F.append(([cb, V(outer[0], z0), V(outer[0], z1), ct], C('startside'), o, None))
                F.append(([cb, V(outer[-1], z0), V(outer[-1], z1), ct], C('endside'), o, None))
        else:
            ir = 1 - ratio
            inner = [P(o.sx * ir * math.cos(ang(i)), o.sy * ir * math.sin(ang(i)), 0) for i in range(pts)]
            for i in range(segs):
                j = (i + 1) % pts
                F.append(([V(inner[i], z0), V(inner[j], z0), V(outer[j], z0), V(outer[i], z0)], C('bottom'), o, None))
                F.append(([V(inner[i], z1), V(inner[j], z1), V(outer[j], z1), V(outer[i], z1)], C('top'), o, None))
                F.append(([V(inner[i], z0), V(inner[i], z1), V(inner[j], z1), V(inner[j], z0)], C('inside'), o, None))
            if not full:
                for idx, face in ((0, 'startside'), (-1, 'endside')):
                    F.append(([V(inner[idx], z0), V(outer[idx], z0), V(outer[idx], z1), V(inner[idx], z1)], C(face), o, None))
        return F

    def cone_faces(s, o, P, z0, z1, col):
        N = max(3, o.divisions)
        ring = [P(o.sx * math.cos(2 * math.pi * i / N), o.sy * math.sin(2 * math.pi * i / N), 0) for i in range(N)]
        apex = P(0, 0, z1); ctr = P(0, 0, z0)
        F = []
        for i in range(N):
            j = (i + 1) % N
            F.append(([(ring[i][0], ring[i][1], z0), (ring[j][0], ring[j][1], z0), apex], col, o, None))
            F.append(([ctr, (ring[i][0], ring[i][1], z0), (ring[j][0], ring[j][1], z0)], col, o, None))
        return F

    def paintEvent(s, _):
        p = QPainter(s); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(s.rect(), QColor('#1b2026')); B = s.basis(); h = s.m.W

        def poly(pts):
            q = [s.proj(B, v) for v in pts]
            return None if None in q else QPolygonF(q)

        g = poly([(-h, -h, 0), (h, -h, 0), (h, h, 0), (-h, h, 0)])
        if g: p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor('#2f3b33')); p.drawPolygon(g)
        p.setPen(QPen(QColor('#3d4b41'), 1)); v = -h
        while v <= h + .01:
            for a, b in (((v, -h, 0), (v, h, 0)), ((-h, v, 0), (h, v, 0))):
                pa, pb = s.proj(B, a), s.proj(B, b)
                if pa and pb: p.drawLine(pa, pb)
            v += 50
        pos = B[0]; L = math.sqrt(sum(x * x for x in s.LIGHT))

        def dist(face):
            cx = [sum(v[i] for v in face[0]) / len(face[0]) - pos[i] for i in range(3)]
            return sum(x * x for x in cx)

        for pts, col, o, tex in sorted(s.solids(), key=dist, reverse=True):
            pg = poly(pts)
            if pg is None: continue
            a, b, c = pts[:3]; u = [b[i] - a[i] for i in range(3)]; w = [c[i] - a[i] for i in range(3)]
            n_ = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
            nl = math.sqrt(sum(x * x for x in n_)) or 1
            k = .5 + .5 * abs(sum(n_[i] * s.LIGHT[i] for i in range(3))) / (nl * L)
            sh = QColor(int(col.red() * k), int(col.green() * k), int(col.blue() * k), col.alpha())
            p.setBrush(sh); p.setPen(QPen(ACC.lighter(150), 2) if o is s.m.sel else QPen(QColor(0, 0, 0, 90), 1))
            p.drawPolygon(pg)
        p.setPen(QColor('#98a0a8')); p.drawText(10, s.height() - 10, 'Drag to orbit, scroll to zoom')
        s.draw_gizmo(p, B)

    def draw_gizmo(s, p, B):
        """A small axis widget, fixed to a screen corner, that turns to match the current
        camera orientation -- the same trick CAD/3D tools use to show which way you're facing."""
        pos, f, r, u = B
        cx, cy, R = 52, 52, 34
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(27, 32, 38, 170))
        p.drawEllipse(QPointF(cx, cy), R + 14, R + 14)
        axes = (('X', (1.0, 0.0, 0.0), QColor('#e0645c')), ('Y', (0.0, 1.0, 0.0), QColor('#5cc47e')),
                ('Z', (0.0, 0.0, 1.0), QColor('#5c8ce0')))
        ends = []
        for name, v, col in axes:
            sx = sum(v[i] * r[i] for i in range(3)); sy = sum(v[i] * u[i] for i in range(3))
            depth = sum(v[i] * f[i] for i in range(3))
            ends.append((depth, cx + sx * R, cy - sy * R, col, name, True))
            ends.append((-depth, cx - sx * R, cy + sy * R, col, name, False))
        for _, ex, ey, col, name, is_pos in ends:  # farthest end of each axis drawn first, so nearer ones sit on top
            p.setPen(QPen(col, 2)); p.drawLine(QPointF(cx, cy), QPointF(ex, ey))
        for depth, ex, ey, col, name, is_pos in sorted(ends, key=lambda e: -e[0]):
            if is_pos:
                p.setPen(QPen(QColor('#1b2026'), 1)); p.setBrush(col)
            else:
                p.setPen(QPen(col, 1.5)); p.setBrush(QColor('#1b2026'))
            p.drawEllipse(QPointF(ex, ey), 9, 9)
            if is_pos:
                p.setPen(QColor('#ffffff'))
                p.drawText(QRectF(ex - 8, ey - 8, 16, 16), Qt.AlignmentFlag.AlignCenter, name)

    def mousePressEvent(s, e): s.last = e.position()

    def mouseMoveEvent(s, e):
        if s.last is None: return
        p = e.position(); s.yaw -= (p.x() - s.last.x()) * .008
        s.pitch = min(1.5, max(.05, s.pitch + (p.y() - s.last.y()) * .008)); s.last = p; s.update()

    def wheelEvent(s, e):
        s.dist = min(8000, max(40, s.dist * (.9 if e.angleDelta().y() > 0 else 1 / .9))); s.update()


def _qpath(poly):
    path = QPainterPath(); path.addPolygon(poly); return path


# ---------------------------------------------------------------------------
# Materials dialog
# ---------------------------------------------------------------------------
class MaterialsDialog(QDialog):
    APPROVED = 'http://images.bzflag.org/'

    def __init__(s, m, folder):
        super().__init__(); s.m, s.folder, s.cur, s._fixing = m, folder, None, False
        s.setWindowTitle('Materials'); s.resize(480, 360)
        root = QHBoxLayout(s)
        s.list = QListWidget(); s.list.addItems(sorted(m.materials)); s.list.currentTextChanged.connect(s.pick)
        root.addWidget(s.list, 1)
        col = QVBoxLayout(); root.addLayout(col, 2)
        s.name = QLineEdit()
        s.tex = QLineEdit()
        s.tex.setPlaceholderText('local texture name (no .png), or paste a full http:// image URL')
        s.tex.textChanged.connect(s.check_tex)
        browse = QPushButton('Load PNG...'); browse.clicked.connect(s.browse)
        rgb = QHBoxLayout(); s.r = QDoubleSpinBox(); s.g = QDoubleSpinBox(); s.b = QDoubleSpinBox(); s.al = QDoubleSpinBox()
        for sp in (s.r, s.g, s.b, s.al): sp.setRange(0, 1); sp.setSingleStep(.05); sp.setValue(1); rgb.addWidget(sp)
        form = QFormLayout(); form.addRow('Name', s.name)
        trow = QHBoxLayout(); trow.addWidget(s.tex); trow.addWidget(browse)
        form.addRow('Texture', trow); form.addRow('Color R G B A', rgb)
        col.addLayout(form)
        s.texwarn = QLabel(); s.texwarn.setWordWrap(True); s.texwarn.setStyleSheet('color:#b5651d')
        col.addWidget(s.texwarn)
        btns = QHBoxLayout()
        add = QPushButton('Add / Update'); add.clicked.connect(s.save)
        rm = QPushButton('Remove'); rm.clicked.connect(s.remove)
        btns.addWidget(add); btns.addWidget(rm); col.addLayout(btns)
        note = QLabel('Material colors show in the 3D preview. Textures and every other material setting '
                       '(addtexture, texmat, ambient...) are kept in the file but are not drawn yet.\n'
                       'For a texture on the internet, paste its full URL (e.g. ' + s.APPROVED + 'someone/name.png). '
                       'The game only loads http://, so a pasted https:// link is switched to http:// automatically.')
        note.setWordWrap(True); col.addWidget(note)
        col.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); bb.rejected.connect(s.accept); bb.accepted.connect(s.accept)
        col.addWidget(bb)

    def pick(s, name):
        s.cur = s.m.materials.get(name)
        if s.cur:
            s.name.setText(s.cur.name); s.tex.setText(s.cur.texture)
            for sp, v in zip((s.r, s.g, s.b, s.al), s.cur.color): sp.setValue(v)

    def check_tex(s, text):
        """The game only fetches http://, never https://, so silently drop the 's'. Then warn --
        without blocking -- if the URL isn't on the one server BZFlag leagues approve textures from."""
        if s._fixing: return
        if text.lower().startswith('https://'):
            s._fixing = True; pos = s.tex.cursorPosition()
            s.tex.setText('http://' + text[8:]); s.tex.setCursorPosition(max(0, pos - 1))
            s._fixing = False; return  # setText re-triggers this handler with the fixed text
        if '://' not in text:
            s.texwarn.setText(''); return
        msgs = []
        if not text.lower().startswith(s.APPROVED.lower()):
            msgs.append('Not under %s -- BZFlag leagues will not approve this texture.' % s.APPROVED)
        if not text.lower().endswith('.png'):
            msgs.append('A texture URL needs the .png file extension.')
        s.texwarn.setText(' '.join(msgs))

    def browse(s):
        path, _ = QFileDialog.getOpenFileName(s, 'Load texture', s.folder or '', 'PNG images (*.png)')
        if not path: return
        base = os.path.splitext(os.path.basename(path))[0]
        s.tex.setText(base); s.m.texcache[s.name.text() or base] = QPixmap(path)

    def save(s):
        name = s.name.text().strip()
        if not name: return
        old = s.m.materials.get(name)
        mat = Material(name, s.tex.text().strip(), (s.r.value(), s.g.value(), s.b.value(), s.al.value()),
                       list(old.extra) if old else [])  # keep addtexture/texmat/etc. when you edit
        s.m.materials[name] = mat
        if s.list.findItems(name, Qt.MatchFlag.MatchExactly) == []: s.list.addItem(name)
        s.m.changed.emit()

    def remove(s):
        name = s.name.text().strip()
        if name in s.m.materials:
            del s.m.materials[name]
            for it in s.list.findItems(name, Qt.MatchFlag.MatchExactly): s.list.takeItem(s.list.row(it))
            s.m.changed.emit()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class Win(QMainWindow):
    FIELDS = (('x', 'X'), ('y', 'Y'), ('z', 'Z'), ('sx', 'Half-width X'), ('sy', 'Half-width Y'),
              ('sz', 'Height'), ('r', 'Rotation'))

    def __init__(s, path=None):
        super().__init__(); s.m = Model(); s.resize(1340, 800)
        s.ed, s.pv = Editor(s.m), Preview(s.m)
        sp = QSplitter(); sp.addWidget(s.ed); sp.addWidget(s.pv); sp.setSizes([670, 670]); s.setCentralWidget(sp)
        s.settings = QSettings('BZMapMaker', 'BZMapMaker')
        s.recent = [p for p in s.settings.value('recentFiles', []) or [] if isinstance(p, str)]
        s.busy = s.pushed = False; s.build_menus(); s.build_panel(); s.statusBar()
        s.m.selected.connect(s.on_select); s.m.changed.connect(s.refresh)
        s.m.warn.connect(lambda msg: s.statusBar().showMessage(msg, 8000))
        if path: s.open_file(path)
        s.title()

    def act(s, menu, label, fn, key=None):
        a = QAction(label, s); a.triggered.connect(fn)
        if key: a.setShortcut(QKeySequence(key))
        menu.addAction(a); return a

    def build_menus(s):
        f = s.menuBar().addMenu('&File')
        s.act(f, 'New', s.new, 'Ctrl+N'); s.act(f, 'Open...', lambda: s.open_file(), 'Ctrl+O')
        s.recent_menu = f.addMenu('Open &Recent'); s.rebuild_recent()
        s.act(f, 'Save', s.save, 'Ctrl+S'); s.act(f, 'Save As...', lambda: s.save(True), 'Ctrl+Shift+S')
        e = s.menuBar().addMenu('&Edit')
        s.act(e, 'Undo', s.m.pop, 'Ctrl+Z'); s.act(e, 'Duplicate', s.m.duplicate, 'Ctrl+D')
        s.act(e, 'Delete', s.m.delete, 'Delete'); s.act(e, 'Fit view', s.ed.fit, 'Ctrl+0')
        s.act(e, 'Group Selected (Ctrl+click to multi-select)', s.do_group, 'Ctrl+G')
        ob = s.menuBar().addMenu('&Objects')
        s.act(ob, 'Materials...', s.open_materials)
        s.act(ob, 'Import Mesh Object...', s.import_mesh)
        tb = s.addToolBar('Tools'); g = QActionGroup(s)
        for t in ('select', 'box', 'pyramid', 'base', 'teleporter', 'arc', 'cone'):
            a = QAction(t.capitalize(), s); a.setCheckable(True); a.setChecked(t == 'select')
            a.triggered.connect(lambda _, t=t: setattr(s.ed, 'tool', t)); g.addAction(a); tb.addAction(a)
        tb.addSeparator(); sn = QAction('Snap to 5', s); sn.setCheckable(True); sn.setChecked(True)
        sn.toggled.connect(lambda v: setattr(s.ed, 'snap', v)); tb.addAction(sn)
        tb.addSeparator(); sy = QComboBox(); sy.addItems(SYM_LABELS)
        sy.setToolTip('New objects (and duplicates) get siblings that follow every edit.\n'
                      '120\u00b0 (3-team) symmetry can push a sibling outside a square world if the\n'
                      'object is placed farther from center than the world half-width; 90\u00b0 and\n'
                      '180\u00b0 symmetry are always safe.')
        sy.currentIndexChanged.connect(lambda i: setattr(s.m, 'sym', i)); tb.addWidget(sy)
        tb.addSeparator(); s.ws = QDoubleSpinBox(); s.ws.setRange(50, 5000); s.ws.setSingleStep(50); s.ws.setValue(400)
        s.ws.setPrefix('World size (half-width) '); s.ws.valueChanged.connect(s.set_world); tb.addWidget(s.ws)

    def build_panel(s):
        w = QWidget(); fl = QFormLayout(w); s.sp = {}
        for k, l in s.FIELDS:
            b = QDoubleSpinBox(); b.setRange(-5000, 5000); b.setDecimals(3); b.setSingleStep(1)
            b.valueChanged.connect(lambda v, k=k: s.edit(k, v)); fl.addRow(l, b); s.sp[k] = b
        s.team = QComboBox(); s.team.addItems(TEAM_NAMES[1:]); s.team.currentIndexChanged.connect(lambda i: s.edit('team', i + 1))
        s.gteam = QComboBox(); s.gteam.addItems(['No override'] + TEAM_NAMES[1:])
        s.gteam.currentIndexChanged.connect(lambda i: s.edit('team', i))
        s.gname = QComboBox(); s.gname.currentTextChanged.connect(lambda t: s.edit('name', t))
        s.link = QComboBox(); s.link.currentTextChanged.connect(lambda t: s.edit('link', '' if t == 'Nothing' else t))
        s.blink = QComboBox(); s.blink.currentTextChanged.connect(lambda t: s.edit('blink', '' if t == 'Nothing' else t))
        s.div = QSpinBox(); s.div.setRange(3, 64); s.div.valueChanged.connect(lambda v: s.edit('divisions', v))
        s.ang = QDoubleSpinBox(); s.ang.setRange(1, 360); s.ang.valueChanged.connect(lambda v: s.edit('angle', v))
        s.rat = QDoubleSpinBox(); s.rat.setRange(0, .95); s.rat.setSingleStep(.05); s.rat.valueChanged.connect(lambda v: s.edit('ratio', v))
        s.mat = QComboBox(); s.mat.setEditable(True); s.mat.currentTextChanged.connect(lambda t: s.edit('matref', t))
        fl.addRow('Team', s.team); fl.addRow('Group team override', s.gteam); fl.addRow('Group prefab', s.gname)
        fl.addRow('Front links to', s.link); fl.addRow('Back links to', s.blink)
        fl.addRow('Divisions', s.div); fl.addRow('Sweep angle', s.ang); fl.addRow('Hollow ratio', s.rat)
        fl.addRow('Material (all faces)', s.mat)
        s.face = {}
        for fk, fl_ in (('top', 'Top'), ('sides', 'Sides'), ('bottom', 'Bottom'), ('inside', 'Inside'),
                        ('outside', 'Outside'), ('startside', 'Start side'), ('endside', 'End side')):
            cb = QComboBox(); cb.currentTextChanged.connect(lambda t, k=fk: s.edit_face(k, t))
            fl.addRow(fl_ + ' material', cb); s.face[fk] = cb
        d = QDockWidget('Selected object'); d.setWidget(w); s.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d)
        s.dock = d; s.on_select()

    def edit(s, k, v):
        o = s.m.sel
        if s.busy or not o: return
        if not s.pushed: s.m.push(); s.pushed = True
        setattr(o, k, v)
        if k not in ('link', 'blink', 'name'): s.m.sync_family(o)
        s.busy = True; s.m.changed.emit(); s.busy = False

    def edit_face(s, k, v):
        o = s.m.sel
        if s.busy or not o: return
        if not s.pushed: s.m.push(); s.pushed = True
        if v: o.mats[k] = v
        else: o.mats.pop(k, None)
        s.m.sync_family(o); s.busy = True; s.m.changed.emit(); s.busy = False

    def on_select(s):
        s.pushed = False; s.refresh()

    def refresh(s):
        if s.busy: return
        o = s.m.sel; s.busy = True
        s.dock.widget().setEnabled(o is not None)
        s.dock.setWindowTitle('Selected object' + (': ' + o.t + (' ' + o.name if o.name else '') if o else ''))
        rows = {'x': True, 'y': True, 'z': True, 'sx': o and o.t not in ('group', 'mesh'), 'sy': o and o.t not in ('group', 'mesh'),
                'sz': o and o.t not in ('group', 'mesh'), 'r': o and o.t != 'mesh', 'team': o and o.t == 'base',
                'gteam': o and o.t == 'group', 'gname': o and o.t == 'group',
                'link': o and o.t == 'teleporter', 'blink': o and o.t == 'teleporter',
                'div': o and o.t in ('arc', 'cone'), 'ang': o and o.t == 'arc', 'rat': o and o.t == 'arc',
                'mat': o and o.t in ('box', 'pyramid', 'arc', 'cone')}
        for name, vis in rows.items():
            widget = {'x': s.sp['x'], 'y': s.sp['y'], 'z': s.sp['z'], 'sx': s.sp['sx'], 'sy': s.sp['sy'],
                      'sz': s.sp['sz'], 'r': s.sp['r'], 'team': s.team, 'gteam': s.gteam, 'gname': s.gname,
                      'link': s.link, 'blink': s.blink, 'div': s.div, 'ang': s.ang, 'rat': s.rat, 'mat': s.mat}[name]
            row = s.dock.widget().layout().labelForField(widget)
            widget.setVisible(bool(vis))
            if row: row.setVisible(bool(vis))
        lay = s.dock.widget().layout()
        for fk, cb in s.face.items():
            show = bool(o and any(k == fk for k, _ in FACES.get(o.t, [])))
            cb.setVisible(show)
            lab = lay.labelForField(cb)
            if lab: lab.setVisible(show)
        if o:
            for k, b in s.sp.items(): b.setValue(getattr(o, k))
            s.team.setCurrentIndex(o.team - 1 if o.t == 'base' else 0)
            s.gteam.setCurrentIndex(o.team if o.t == 'group' else 0)
            s.gname.clear(); s.gname.addItems(sorted(s.m.defines)); s.gname.setCurrentText(o.name if o.t == 'group' else '')
            for cb, cur in ((s.link, o.link), (s.blink, o.blink)):
                cb.clear(); cb.addItem('Nothing')
                cb.addItems([q.name for q in s.m.objs if q.t == 'teleporter' and q is not o])
                cb.setCurrentText(cur or 'Nothing')
            s.div.setValue(o.divisions); s.ang.setValue(o.angle if o.angle else 360); s.rat.setValue(o.ratio)
            s.mat.clear(); s.mat.addItems([''] + sorted(s.m.materials)); s.mat.setCurrentText(o.matref)
            for fk, cb in s.face.items():
                cur = o.mats.get(fk, '')
                cb.clear(); cb.addItems([''] + sorted(set(s.m.materials) | ({cur} if cur else set())))
                cb.setCurrentText(cur)
        s.ws.setValue(s.m.W); s.busy = False

    def set_world(s, v):
        if not s.busy and v != s.m.W: s.m.push(); s.m.W = v; s.ed.fit(); s.m.changed.emit()

    def do_group(s):
        if not s.m.group_selection():
            QMessageBox.information(s, 'Group', 'Ctrl+click at least two objects in the layout view first.')

    def open_materials(s):
        MaterialsDialog(s.m, os.path.dirname(s.m.path) if s.m.path else None).exec()

    def import_mesh(s):
        """Pull one or more 'mesh ... end' objects out of another .bzw (or a file that's
        just a mesh block on its own) and drop them into the current map at the origin."""
        folder = os.path.dirname(s.m.path) if s.m.path else ''
        path, _ = QFileDialog.getOpenFileName(s, 'Import mesh object', folder, 'BZFlag world files (*.bzw);;All files (*)')
        if not path: return
        try:
            with open(path, encoding='utf-8', errors='replace') as f: text = f.read()
        except OSError as e:
            QMessageBox.warning(s, 'Import failed', str(e)); return
        nw, objs, materials, defines, extras = parse(text)
        meshes = [o for o in objs if o.t == 'mesh']
        if not meshes:
            QMessageBox.information(s, 'Import mesh', 'No mesh object was found in that file.'); return
        s.m.push()
        for mo in meshes:
            mo.uid = s.m.nid; s.m.nid += 1
            s.m.objs.append(mo)
        for name, mat in materials.items():  # bring along any materials the mesh's faces refer to
            s.m.materials.setdefault(name, mat)
        s.m.select(meshes[-1]); s.m.changed.emit()
        skipped = len(objs) - len(meshes)
        msg = 'Imported %d mesh object%s from %s.' % (len(meshes), '' if len(meshes) == 1 else 's', os.path.basename(path))
        if skipped: msg += ' (%d other object%s in that file %s not imported.)' % (skipped, '' if skipped == 1 else 's', 'was' if skipped == 1 else 'were')
        s.statusBar().showMessage(msg, 8000)

    def title(s): s.setWindowTitle('BZ Map Maker - ' + (s.m.path or 'untitled'))

    def add_recent(s, path):
        path = os.path.abspath(path)
        s.recent = [path] + [p for p in s.recent if p != path]
        s.recent = s.recent[:12]
        s.settings.setValue('recentFiles', s.recent)
        s.rebuild_recent()

    def rebuild_recent(s):
        s.recent_menu.clear()
        existing = [p for p in s.recent if os.path.isfile(p)]
        if existing != s.recent:  # quietly drop files that were moved or deleted since last time
            s.recent = existing; s.settings.setValue('recentFiles', s.recent)
        if not existing:
            a = s.recent_menu.addAction('(no recent files)'); a.setEnabled(False); return
        for i, p in enumerate(existing):
            label = '%d  %s   [%s]' % (i + 1, os.path.basename(p), os.path.dirname(p))
            act = QAction(label, s); act.triggered.connect(lambda _, p=p: s.open_file(p))
            s.recent_menu.addAction(act)
        s.recent_menu.addSeparator()
        clr = QAction('Clear Recent Files', s); clr.triggered.connect(s.clear_recent)
        s.recent_menu.addAction(clr)

    def clear_recent(s):
        s.recent = []; s.settings.setValue('recentFiles', []); s.rebuild_recent()

    def new(s):
        s.m.push(); s.m.__init__(); s.m.path = None
        s.m.reset.emit(); s.m.selected.emit(); s.m.changed.emit(); s.title()

    def load_textures(s, folder):
        s.m.texcache = {}
        if not folder: return
        for name, mat in s.m.materials.items():
            if mat.texture:
                p = os.path.join(folder, mat.texture + '.png')
                if os.path.isfile(p): s.m.texcache[name] = QPixmap(p)

    def open_file(s, path=None):
        path = path or QFileDialog.getOpenFileName(s, 'Open world', '', 'BZFlag worlds (*.bzw);;All files (*)')[0]
        if not path: return
        try:
            with open(path, encoding='utf-8', errors='replace') as f: text = f.read()
        except OSError as e:
            QMessageBox.warning(s, 'Open failed', str(e)); return
        s.m.load(text); s.m.path = path; s.load_textures(os.path.dirname(path)); s.title(); s.add_recent(path)

    def save(s, as_new=False):
        path = s.m.path
        if as_new or not path:
            path = QFileDialog.getSaveFileName(s, 'Save world', 'map.bzw', 'BZFlag worlds (*.bzw)')[0]
        if not path: return
        try:
            with open(path, 'w', encoding='utf-8') as f: f.write(s.m.text())
        except OSError as e:
            QMessageBox.warning(s, 'Save failed', str(e)); return
        s.m.path = path; s.load_textures(os.path.dirname(path)); s.title(); s.add_recent(path)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = Win(sys.argv[1] if len(sys.argv) > 1 else None); w.show()
    sys.exit(app.exec())
