#!/usr/bin/env python3
"""BZ Map Maker - a BZFlag .bzw editor with a 2D layout view and a live 3D preview.

Run:  pip install PyQt6   then   python bzmapmaker.py [optional_map.bzw]
Works on Windows, macOS and Linux. The 3D preview is drawn in software (QPainter), so no
OpenGL is required. Textures are applied to box faces with Qt's 2D projective quadToQuad
transform, which is an exact planar homography, not an OpenGL approximation.
"""
import sys, os, re, math, json, copy, time, types, base64, hashlib, shlex, threading, queue, urllib.request

__version__ = '1.4.9'
import numpy as np
from dataclasses import dataclass, field, asdict, replace
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QPointF, QRectF, QRect, QSize, QSettings, QTimer, QUrl
from PyQt6.QtGui import (QPainter, QColor, QPen, QBrush, QPolygonF, QPainterPath, QAction,
                          QActionGroup, QKeySequence, QPixmap, QTransform, QImage, QIcon, QDesktopServices,
                          QShortcut, QTextCursor, QTextDocument, QTextCharFormat)
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QSplitter, QDockWidget, QFormLayout,
                              QDoubleSpinBox, QSpinBox, QComboBox, QFileDialog, QMessageBox, QDialog,
                              QListWidget, QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout, QLabel,
                              QDialogButtonBox, QInputDialog, QPlainTextEdit, QCheckBox, QTextEdit, QListWidgetItem)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
DEF = {'box': (10, 10, 9.4), 'pyramid': (8.2, 8.2, 8.2), 'base': (20, 20, 0),
       'teleporter': (.5, 4, 10), 'arc': (10, 10, 10), 'cone': (10, 10, 10), 'group': (1, 1, 1), 'mesh': (0, 0, 0),
       'zone': (10, 10, 1), 'weapon': (0, 0, 0)}
TEAM = ['', '#c8453b', '#3c9a5a', '#3a6fc4', '#8a55b5']
TEAM_NAMES = ['', 'Red', 'Green', 'Blue', 'Purple']
COL = {'box': '#8d939a', 'pyramid': '#b08a4e', 'teleporter': '#d0702a', 'arc': '#5c8a7a',
       'cone': '#7a6a9a', 'group': '#a0a4ad', 'mesh': '#9aa0a8', 'zone': '#6b7280', 'weapon': '#e0702a'}
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
ZONE_TEAM_COLORS = ['#1b1b1b', '#c8453b', '#3c9a5a', '#3a6fc4', '#8a55b5']  # 0=rogue(black)..4=purple


def zone_colors(o):
    """Colors a zone should show: one color per spawning team, or a single neutral gray if
    no team is set (e.g. a zone that only exists to drop flags in, not to spawn players)."""
    teams = sorted(set(t for t in o.zteam if 0 <= t <= 4))
    return [ZONE_TEAM_COLORS[t] for t in teams] if teams else [COL['zone']]


def zone_grid(sx, sy, target=8.0, min_cell=8.0, max_cells=16):
    """How a zone's footprint splits into checkerboard cells for both views. Cell size
    GROWS with the zone instead of staying fixed, so the total cell count stays bounded no
    matter how large the zone is -- a big zone gets coarser tiles, not thousands of tiny
    ones. Without this, a large zone could generate several thousand tiny triangles that
    the 3D view's per-pixel rasterizer has to redo on every single repaint."""
    cell = max(min_cell, min(2 * sx, 2 * sy) / target)
    nx = max(1, min(max_cells, round(2 * sx / cell)))
    ny = max(1, min(max_cells, round(2 * sy / cell)))
    return nx, ny, 2 * sx / nx, 2 * sy / ny


ACC = QColor('#0f6b6b')


@dataclass
class Obj:
    t: str
    x: float = 0; y: float = 0; z: float = 0
    sx: float = 1; sy: float = 1; sz: float = 1
    r: float = 0; team: int = 1; name: str = ''; link: str = ''; blink: str = ''
    mscale: tuple = (1.0, 1.0, 1.0)  # mesh leaf inside a group: the group chain's scale (see expand)
    iname: str = ''  # group instance name (BZW 'name' line in a group block); teleporters inside are named <iname>:<name>
    lface: str = 'b'; bface: str = 'f'  # teleporter: face of the link target you emerge from (front link -> its back, back link -> its front, unless changed)
    matref: str = ''
    phydrv: str = ''  # box/pyramid/arc/cone: name of a physics driver, if any
    ricochet: bool = False  # box/pyramid/arc/cone/group: shots bounce off instead of exploding
    tint: tuple = (1.0, 1.0, 1.0, 1.0)  # group only: tints every object inside it
    mats: dict = field(default_factory=dict)  # per-face materials (box, arc): face key -> material name
    mesh: str = ''  # key into MESHDATA: the mesh block's original text, kept verbatim
    flipz: bool = False  # pyramid only: point down instead of up
    phys: str = 'normal'  # box/pyramid/arc/cone: 'normal', 'drivethrough', 'shootthrough', 'passable'
    zteam: list = field(default_factory=list)   # zone only: which teams may spawn here (0=rogue..4=purple)
    zsafety: list = field(default_factory=list) # zone only: team flags (1-4) that fly here when dropped
    zflags: str = ''  # zone only: raw 'flag X' / 'zoneflag X N' lines, one per line, written verbatim
    divisions: int = 16; angle: float = 360.0; ratio: float = 0.0  # arc / cone only
    wcolor: int = 0  # weapon only: 0 rogue..4 purple, 5 observer, 6 rabbit
    tilt: float = 0.0  # weapon only: vertical aim angle, independent of horizontal rotation
    initdelay: float = 0.0; delay: list = field(default_factory=list)  # weapon only: fire timing
    wtype: str = ''  # weapon only: flag abbreviation of what it fires, e.g. SW
    trigger: str = ''; eventteam: int = 1  # weapon only: '' or 'oncap', and which team's cap fires it
    fam: str = ''; fam_k: int = 0; fold: int = 0; mode: str = ''   # symmetry family
    uid: int = 0
    texsize: str = ''  # box / pyramid / arc (4 numbers) or cone (2 numbers): BZW 'texsize' as written; '' = BZFlag's default (nothing is written)
    trot: float = 0.0  # arc leaf inside groups only: how far the enclosing groups turn the whole shape (see shape_turn)

    def __post_init__(s):
        s.mats = dict(s.mats)  # never share the dict between clones


@dataclass
class Material:
    name: str
    texture: str = ''
    color: tuple = (1.0, 1.0, 1.0, 1.0)
    extra: list = field(default_factory=list)  # every other line (addtexture, texmat, ambient...), kept as-is
    raw: list = field(default_factory=list)    # the block's original lines; written back unchanged until edited


@dataclass
class PhysicsDriver:
    """A physics driver affects any tank touching the object it's attached to: linear push
    (conveyors, trampolines), angular spin (turntables), slide (slippery surfaces), or an
    instant-death message (landmines). Each capability is written only if has_* is set, so a
    driver that only uses 'slide' doesn't also emit an unwanted 'linear 0 0 0' line."""
    name: str
    linear: tuple = (0.0, 0.0, 0.0)
    angular: tuple = (0.0, 0.0, 0.0)  # (rotation speed, center x, center y)
    slide: float = 0.0
    death: str = ''
    has_linear: bool = False
    has_angular: bool = False
    has_slide: bool = False
    has_death: bool = False
    extra: list = field(default_factory=list)
    raw: list = field(default_factory=list)


@dataclass
class TexMatrix:
    """A textureMatrix: how a material's texture shifts, spins or scales, fixed or over time. Every
    setting is optional (None = not written), so a matrix that only shifts doesn't also emit
    unwanted zero lines. Lines this editor doesn't know are kept in `extra`."""
    name: str
    fixedshift: tuple = None   # (u, v) time-invariant shift
    fixedspin: float = None    # time-invariant rotation
    fixedscale: tuple = None   # (u, v) time-invariant scale
    shift: tuple = None        # (u frequency, v frequency)
    spin: float = None         # rotation frequency
    scale: tuple = None        # (u frequency, v frequency, u scale, v scale)
    center: tuple = None       # (u, v) point that spin and scale turn around
    extra: list = field(default_factory=list)
    raw: list = field(default_factory=list)


TEXMAT_FIELDS = (('scale', 4), ('spin', 1), ('shift', 2), ('center', 2),
                 ('fixedscale', 2), ('fixedspin', 1), ('fixedshift', 2))  # written in the wiki's order


def texmatrix_block(t):
    if t.raw: return ['textureMatrix'] + ['  ' + b for b in t.raw] + ['end']  # untouched since import
    ln = ['textureMatrix', '  name %s' % t.name]
    for key, n in TEXMAT_FIELDS:
        v = getattr(t, key)
        if v is None: continue
        v = (v,) if n == 1 and not isinstance(v, (tuple, list)) else v
        ln.append('  %s %s' % (key, ' '.join(num(c) for c in v)))
    return ln + ['  ' + e for e in t.extra] + ['end']


def _texmat_names(mat):
    """Texturematrix a material points at (its 'texmat <name>' lines)."""
    return [q[1] for q in (ln.split() for ln in (mat.raw or mat.extra)) if len(q) > 1 and q[0].lower() == 'texmat']


def _retarget_texmat(mat, mapping):
    fix = lambda ln: ('texmat ' + mapping[ln.split()[1]]) if len(ln.split()) > 1 and ln.split()[0].lower() == 'texmat' and ln.split()[1] in mapping else ln
    mat.extra = [fix(ln) for ln in mat.extra]; mat.raw = [fix(ln) for ln in mat.raw]


def _tm_sig(t):
    f = lambda v: tuple(v) if isinstance(v, (tuple, list)) else v
    return tuple(f(getattr(t, k)) for k, _n in TEXMAT_FIELDS) + (tuple(t.extra),)


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


def _xform_vec(k, v, mode, fold):
    """Same rotation/mirror as _xform, but for a direction (e.g. a physics driver's push
    velocity) rather than a position -- no translation, since a direction has no location."""
    x, y, z = v
    if mode == 'rot' and k:
        a = math.radians(k * 360.0 / fold); c, s = math.cos(a), math.sin(a)
        return (x * c - y * s, x * s + y * c, z)
    if mode == 'mirror_lr' and k == 1: return (-x, y, z)
    if mode == 'mirror_tb' and k == 1: return (x, -y, z)
    return (x, y, z)


def _inv_vec(k, v, mode, fold):
    if mode == 'rot' and k: return _xform_vec((-k) % fold, v, mode, fold)
    if mode in ('mirror_lr', 'mirror_tb') and k == 1: return _xform_vec(1, v, mode, fold)
    return v


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
    kx, ky, kz = o.mscale
    out = []
    for x, y, z in verts:
        x = (x + o.sx) * kx; y = (y + o.sy) * ky; z = (z + o.sz) * kz
        out.append((x * c - y * sn + o.x, x * sn + y * c + o.y, z + o.z))
    return out, faces


def parse(text):
    """Read world, box, pyramid, base, teleporter, arc, cone, mesh, material, define/group
    and link blocks. Meshes and materials keep their original text; any other block
    (physics, zones, dynamic colors...) is returned verbatim in `extras` so it isn't lost."""
    # A '#bzmm-sym ...' comment is BZ Map Maker's own marker, recording which symmetry family
    # the object on the next line belonged to when saved -- plain BZFlag and every other tool
    # sees only an ordinary ignorable comment, but this editor uses it to restore symmetry
    # linkage that the base .bzw format has no other way to express. metas[i] holds the
    # marker (if any) for lines[i], the object block it immediately precedes.
    lines, metas = [], []
    pending = None
    for raw in text.splitlines():
        code, _, comment = raw.partition('#')
        comment = comment.strip()
        if comment.startswith('bzmm-sym'):
            pending = {}
            for kv in comment[len('bzmm-sym'):].split():
                if '=' in kv:
                    kk, vv = kv.split('=', 1); pending[kk] = vv
            continue
        code = code.rstrip()
        if not code.strip(): continue
        lines.append(code); metas.append(pending); pending = None
    i = 0
    nw, objs, materials, defines, links, extras, options_text, physics = None, [], {}, {}, [], [], '', {}
    world = new_world(); texmats = {}
    TOP = {'world', 'material', 'define', 'enddef', 'group', 'box', 'pyramid', 'base', 'teleporter', 'arc',
           'cone', 'mesh', 'meshbox', 'meshpyr', 'tetra', 'sphere', 'link', 'physics', 'zone', 'weapon',
           'options', 'dynamiccolor', 'texturematrix', 'waterlevel', 'wall', 'transform'}

    def nm(a, d):
        out = []
        for idx, val in enumerate(d):
            try: out.append(float(a[idx]))
            except Exception: out.append(val)
        return out

    def apply_meta(o, meta):
        if not meta: return
        o.fam = meta.get('fam', '')
        try: o.fam_k = int(meta.get('k', 0))
        except ValueError: pass
        try: o.fold = int(meta.get('fold', 0))
        except ValueError: pass
        o.mode = meta.get('mode', '')

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
        line_meta = metas[i]; p = lines[i].split(); i += 1
        k = p[0].lower()
        if k == 'world':
            while i < len(lines):  # read line by line so unknown parameters are kept verbatim
                q = lines[i].split(); i += 1
                key = q[0].lower()
                if key == 'end': break
                if key == 'size':
                    try: nw = float(q[1])
                    except (IndexError, ValueError): pass
                elif key == 'name': world['name'] = ' '.join(q[1:])
                elif key == 'flagheight':
                    try: world['flagheight'] = float(q[1])
                    except (IndexError, ValueError): pass
                elif key == 'nowalls': world['nowalls'] = True
                elif key == 'freectfspawns': world['freectf'] = True
                else: world['extra'].append(' '.join(q))
        elif k == 'material':
            body = read_raw_block()
            name, tex, color, extra, adds = '', '', (1.0, 1.0, 1.0, 1.0), [], []
            for ln in body:
                q = ln.split(); kk = q[0].lower()
                if kk == 'name' and len(q) > 1: name = q[1]
                elif kk == 'texture' and len(q) > 1: tex = q[1]
                elif kk == 'addtexture' and len(q) > 1: adds.append(ln)  # older maps: same as 'texture' (see below)
                elif kk in ('diffuse', 'color'):  # 'color' is a synonym for 'diffuse'
                    try: color = tuple(float(c) for c in (q[1:] + ['1', '1', '1', '1'])[:4])
                    except ValueError: pass
                else: extra.append(ln)
            if adds:  # 'addtexture' means the same as 'texture': the first one is the material's texture when it has no
                # 'texture' line, so the preview, the Materials dialog and URL fetching treat it alike. Extra layers stay in `extra`.
                if not tex: tex = adds.pop(0).split()[1]
                extra += adds
            name = name or (p[1] if len(p) > 1 else 'mat%d' % (len(materials) + 1))
            materials[name] = Material(name, tex, color, extra, list(body))
        elif k == 'texturematrix':
            body = read_raw_block(); tm = TexMatrix(''); counts = dict(TEXMAT_FIELDS)
            for ln in body:
                q = ln.split()
                if not q: continue
                kk = q[0].lower()
                if kk == 'name' and len(q) > 1: tm.name = q[1]; continue
                if kk in counts:
                    try:
                        v = tuple(float(a) for a in q[1:1 + counts[kk]])
                        if len(v) == counts[kk]:
                            setattr(tm, kk, v[0] if counts[kk] == 1 else v); continue
                    except ValueError: pass
                tm.extra.append(ln)  # anything unrecognised is written back as it was
            tm.name = tm.name or (p[1] if len(p) > 1 else 'texmat%d' % (len(texmats) + 1)); tm.raw = list(body)
            texmats[tm.name] = tm
        elif k == 'physics':
            body = read_raw_block()
            name, linear, angular, slide, death = '', (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0.0, ''
            hl = ha = hs = hd = False; extra = []
            for ln in body:
                q = ln.split()
                if not q: continue
                kk = q[0].lower()
                if kk == 'name' and len(q) > 1: name = q[1]
                elif kk == 'linear':
                    try: linear = tuple(float(a) for a in (q[1:4] + ['0', '0', '0'])[:3]); hl = True
                    except ValueError: pass
                elif kk == 'angular':
                    try: angular = tuple(float(a) for a in (q[1:4] + ['0', '0', '0'])[:3]); ha = True
                    except ValueError: pass
                elif kk == 'slide':
                    try: slide = float(q[1]); hs = True
                    except (IndexError, ValueError): pass
                elif kk == 'death':
                    death = ' '.join(q[1:]); hd = True
                else:
                    extra.append(ln)
            name = name or 'phydrv%d' % (len(physics) + 1)
            physics[name] = PhysicsDriver(name, linear, angular, slide, death, hl, ha, hs, hd, extra, list(body))
        elif k == 'options':
            if not options_text: options_text = '\n'.join(read_raw_block())  # only once per map
            else: read_raw_block()
        elif k == 'mesh':
            body = read_raw_block()
            name = next((b.split()[1] for b in body if b.lower().startswith('name ') and len(b.split()) > 1), '')
            o = Obj('mesh', 0, 0, 0, 0, 0, 0, 0, 1, name); o.mesh = mesh_register(body); objs.append(o)
        elif k == 'zone':
            body = read_raw_block()
            name = ''; zflags = []; zteam = []; zsafety = []
            zx = zy = zz = 0.0; zsx, zsy, zsz = DEF['zone']; zr = 0.0
            for ln in body:
                q = ln.split()
                if not q: continue
                kk = q[0].lower()
                if kk == 'name' and len(q) > 1: name = q[1]
                elif kk in ('position', 'pos'):
                    try: zx, zy, zz = (float(a) for a in q[1:4])
                    except Exception: pass
                elif kk == 'size':
                    try: zsx, zsy, zsz = (float(a) for a in q[1:4])
                    except Exception: pass
                elif kk in ('rotation', 'rot'):
                    try: zr = float(q[1])
                    except Exception: pass
                elif kk in ('flag', 'zoneflag'):
                    zflags.append(ln)
                elif kk == 'team':
                    for a in q[1:]:
                        try: zteam.append(int(a))
                        except ValueError: pass
                elif kk == 'safety':
                    for a in q[1:]:
                        try: zsafety.append(int(a))
                        except ValueError: pass
            zo = Obj('zone', zx, zy, zz, zsx, zsy, zsz, zr, 1, name)
            zo.zflags = '\n'.join(zflags); zo.zteam = zteam; zo.zsafety = zsafety
            apply_meta(zo, line_meta)
            objs.append(zo)
        elif k == 'weapon':
            body = read_raw_block()
            name = ''; wx = wy = wz = 0.0; wr = 0.0; wcolor = 0; tilt = 0.0
            initdelay = 0.0; delay = []; wtype = ''; trigger = ''; eventteam = 1
            for ln in body:
                q = ln.split()
                if not q: continue
                kk = q[0].lower()
                if kk == 'name' and len(q) > 1: name = q[1]
                elif kk in ('position', 'pos'):
                    try: wx, wy, wz = (float(a) for a in q[1:4])
                    except Exception: pass
                elif kk in ('rotation', 'rot'):
                    try: wr = float(q[1])
                    except Exception: pass
                elif kk == 'color':
                    try: wcolor = int(float(q[1]))
                    except Exception: pass
                elif kk == 'tilt':
                    try: tilt = float(q[1])
                    except Exception: pass
                elif kk == 'initdelay':
                    try: initdelay = float(q[1])
                    except Exception: pass
                elif kk == 'delay':
                    try: delay = [float(a) for a in q[1:]]
                    except Exception: pass
                elif kk == 'type' and len(q) > 1: wtype = q[1]
                elif kk == 'trigger' and len(q) > 1: trigger = q[1].lower()
                elif kk == 'eventteam':
                    try: eventteam = int(float(q[1]))
                    except Exception: pass
            wo = Obj('weapon', wx, wy, wz, 0, 0, 0, wr, 1, name)
            wo.wcolor, wo.tilt, wo.initdelay = wcolor, tilt, initdelay
            wo.delay, wo.wtype, wo.trigger, wo.eventteam = delay, wtype, trigger, eventteam
            apply_meta(wo, line_meta)
            objs.append(wo)
        elif k == 'link':
            v = read_block(); links.append(((v.get('from') or [''])[0], (v.get('to') or [''])[0]))
        elif k == 'define':
            name = p[1] if len(p) > 1 else 'group%d' % (len(defines) + 1)
            sub = []
            while i < len(lines):
                if lines[i].split()[0].lower() == 'enddef': i += 1; break
                sub.append(lines[i]); i += 1
            _, kids, *_ = parse('\n'.join(sub))
            defines[name] = kids
        elif k == 'group':
            v = read_block()
            name = p[1] if len(p) > 1 else ''
            x, y, z = nm(v.get('shift'), (0, 0, 0))
            r = nm(v.get('rotation', v.get('rot')), (0,))[0]
            gsx, gsy, gsz = nm(v.get('scale'), (1, 1, 1))
            team = int(nm(v.get('team'), (0,))[0])
            go = Obj('group', x, y, z, gsx or 1.0, gsy or 1.0, gsz or 1.0, r, team, name)
            if v.get('tint'): go.tint = tuple(nm(v['tint'], (1.0, 1.0, 1.0, 1.0)))
            go.ricochet = 'ricochet' in v
            hd, hs = 'drivethrough' in v, 'shootthrough' in v
            go.phys = 'passable' if (hd and hs) else 'drivethrough' if hd else 'shootthrough' if hs else 'normal'
            go.matref = (v.get('matref') or [''])[0]
            go.phydrv = (v.get('phydrv') or [''])[0]
            go.iname = ' '.join(v.get('name') or [])
            apply_meta(go, line_meta)
            objs.append(go)
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
            o.phydrv = (v.get('phydrv') or [''])[0]
            for fk, _ in FACES.get(k, []):
                vv = v.get(fk)
                if vv and len(vv) >= 2 and vv[0].lower() == 'matref': o.mats[fk] = vv[1]
            if k == 'arc':
                o.divisions = int(nm(v.get('divisions'), (16,))[0])
                o.angle = nm(v.get('angle'), (360,))[0]
                o.ratio = nm(v.get('ratio'), (0,))[0]
            if k == 'cone':
                o.divisions = int(nm(v.get('divisions'), (16,))[0])
            if k in ('arc', 'cone', 'box', 'pyramid'): o.texsize = ' '.join(v.get('texsize') or [])
            if k == 'pyramid': o.flipz = 'flipz' in v
            hd, hs, hp = 'drivethrough' in v, 'shootthrough' in v, 'passable' in v
            o.phys = 'passable' if (hp or (hd and hs)) else 'drivethrough' if hd else 'shootthrough' if hs else 'normal'
            o.ricochet = 'ricochet' in v
            apply_meta(o, line_meta)
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
        if not sep or face.lower() not in ('f', 'b', '*', ''): name, face = ident, default_face  # no face given, or 'inst:tele'
        face = face.lower() or default_face
        if name.isdigit() and 0 <= int(name) < len(tele_order): name = tele_order[int(name)].name
        return name, face

    lists = [objs] + list(defines.values())
    uses = {}
    for lst in lists:
        for g in lst:
            if g.t == 'group': uses[g.name] = uses.get(g.name, 0) + 1

    def find_tele(name):
        """Teleporters a link endpoint names: top-level ones, or ones inside group instances
        (<instance>:<teleporter>, chaining through nested instances). Returns (teleporters,
        plain name, instance-name chain, prefab names along the chain)."""
        parts = name.split(':'); lst, chain, defs = objs, [], []
        for part in parts[:-1]:
            g = next((g for g in lst if g.t == 'group' and g.iname == part), None)
            if g is None: return [], name, (), ()
            chain.append(part); defs.append(g.name); lst = defines.get(g.name, [])
        return [k for k in lst if k.t == 'teleporter' and k.name == parts[-1]], parts[-1], tuple(chain), tuple(defs)

    def places(name):  # how many places (top level, or one prefab each) hold a teleporter with this name
        return sum(1 for lst in lists if any(k.t == 'teleporter' and k.name == name for k in lst))

    raw = []
    for f, t in links:
        fn, fface = resolve(f, 'f')
        tn, tface = resolve(t, 'b' if fface == 'f' else 'f')
        if tface not in ('f', 'b'): tface = 'b' if fface == 'f' else 'f'
        srcs, _, schain, sdefs = find_tele(fn); tgts, tplain, tchain, tdefs = find_tele(tn)
        ok = bool(srcs)
        if ok and (schain or tchain):
            # Links are stored per teleporter, and a group's teleporters belong to its prefab, shared by
            # every instance. Keep only links that come back out exactly as they went in; anything else
            # (e.g. between two instances) is carried through untouched below.
            unshared = lambda ds: all(uses.get(d, 0) == 1 for d in ds)
            ok = bool(tgts) and (schain == tchain or (unshared(sdefs) and unshared(tdefs) and places(tplain) == 1))
        if not ok: raw.append((f, t)); continue
        for o in srcs:
            if fface == 'f': o.link, o.lface = (tplain if tchain else tn), tface
            elif fface == 'b': o.blink, o.bface = (tplain if tchain else tn), tface
    world['rawlinks'] = raw
    return nw, objs, materials, defines, extras, options_text, physics, world, texmats


def obj_block(o):
    if o.t == 'zone':
        ln = ['zone']
        if o.name: ln.append('  name %s' % o.name)
        ln += ['  position %s %s %s' % (num(o.x), num(o.y), num(o.z)),
               '  size %s %s %s' % (num(o.sx), num(o.sy), num(o.sz)), '  rotation %s' % num(o.r)]
        ln += ['  ' + fl for fl in o.zflags.splitlines() if fl.strip()]
        if o.zteam: ln.append('  team ' + ' '.join(str(t) for t in o.zteam))
        if o.zsafety: ln.append('  safety ' + ' '.join(str(t) for t in o.zsafety))
        return ln + ['end']
    if o.t == 'weapon':
        ln = ['weapon']
        if o.name: ln.append('  name %s' % o.name)
        ln += ['  position %s %s %s' % (num(o.x), num(o.y), num(o.z)), '  rotation %s' % num(o.r)]
        if o.wcolor: ln.append('  color %d' % o.wcolor)
        if o.tilt: ln.append('  tilt %s' % num(o.tilt))
        if o.initdelay: ln.append('  initdelay %s' % num(o.initdelay))
        if o.delay: ln.append('  delay ' + ' '.join(num(d) for d in o.delay))
        if o.wtype: ln.append('  type %s' % o.wtype)
        if o.trigger: ln.append('  trigger %s' % o.trigger)
        if o.trigger == 'oncap': ln.append('  eventteam %d' % o.eventteam)
        return ln + ['end']
    if o.t == 'mesh':
        ln = ['mesh'] + ['  ' + b for b in MESHDATA.get(o.mesh, [])]
        if o.x or o.y or o.z: ln.append('  shift %s %s %s' % (num(o.x), num(o.y), num(o.z)))
        return ln + ['end']
    if o.t == 'group':
        # Order matters inside a group: BZFlag applies transforms as written, so scale comes first,
        # then rotation, then shift.
        ln = ['group %s' % o.name]
        if (o.sx, o.sy, o.sz) != (1, 1, 1): ln.append('  scale %s %s %s' % (num(o.sx), num(o.sy), num(o.sz)))
        ln += ['  rotation %s' % num(o.r), '  shift %s %s %s' % (num(o.x), num(o.y), num(o.z))]
        if o.team: ln.append('  team %d' % o.team)
        if tuple(o.tint) != (1.0, 1.0, 1.0, 1.0): ln.append('  tint %s %s %s %s' % tuple(num(c) for c in o.tint))
        if o.phys == 'passable': ln += ['  drivethrough', '  shootthrough']
        elif o.phys == 'drivethrough': ln.append('  drivethrough')
        elif o.phys == 'shootthrough': ln.append('  shootthrough')
        if o.matref: ln.append('  matref %s' % o.matref)
        if o.phydrv: ln.append('  phydrv %s' % o.phydrv)
        if o.ricochet: ln.append('  ricochet')
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
    if o.t in ('arc', 'cone', 'box', 'pyramid') and o.texsize: ln.append('  texsize %s' % o.texsize)
    if o.t == 'pyramid' and o.flipz: ln.append('  flipz')
    if o.t in ('box', 'pyramid', 'arc', 'cone') and o.phys != 'normal': ln.append('  ' + o.phys)
    if o.t in ('box', 'pyramid', 'arc', 'cone') and o.ricochet: ln.append('  ricochet')
    if o.phydrv: ln.append('  phydrv %s' % o.phydrv)
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


def new_world():
    """Settings for the 'world' block other than its size (which lives in Model.W).
    flagheight None = not set, so BZFlag's own default applies and nothing is written.
    `extra` keeps any parameter this editor doesn't know about, so it survives a round trip."""
    return {'name': '', 'flagheight': None, 'nowalls': False, 'freectf': False, 'extra': [],
            'rawlinks': []}  # links the editor can't model (wildcards, links between group instances): kept verbatim


def world_block(w, size):
    ln = ['world']
    if w.get('name'): ln.append('  name %s' % w['name'])
    ln.append('  size %s' % num(size))
    if w.get('flagheight') is not None: ln.append('  flagHeight %s' % num(w['flagheight']))
    if w.get('nowalls'): ln.append('  noWalls')
    if w.get('freectf'): ln.append('  freeCtfSpawns')
    return ln + ['  ' + e for e in w.get('extra', [])] + ['end']


def physics_block(p):
    if p.raw: return ['physics'] + ['  ' + b for b in p.raw] + ['end']  # untouched since import
    ln = ['physics', '  name %s' % p.name]
    if p.has_linear: ln.append('  linear %s %s %s' % tuple(num(c) for c in p.linear))
    if p.has_angular: ln.append('  angular %s %s %s' % tuple(num(c) for c in p.angular))
    if p.has_slide: ln.append('  slide %s' % num(p.slide))
    if p.has_death: ln.append('  death %s' % p.death)
    return ln + ['  ' + e for e in p.extra] + ['end']


# ---------------------------------------------------------------------------
# Custom object library
# ---------------------------------------------------------------------------
# A library object is an ordinary .bzw file holding meshes (or any other objects), the materials
# they use, and any 'define' prefabs they need. An optional first-lines comment gives it a menu entry:
#   #bzmm-object name="Lamp post" category="Street" description="A tall street lamp"
# Placing one adds its content to the map as a group instance, so many copies share one definition.
LIB_TAG = '#bzmm-object'
LIB_SKIP = ('teleporter', 'zone', 'weapon')  # these only make sense inside one particular map


def user_library_dir():
    """The person's own objects folder (Linux ~/.local/share, Windows %APPDATA%, macOS Application Support)."""
    if sys.platform == 'win32':
        return os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'BZMapMaker', 'objects')
    if sys.platform == 'darwin':
        return os.path.expanduser('~/Library/Application Support/BZMapMaker/objects')
    return os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), 'bzmapmaker', 'objects')


def texture_cache_dir():
    """Where downloaded textures are kept so a map opens instantly (and offline) the next time."""
    if sys.platform == 'win32':
        return os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'BZMapMaker', 'cache', 'textures')
    if sys.platform == 'darwin':
        return os.path.expanduser('~/Library/Caches/BZMapMaker/textures')
    return os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'bzmapmaker', 'textures')


def is_remote_texture(name):
    return bool(name) and name.lower().startswith(('http://', 'https://'))


MAX_TEXTURE_BYTES = 8 * 1024 * 1024  # a texture bigger than this is not downloaded for the preview


def download_texture(url, timeout=8):
    """The bytes of an image on the web, or from the disk cache if this URL was fetched before. Raises on failure.
    Safe to call from a background thread."""
    key = os.path.join(texture_cache_dir(), hashlib.sha1(url.encode('utf-8')).hexdigest() + '.img')
    try:
        with open(key, 'rb') as f: return f.read()
    except OSError: pass
    req = urllib.request.Request(url, headers={'User-Agent': 'BZMapMaker/' + __version__})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_TEXTURE_BYTES + 1)
    if len(data) > MAX_TEXTURE_BYTES: raise ValueError('image is too large')
    try:
        os.makedirs(os.path.dirname(key), exist_ok=True)
        with open(key + '.part', 'wb') as f: f.write(data)
        os.replace(key + '.part', key)
    except OSError: pass  # no cache is fine, just slower next time
    return data


def system_library_dirs():
    """Read-only folders of objects that came with the program (the .deb installs /usr/share/bzmapmaker/objects)."""
    here = os.path.dirname(os.path.abspath(__file__)); out = []
    cands = [os.path.join(p, 'objects') for p in (getattr(sys, '_MEIPASS', None), here) if p]
    if sys.platform not in ('win32', 'darwin'): cands += ['/usr/local/share/bzmapmaker/objects', '/usr/share/bzmapmaker/objects']
    for c in cands:
        c = os.path.abspath(c)
        if os.path.isdir(c) and c not in out: out.append(c)
    return out


@dataclass
class LibItem:
    name: str
    category: str = 'Other'
    description: str = ''
    path: str = ''
    builtin: bool = False


def parse_lib_header(text):
    for ln in text.splitlines()[:15]:
        if ln.strip().lower().startswith(LIB_TAG):
            try: parts = shlex.split(ln.strip()[len(LIB_TAG):])
            except ValueError: return {}
            return dict(p.split('=', 1) for p in parts if '=' in p)
    return {}


def lib_header(name, category, description):
    return '%s name=%s category=%s description=%s' % (LIB_TAG, shlex.quote(name), shlex.quote(category), shlex.quote(description))


def lib_slug(name):
    return re.sub(r'[^a-z0-9_]+', '_', name.lower()).strip('_') or 'object'


def scan_library():
    """Every object in the program's folders and the person's folder; theirs replace same-named built-ins."""
    found = {}
    roots = [(d, True) for d in system_library_dirs()] + [(user_library_dir(), False)]
    for root, builtin in roots:
        for dirpath, dirs, files in os.walk(root):
            dirs.sort()
            for f in sorted(files):
                if not f.lower().endswith('.bzw'): continue
                path = os.path.join(dirpath, f)
                try:
                    with open(path, encoding='utf-8', errors='replace') as fh: head = fh.read(3000)
                except OSError: continue
                meta = parse_lib_header(head)
                rel = os.path.relpath(dirpath, root)
                name = meta.get('name') or os.path.splitext(f)[0].replace('_', ' ').replace('-', ' ').strip().title()
                cat = meta.get('category') or ('Other' if rel == '.' else rel.replace(os.sep, ' / '))
                found[name.lower()] = LibItem(name, cat, meta.get('description', ''), path, builtin)
    return sorted(found.values(), key=lambda i: (i.category.lower(), i.name.lower()))


_LIBCACHE = {}


def load_library_content(path):
    """(objs, defines, materials) of a library file with the map-specific objects removed, or None."""
    try:
        key = (path, os.path.getmtime(path))
        if key not in _LIBCACHE:
            with open(path, encoding='utf-8', errors='replace') as fh: text = fh.read()
            _nw, objs, materials, defines, _x, _o, _p, _w, texmats = parse(text)
            objs = [o for o in objs if o.t not in LIB_SKIP]
            defines = {n: [k for k in ks if k.t not in LIB_SKIP] for n, ks in defines.items()}
            _LIBCACHE[key] = (objs, defines, materials, texmats)
        return copy.deepcopy(_LIBCACHE[key])
    except OSError:
        return None


def _mat_sig(m):
    return (m.texture, tuple(m.color), tuple(m.extra), tuple(ln for ln in m.raw if ln.split()[:1] != ['name']))


def _rename_material_in(objs, mapping):
    """Point objects at renamed materials (their own fields and the matref lines inside mesh data)."""
    for o in objs:
        if o.matref in mapping: o.matref = mapping[o.matref]
        for k, v in list(o.mats.items()):
            if v in mapping: o.mats[k] = mapping[v]
        if o.t == 'mesh' and o.mesh:
            body = []
            for ln in MESHDATA.get(o.mesh, []):
                p = ln.split()
                if len(p) > 1 and p[0].lower() == 'matref' and p[1] in mapping: ln = 'matref ' + mapping[p[1]]
                body.append(ln)
            o.mesh = mesh_register(body)


def _materials_used(objs, defines):
    """Names of every material the objects (and prefabs they use) refer to."""
    used, seen = set(), set()

    def scan(lst, depth=0):
        for o in lst:
            if o.matref: used.add(o.matref)
            used.update(v for v in o.mats.values() if v)
            if o.t == 'mesh':
                for ln in MESHDATA.get(o.mesh, []):
                    p = ln.split()
                    if len(p) > 1 and p[0].lower() == 'matref': used.add(p[1])
            if o.t == 'group' and o.name not in seen and depth <= MAX_NEST and o.name in defines:
                seen.add(o.name); scan(defines[o.name], depth + 1)
    scan(objs)
    return used


def _uses_in(objs, defines):
    """(materials, physics drivers) the objects use, following groups into the prefabs they place."""
    mats, phys, seen = set(), set(), set()

    def scan(lst, depth=0):
        for o in lst:
            if o.matref: mats.add(o.matref)
            mats.update(v for v in o.mats.values() if v)
            if o.phydrv: phys.add(o.phydrv)
            if o.t == 'mesh':
                for ln in MESHDATA.get(o.mesh, []):
                    p = ln.split()
                    if len(p) > 1 and p[0].lower() == 'matref': mats.add(p[1])
                    elif len(p) > 1 and p[0].lower() == 'phydrv': phys.add(p[1])
            if o.t == 'group' and o.name in defines and o.name not in seen and depth <= MAX_NEST:
                seen.add(o.name); scan(defines[o.name], depth + 1)
    scan(objs)
    return mats, phys


def library_text(name, category, description, objs, defines, materials, texmats=None):
    """The .bzw text of a library object: header, the materials it uses, the prefabs it needs, then the objects."""
    need, order = set(), []

    def visit(dn, depth=0):
        if dn in need or dn not in defines or depth > MAX_NEST: return
        need.add(dn)
        for k in defines[dn]:
            if k.t == 'group': visit(k.name, depth + 1)
        order.append(dn)
    for o in objs:
        if o.t == 'group': visit(o.name)
    used = _materials_used(objs, defines)
    out = [lib_header(name, category, description), '# Made with BZ Map Maker', '']
    wanted = {t for mn in used if mn in materials for t in _texmat_names(materials[mn])}
    for tn in sorted(wanted):  # a material's texturematrix has to be defined before it
        if tn in (texmats or {}): out += texmatrix_block(texmats[tn]) + ['']
    for mn in sorted(used):
        if mn in materials: out += material_block(materials[mn]) + ['']
    for dn in order:
        out.append('define %s' % dn)
        for k in defines[dn]: out += ['  ' + ln for ln in obj_block(k)]
        out += ['enddef', '']
    for o in objs: out += obj_block(o) + ['']
    return '\n'.join(out)


def objs_center(objs, defines):
    """(x, y) middle of the objects' real extent: meshes by their vertices, groups through their contents."""
    xs, ys = [], []
    for leaf, _ in expand(types.SimpleNamespace(objs=list(objs), defines=defines)):
        if leaf.t == 'mesh':
            W, _f = mesh_world(leaf); xs += [v[0] for v in W]; ys += [v[1] for v in W]
        else:
            xs += [leaf.x - leaf.sx, leaf.x + leaf.sx]; ys += [leaf.y - leaf.sy, leaf.y + leaf.sy]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2) if xs else (0.0, 0.0)


def library_path_for(name, directory=None):
    """(path, exists-for-this-name) where a person's object called `name` would be saved. A different
    object that already has that file name pushes this one to name_2.bzw rather than being overwritten."""
    directory = directory or user_library_dir()
    base = lib_slug(name); path, i = os.path.join(directory, base + '.bzw'), 1
    while os.path.exists(path):
        try:
            with open(path, encoding='utf-8', errors='replace') as fh: other = parse_lib_header(fh.read(3000)).get('name', '')
        except OSError: other = ''
        if other.lower() == name.lower(): return path, True
        i += 1; path = os.path.join(directory, '%s_%d.bzw' % (base, i))
    return path, False


def save_library_object(name, category, description, objs, defines, materials, texmats=None, directory=None):
    """Write objs (centred on the origin, map-only objects left out) into the person's library. Returns the path, or None."""
    objs = copy.deepcopy([o for o in objs if o.t not in LIB_SKIP])
    if not objs: return None
    cx, cy = objs_center(objs, defines)
    for o in objs:
        o.x -= cx; o.y -= cy; o.fam = o.fam_k = o.fold = 0; o.mode = ''
    directory = directory or user_library_dir()
    os.makedirs(directory, exist_ok=True)
    path, _ = library_path_for(name, directory)
    with open(path, 'w', encoding='utf-8') as fh: fh.write(library_text(name, category, description, objs, defines, materials, texmats or {}))
    _LIBCACHE.clear()
    return path


def import_into_library(src, name, category, description, split=False, directory=None):
    """Copy the objects (and the materials and prefabs they need) from another .bzw into the library.
    Returns the list of files written; [] if the file held nothing a library object can be made of."""
    with open(src, encoding='utf-8', errors='replace') as fh: text = fh.read()
    _nw, objs, materials, defines, _x, _o, _p, _w, texmats = parse(text)
    objs = [o for o in objs if o.t not in LIB_SKIP]
    if not objs: return []
    groups = [[o] for o in objs] if (split and len(objs) > 1) else [objs]
    out = []
    for i, grp in enumerate(groups, 1):
        label = name if len(groups) == 1 else '%s %d' % (name, i)
        p = save_library_object(label, category, description, grp, defines, materials, texmats, directory)
        if p: out.append(p)
    return out


class Model(QObject):
    changed = pyqtSignal(); selected = pyqtSignal(); reset = pyqtSignal(); warn = pyqtSignal(str)
    _tex_ready = pyqtSignal()  # a background download finished (emitted from the worker thread, handled on the main one)

    def __init__(s):
        super().__init__()
        if not hasattr(s, '_tex_done'):  # File > New runs __init__ again; that must not reset these or double-connect
            s.remote_textures = True  # View menu: fetch http(s) textures in the background; Win sets it from the saved settings
            s._tex_pending, s._tex_done = set(), queue.Queue()
            s._tex_ready.connect(s._collect_textures)
        s.W, s.objs, s.sel, s.msel, s.undo, s.path = 400, [], None, [], [], None
        s.materials, s.defines, s.texcache, s.extras = {}, {}, {}, []
        s.options_text = ''
        s.physics = {}
        s.texmats = {}  # Objects -> Texturematrix
        s.world = new_world()  # Objects -> World Settings
        s.lib_item = None  # the custom object the 'Custom object' tool places
        s.nid, s.sym = 1, 0
        s.tool, s.snap = 'select', True
        s.show_zones = True  # View → Show Zones; zones are editor-only aids and can clutter large maps

    def push(s):
        d = {'W': s.W, 'objs': [asdict(o) for o in s.objs],
             'materials': {k: asdict(v) for k, v in s.materials.items()},
             'defines': {k: [asdict(o) for o in v] for k, v in s.defines.items()}, 'extras': s.extras,
             'options_text': s.options_text, 'physics': {k: asdict(v) for k, v in s.physics.items()},
             'texmats': {k: asdict(v) for k, v in s.texmats.items()}, 'world': s.world}
        s.undo.append(json.dumps(d)); del s.undo[:-80]

    def pop(s):
        if not s.undo: return
        d = json.loads(s.undo.pop())
        s.W, s.objs, s.sel, s.msel = d['W'], [Obj(**o) for o in d['objs']], None, []
        s.materials = {k: Material(**v) for k, v in d['materials'].items()}
        s.defines = {k: [Obj(**o) for o in v] for k, v in d['defines'].items()}
        s.extras = d.get('extras', [])
        s.options_text = d.get('options_text', '')
        s.physics = {k: PhysicsDriver(**v) for k, v in d.get('physics', {}).items()}
        s.texmats = {k: TexMatrix(**v) for k, v in d.get('texmats', {}).items()}
        s.world = d.get('world') or new_world()
        s.nid = max([s.nid] + [o.uid + 1 for o in s.objs])
        s.selected.emit(); s.changed.emit()

    def reachable_defines(s):
        """Names of the prefabs that something in the map actually places (directly or through another prefab)."""
        out = set()

        def visit(name, depth=0):
            if name in out or name not in s.defines or depth > MAX_NEST: return
            out.add(name)
            for k in s.defines[name]:
                if k.t == 'group': visit(k.name, depth + 1)
        for o in s.objs:
            if o.t == 'group': visit(o.name)
        return out

    def cleanup_plan(s, everything=False):
        """What 'remove unused' would delete, as {'defines', 'materials', 'texmats', 'physics'} name lists.
        Always: group prefabs nothing places, plus the materials, texturematrix and physics drivers
        that only those prefabs used. With everything=True also anything else nothing uses at all (say, a
        material you made but haven't assigned yet). Anything an unrecognised block mentions by name is kept."""
        reach = s.reachable_defines(); dead = [n for n in s.defines if n not in reach]
        live_m, live_p = _uses_in(s.objs, s.defines); dead_m, dead_p = set(), set()
        for n in dead:
            m, p = _uses_in(s.defines[n], s.defines); dead_m |= m; dead_p |= p
        blob = '\n'.join(s.extras)
        named = lambda name: re.search(r'(?<![\w.-])%s(?![\w.-])' % re.escape(name), blob) is not None
        pick = lambda have, live, dead_used: [n for n in have if n not in live and not named(n) and (everything or n in dead_used)]
        mats, phys = pick(s.materials, live_m, dead_m), pick(s.physics, live_p, dead_p)
        kept = [m for n, m in s.materials.items() if n not in mats]
        ref_kept = {t for m in kept for t in _texmat_names(m)}; ref_all = {t for m in s.materials.values() for t in _texmat_names(m)}
        texmats = [n for n in s.texmats if n not in ref_kept and not named(n) and (everything or n in ref_all)]
        return {'defines': dead, 'materials': mats, 'texmats': texmats, 'physics': phys}

    def apply_cleanup(s, plan):
        """Delete what the plan lists (undoable). Anything still used by something that is being kept is left
        alone, so unticking a prefab in the dialog can't strand the materials it needs. Returns what was removed."""
        gone = {k: [] for k in ('defines', 'materials', 'texmats', 'physics')}
        want = {k: set(plan.get(k, ())) for k in gone}
        if not any(want.values()): return gone
        s.push()
        for n in want['defines']:
            if n in s.defines: del s.defines[n]; gone['defines'].append(n)
        live_m, live_p = _uses_in(s.objs, s.defines)
        for kids in s.defines.values():  # prefabs that stay, even unplaced ones, still need their things
            m, p = _uses_in(kids, s.defines); live_m |= m; live_p |= p
        for n in want['materials']:
            if n in s.materials and n not in live_m: del s.materials[n]; gone['materials'].append(n)
        for n in want['physics']:
            if n in s.physics and n not in live_p: del s.physics[n]; gone['physics'].append(n)
        ref = {t for m in s.materials.values() for t in _texmat_names(m)}
        for n in want['texmats']:
            if n in s.texmats and n not in ref: del s.texmats[n]; gone['texmats'].append(n)
        s.changed.emit()
        return gone

    def texture_file(s, texture):
        """Path of a texture's PNG next to the map (or in a 'textures' folder beside it), if there is one."""
        if not texture or '://' in texture or not s.path: return None
        base = os.path.dirname(s.path); name = texture if texture.lower().endswith('.png') else texture + '.png'
        for folder in (base, os.path.join(base, 'textures')):
            p = os.path.join(folder, name)
            if os.path.isfile(p): return p
        return None

    def texture_array(s, texture):
        """The tiny preview copy of a texture (or None), found the first time it is asked for. The cache is
        keyed by the texture's name, so it doesn't matter when or under what material name it was loaded, and
        it survives reloading the map text. A miss is remembered too, so a missing file isn't searched for
        on every redraw."""
        if not texture: return None
        if texture not in s.texcache:
            if is_remote_texture(texture):  # never block the picture on the network: draw without it until it arrives
                if s.remote_textures: s.fetch_remote(texture)
                return None
            p = s.texture_file(texture); img = QImage(p) if p else None
            s.texcache[texture] = micro_texture(img) if img is not None and not img.isNull() else None
        return s.texcache[texture]

    def fetch_remote(s, url):
        """Start downloading an internet texture on a background thread (once per URL). When it lands, the tiny
        preview copy goes into the cache and the views redraw. A failed download is remembered as a miss, so an
        offline or dead link isn't retried on every redraw; Objects > Materials > Fetch All URL Textures retries."""
        if url in s._tex_pending or url in s.texcache: return
        s._tex_pending.add(url)
        threading.Thread(target=s._fetch_worker, args=(url,), daemon=True).start()

    def prefetch_remote(s):
        """Start fetching every internet texture the map's materials use (called when a map is opened)."""
        if not s.remote_textures: return
        for url in sorted({m.texture for m in s.materials.values() if is_remote_texture(m.texture)}):
            s.fetch_remote(url)

    def _fetch_worker(s, url):
        arr = None
        try:
            img = QImage()
            if img.loadFromData(download_texture(url)):  # QImage (unlike QPixmap) may be used off the main thread
                arr = micro_texture(img)
        except Exception: pass
        s._tex_done.put((url, arr)); s._tex_ready.emit()

    def _collect_textures(s):
        got = False
        while True:
            try: url, arr = s._tex_done.get_nowait()
            except queue.Empty: break
            s._tex_pending.discard(url); s.texcache[url] = arr; got = True
        if got: s.changed.emit()

    def tele_names(s):
        """Every teleporter name in the map: top-level ones and ones inside group prefabs."""
        seen = [o.name for o in s.objs if o.t == 'teleporter']
        for kids in s.defines.values():
            seen += [k.name for k in kids if k.t == 'teleporter']
        return list(dict.fromkeys(seen))

    def free_tele_name(s, extra=()):
        taken = set(s.tele_names()) | {z.name for z in extra}; j = 1
        while 't%d' % j in taken: j += 1
        return 't%d' % j

    def _scope_names(s, lst):
        """Unique instance names for the group instances in lst (the map's top level, or the
        contents of one prefab). BZFlag names a teleporter inside a group instance
        '<instance>:<teleporter>', and nested instances chain: '<outer>:<inner>:<teleporter>'."""
        out, used = {}, set()
        for g in lst:
            if g.t != 'group': continue
            base = g.iname or g.name or 'group'; n, i = base, 1
            while n in used: i += 1; n = '%s_%d' % (base, i)
            used.add(n); out[id(g)] = n
        return out

    def instance_names(s):
        return s._scope_names(s.objs)

    def group_has_tele(s, name, depth=0):
        if depth > MAX_NEST: return False
        return any((k.t == 'teleporter' and k.name) or (k.t == 'group' and s.group_has_tele(k.name, depth + 1))
                   for k in s.defines.get(name, []))

    def tele_placements(s):
        """[(instance-name chain, teleporter)] for every teleporter that will exist in the world,
        top level ones with an empty chain."""
        res, cache = [], {}

        def walk(lst, prefix, depth):
            nm = cache.setdefault(id(lst), s._scope_names(lst))
            for o in lst:
                if o.t == 'teleporter':
                    if o.name: res.append((prefix, o))
                elif o.t == 'group' and depth < MAX_NEST:
                    walk(s.defines.get(o.name, []), prefix + (nm[id(o)],), depth + 1)
        walk(s.objs, (), 0)
        return res

    def define_contains(s, name, target, depth=0):
        """True if prefab `name` is `target`, or holds (at any depth) an instance of it."""
        if name == target: return True
        if depth > MAX_NEST: return True  # a loop already: treat as contained so nothing new is added to it
        return any(k.t == 'group' and s.define_contains(k.name, target, depth + 1) for k in s.defines.get(name, []))

    def ordered_defines(s):
        """Prefabs with the ones other prefabs use written first."""
        out, seen = [], set()

        def visit(name, depth=0):
            if name in seen or name not in s.defines or depth > MAX_NEST: return
            seen.add(name)
            for k in s.defines[name]:
                if k.t == 'group': visit(k.name, depth + 1)
            out.append(name)
        for name in list(s.defines): visit(name)
        return out

    def place_library(s, item, x, y):
        """Add a custom object to the map as a group instance at (x, y). Its materials come along
        (renamed if the map already has a different one of that name) and its prefabs are added
        once, so placing it again shares them. Returns the group, or None."""
        got = load_library_content(item.path)
        if not got or not got[0]: return None
        objs, defines, materials, texmats = got
        slug = lib_slug(item.name); rename = {}; tmap = {}
        for name, tm in texmats.items():
            have = s.texmats.get(name)
            if have is None: s.texmats[name] = tm; continue
            if _tm_sig(have) == _tm_sig(tm): continue  # the map already has this one
            n, i = name, 1
            while n in s.texmats or n in texmats: i += 1; n = '%s_%d' % (name, i)
            tm.name = n; tm.raw = [('name ' + n) if ln.split()[:1] == ['name'] else ln for ln in tm.raw]
            s.texmats[n] = tm; tmap[name] = n
        if tmap:
            for mat in materials.values(): _retarget_texmat(mat, tmap)
        for name, mat in materials.items():
            have = s.materials.get(name)
            if have is None: s.materials[name] = mat; continue
            if _mat_sig(have) == _mat_sig(mat): continue  # same material already in the map
            n, i = name, 1
            while n in s.materials or n in materials: i += 1; n = '%s_%d' % (name, i)
            mat.name = n; mat.raw = [('name ' + n) if ln.split()[:1] == ['name'] else ln for ln in mat.raw]
            s.materials[n] = mat; rename[name] = n
        sig = lambda kids: '\n'.join('\n'.join(obj_block(k)) for k in kids)
        dmap = {}

        def bring(dn, depth=0):
            if dn in dmap: return dmap[dn]
            if dn not in defines or depth > MAX_NEST: return dn
            dmap[dn] = dn  # a prefab that (wrongly) contains itself stops here
            kids = defines[dn]; _rename_material_in(kids, rename)
            for k in kids:
                if k.t == 'group': k.name = bring(k.name, depth + 1)
            base = n = '%s__%s' % (slug, dn); i = 1
            while n in s.defines and sig(s.defines[n]) != sig(kids): i += 1; n = '%s_%d' % (base, i)
            s.defines.setdefault(n, kids); dmap[dn] = n
            return n
        _rename_material_in(objs, rename)
        for o in objs:
            if o.t == 'group': o.name = bring(o.name)
        if len(objs) == 1 and objs[0].t == 'group':
            g = objs[0]; g.x += x; g.y += y
        else:
            base = n = slug; i = 1
            while n in s.defines and sig(s.defines[n]) != sig(objs): i += 1; n = '%s_%d' % (base, i)
            s.defines.setdefault(n, objs)
            g = Obj('group', x, y, 0, 1, 1, 1, 0, 0, n)
        g.iname = ''; g.fam = g.fam_k = g.fold = 0; g.mode = ''
        g.uid = s.nid; s.nid += 1
        s.objs.append(g)
        return g

    def add(s, t, x, y):
        if t == 'library': return s.place_library(s.lib_item, x, y) if s.lib_item else None
        o = Obj(t, x, y, 0, *DEF[t]); o.uid = s.nid; s.nid += 1
        if t == 'teleporter':
            o.name = s.free_tele_name()
        s.objs.append(o); return o

    def select(s, o):
        s.sel = o; s.selected.emit()

    def toggle_msel(s, o):
        # When starting a multi-select, keep the previously selected object in the set
        # so Ctrl+clicking the second object doesn't drop the first one.
        if not s.msel and s.sel and s.sel is not o:
            s.msel.append(s.sel)
        if o in s.msel: s.msel.remove(o)
        else: s.msel.append(o)
        s.select(o)

    def select_in_rect(s, x0, y0, x1, y1, add=False):
        """Box select: every top-level object whose center lies inside the rectangle joins the
        multi-selection (the same set Ctrl+click builds), ready for Ctrl+G. add=True keeps what
        was already selected."""
        lx, hx, ly, hy = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        keep = (list(s.msel) or ([s.sel] if s.sel else [])) if add else []
        new = keep + [o for o in s.objs if lx <= o.x <= hx and ly <= o.y <= hy and o not in keep]
        s.msel = new
        s.select(new[-1] if new else None)
        return len(new)

    def group_bounds(s, name):
        leaves = expand_group(s, Obj('group', 0, 0, 0, 1, 1, 1, 0, 0, name))
        if not leaves: return 10, 10
        xs, ys = [5], [5]
        for k in leaves:
            if k.t == 'mesh':
                W, _ = mesh_world(k); xs += [abs(v[0]) for v in W]; ys += [abs(v[1]) for v in W]
            else:
                xs.append(abs(k.x) + k.sx); ys.append(abs(k.y) + k.sy)
        return max(xs), max(ys)

    # ---- symmetry families ---------------------------------------------------
    def make_family(s, o):
        if o is None or not s.sym or o.fam or o.t == 'mesh': return
        mode, fold = SYM_DEFS[s.sym]
        s.nid += 0
        fam = 'f%d' % o.uid
        o.fam, o.fam_k, o.fold, o.mode = fam, 0, fold, mode
        created = [o]
        for k in range(1, fold):
            q = replace(o); q.uid = s.nid; s.nid += 1; q.fam_k = k; q.link = q.blink = ''
            if q.t == 'teleporter':
                q.name = s.free_tele_name(created)
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
            q.flipz, q.phys, q.zflags = o.flipz, o.phys, o.zflags
            # a zone's team/safety lists swap or cycle the same way a base's team does, per
            # entry (team 0/rogue is left alone, same rule _team_fwd already applies to bases)
            z0team = [_team_inv(o.fam_k, t, mode, fold) for t in o.zteam]
            q.zteam = sorted(set(_team_fwd(q.fam_k, t, mode, fold) for t in z0team))
            z0safe = [_team_inv(o.fam_k, t, mode, fold) for t in o.zsafety]
            q.zsafety = sorted(set(_team_fwd(q.fam_k, t, mode, fold) for t in z0safe))
            for f in ('z', 'sx', 'sy', 'sz', 'angle', 'ratio', 'divisions'):
                setattr(q, f, getattr(o, f))
            # matref, mats, phydrv, tint and ricochet are deliberately NOT copied here: each
            # symmetry copy keeps its own material, physics driver, tint and ricochet, so a
            # red-team copy can be red and push +X while a green-team copy is green and
            # pushes -X, without one overwriting the other. See match_team_color() and
            # mirror_physics_for_family() for ways to set those per-copy values quickly.
            if o.t in ('base', 'group'):  # team means something for these only -- everything
                q.team = _team_fwd(q.fam_k, team0, mode, fold)  # else keeps its untouched default
        if o.t == 'teleporter' and o.mode in ('mirror_lr', 'mirror_tb'):
            for q in fam:
                if q is not o: q.r = (o.r + 180) % 360  # forces front/back to line up on a mirror

    def _relink(s, ordered):
        n = len(ordered)
        for i, m in enumerate(ordered):
            m.link = ordered[(i + 1) % n].name; m.blink = ordered[(i - 1) % n].name
            m.lface, m.bface = 'b', 'f'

    def family_of(s, o):
        return [q for q in s.objs if o.fam and q.fam == o.fam]

    def match_team_color(s, o):
        """Tint this one symmetry copy to the team color its position in the rotation
        corresponds to (red/green/blue/purple) -- a quick way to color-code a symmetric set
        by team without having to pick the RGB values yourself. Only meaningful for rot
        fold>=3, where each sibling already maps to a distinct team the way a base's team
        does; for 2-fold/mirror modes team pairing is red<->green or blue<->purple, so this
        uses whichever of those the sibling's position swaps to."""
        if not o.fam: return
        team = o.fam_k + 1 if (o.mode == 'rot' and o.fold >= 3) else (1 if o.fam_k == 0 else 2)
        team = min(4, max(1, team))
        c = ZONE_TEAM_COLORS[team]
        o.tint = (int(c[1:3], 16) / 255, int(c[3:5], 16) / 255, int(c[5:7], 16) / 255, 1.0)

    def mirror_physics_for_family(s, o):
        """Give every other sibling its own physics driver, derived from this one's, with
        the push direction rotated/mirrored to match that sibling's own orientation -- so a
        +X launch on one copy becomes a correctly mirrored -X (or rotated) launch on the
        others, instead of every copy pushing the exact same absolute direction."""
        if not o.fam or not o.phydrv or o.phydrv not in s.physics: return
        src = s.physics[o.phydrv]  # o's own CURRENT driver -- the one we recover "canonical" from
        base_name = src.name.rsplit('_f', 1)[0] if '_f' in src.name else src.name
        v0 = _inv_vec(o.fam_k, src.linear, o.mode, o.fold) if src.has_linear else (0, 0, 0)
        for q in s.family_of(o):
            if q is o: continue  # never touches o's own driver, or any driver used elsewhere on the map
            pd = replace(src)
            pd.name = '%s_f%d' % (base_name, q.fam_k)
            if src.has_linear: pd.linear = _xform_vec(q.fam_k, v0, o.mode, o.fold)
            s.physics[pd.name] = pd
            q.phydrv = pd.name

    def delete(s):
        if not s.sel: return
        s.push()
        dead = s.family_of(s.sel) or [s.sel]
        names = {d.name for d in dead if d.name}
        s.objs = [o for o in s.objs if o not in dead]
        for o in s.objs:
            if o.link in names: o.link = ''
            if o.blink in names: o.blink = ''
        for kids in s.defines.values():
            for k in kids:
                if k.link in names: k.link = ''
                if k.blink in names: k.blink = ''
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
            c.name = s.free_tele_name()
        s.objs.append(c); s.make_family(c); s.select(c); s.changed.emit()

    def make_group(s, objs, name_hint='group'):
        """Wrap the given objects (at least one) into a new prefab ('define') plus one
        instance ('group') placed at their centroid -- BZFlag's group only stores a shift
        and rotation, so the prefab's own contents are stored relative to that midpoint.
        Does not touch s.objs or undo; callers decide what to do with the result."""
        if not objs: return None
        cx = sum(o.x for o in objs) / len(objs); cy = sum(o.y for o in objs) / len(objs)
        kids = []
        for o in objs:
            k = replace(o); k.x -= cx; k.y -= cy; k.fam = k.fam_k = k.fold = 0; k.mode = ''
            kids.append(k)
        name, i = name_hint, 1
        while name in s.defines: i += 1; name = '%s%d' % (name_hint, i)
        s.defines[name] = kids
        g = Obj('group', cx, cy, 0, 1, 1, 1, 0, 0, name); g.uid = s.nid; s.nid += 1
        return g

    def add_to_group(s, grp, obj):
        """Move an existing top-level object (a group instance too: groups nest) into an existing
        group's prefab, converting it into the group's local frame (undoing the group's own scale,
        rotation and shift) so it doesn't visually move. Returns False if that isn't possible,
        including when it would make a prefab contain itself."""
        if grp.t != 'group' or obj is grp or obj not in s.objs: return False
        if obj.t == 'group' and s.define_contains(obj.name, grp.name): return False
        a = math.radians(-grp.r); c, sn = math.cos(a), math.sin(a)
        dx, dy = obj.x - grp.x, obj.y - grp.y
        kid = replace(obj)
        kid.x, kid.y, kid.z = (dx * c - dy * sn) / (grp.sx or 1), (dx * sn + dy * c) / (grp.sy or 1), (obj.z - grp.z) / (grp.sz or 1)
        kid.r = (obj.r - grp.r) % 360
        if obj.t == 'group': kid.sx, kid.sy, kid.sz = obj.sx / (grp.sx or 1), obj.sy / (grp.sy or 1), obj.sz / (grp.sz or 1)
        elif obj.t == 'mesh': pass
        else:
            t = math.radians(kid.r); ct, st = math.cos(t), math.sin(t)
            kid.sx = obj.sx / (math.hypot((grp.sx or 1) * ct, (grp.sy or 1) * st) or 1)
            kid.sy = obj.sy / (math.hypot((grp.sx or 1) * st, (grp.sy or 1) * ct) or 1)
            kid.sz = obj.sz / (grp.sz or 1)
        kid.fam = kid.fam_k = kid.fold = 0; kid.mode = ''
        s.defines.setdefault(grp.name, []).append(kid)
        s.objs.remove(obj)
        return True

    def group_selection(s):
        """Ctrl+G. If exactly one of the selected objects is a group and others are selected too,
        the others are added into it. Otherwise everything selected (groups included, since groups
        nest) is wrapped into a single new prefab + group instance at its centroid."""
        sel = s.msel or ([s.sel] if s.sel else [])
        groups = [o for o in sel if o.t == 'group']; plain = [o for o in sel if o.t != 'group']
        if not sel: return None
        s.push()
        if len(groups) == 1 and plain:
            g = groups[0]
            for o in plain: s.add_to_group(g, o)
            s.msel = []; s.select(g); s.changed.emit()
            return g.name
        if len(sel) < 2 and not plain: return None
        g = s.make_group(sel)
        for o in sel:
            s.objs.remove(o)
        s.objs.append(g)
        s.msel = []; s.select(g); s.changed.emit()
        return g.name

    def load(s, text, folder=None):
        nw, objs, materials, defines, extras, options_text, physics, world, texmats = parse(text)
        s.push(); s.W = nw or s.W; s.world = world; s.objs = objs; s.materials = materials; s.defines = defines
        s.extras = extras; s.options_text = options_text; s.physics = physics; s.texmats = texmats
        s.sel = None; s.msel = []
        for o in s.objs: o.uid = s.nid; s.nid += 1
        s.reset.emit(); s.selected.emit(); s.changed.emit()

    def text(s):
        out = ['# Made with BZ Map Maker'] + world_block(s.world, s.W) + ['']
        if s.options_text.strip():
            out += ['options'] + ['  ' + ln for ln in s.options_text.splitlines() if ln.strip()] + ['end', '']
        for pd in s.physics.values(): out += physics_block(pd) + ['']
        for tm in s.texmats.values(): out += texmatrix_block(tm) + ['']  # before the materials that use them
        for c in s.extras: out += [c, '']  # physics, dynamic colors... they may be referenced below
        for m in s.materials.values(): out += material_block(m) + ['']
        for name in s.ordered_defines():
            out.append('define %s' % name)
            nm = s._scope_names(s.defines[name])
            for kd in s.defines[name]:
                blk = obj_block(kd)
                if kd.t == 'group' and s.group_has_tele(kd.name): blk.insert(1, '  name %s' % nm[id(kd)])
                out += ['  ' + ln for ln in blk]
            out += ['enddef', '']
        eff = s.instance_names()
        for o in s.objs:
            if o.fam:  # a plain, harmless comment to everything else that reads this file --
                       # only this editor looks for it, to restore symmetry linkage on reopen
                out.append('#bzmm-sym fam=%s k=%d fold=%d mode=%s' % (o.fam, o.fam_k, o.fold, o.mode))
            blk = obj_block(o)
            if o.t == 'group' and s.group_has_tele(o.name): blk.insert(1, '  name %s' % eff[id(o)])  # names its teleporters for links
            out += blk + ['']
        pls = s.tele_placements()
        full_of = lambda prefix, k: ':'.join(prefix + (k.name,))

        def full(tgt, prefix):
            """BZW name of the teleporter called tgt, as seen from the instance chain `prefix`: the
            nearest one wins; if several instances tie, a wildcard lets BZFlag pick one."""
            cands = [(p, k) for p, k in pls if k.name == tgt]
            if not cands: return tgt
            def shared(p):
                n = 0
                for a, c in zip(p, prefix):
                    if a != c: break
                    n += 1
                return n
            best = max(shared(p) for p, _ in cands); near = [c for c in cands if shared(c[0]) == best]
            if len(near) == 1: return full_of(*near[0])
            return tgt if all(not p for p, _ in near) else '*:' + tgt

        for prefix, k in pls:
            for face, tgt, other in (('f', k.link, k.lface), ('b', k.blink, k.bface)):
                if tgt: out.extend(['link', '  from %s:%s' % (full_of(prefix, k), face),
                                    '  to %s:%s' % (full(tgt, prefix), other), 'end', ''])
        for f, t in s.world.get('rawlinks', []): out.extend(['link', '  from %s' % f, '  to %s' % t, 'end', ''])
        return '\n'.join(out)


MAX_NEST = 12  # deepest group-in-group chain followed (also stops a group that contains itself)


def shape_turn(o):
    """How far an object's whole outline is turned about its centre. For most objects that is its `rotation`. In BZFlag an
    arc's `rotation` does not turn its outline: the arc is an ellipse lined up with the world axes (X size by Y size) and
    `rotation` only moves the angle its first corner (and the start of a partial sweep) sits at. Only the enclosing groups
    turn the ellipse itself, and that part is kept in `trot` (see _place)."""
    return o.trot if o.t == 'arc' else o.r


def arc_phase(o):
    """The angle (degrees, counter-clockwise) at which an arc's first corner / sweep start sits on its ellipse."""
    return o.r - o.trot


def _place(kd, g):
    """kd (an object inside group instance g's prefab) placed into g's parent frame.
    A group's transform is scale, then rotation about Z, then shift. A non-uniform scale on a
    rotated child can't be a plain box, so child sizes are scaled along the child's own axes."""
    a = math.radians(g.r); c, sn = math.cos(a), math.sin(a)
    px, py, pz = kd.x * g.sx, kd.y * g.sy, kd.z * g.sz
    leaf = replace(kd)
    leaf.x, leaf.y, leaf.z = g.x + px * c - py * sn, g.y + px * sn + py * c, g.z + pz
    leaf.r = (kd.r + g.r) % 360
    if kd.t == 'arc': leaf.trot = (kd.trot + g.r) % 360
    if kd.t == 'mesh':  # a mesh keeps its own geometry; the chain's scale and shift go on the leaf
        leaf.sx = leaf.sy = leaf.sz = 0
        leaf.mscale = (g.sx * kd.mscale[0], g.sy * kd.mscale[1], g.sz * kd.mscale[2])
    elif kd.t == 'group':  # a nested instance: compose the two transforms (exact unless g scales X/Y unequally)
        leaf.sx, leaf.sy, leaf.sz = kd.sx * g.sx, kd.sy * g.sy, kd.sz * g.sz
        if g.team: leaf.team = g.team  # the outer group's team override wins
    else:
        t = math.radians(kd.r); ct, st = math.cos(t), math.sin(t)
        leaf.sx = kd.sx * math.hypot(g.sx * ct, g.sy * st)
        leaf.sy = kd.sy * math.hypot(g.sx * st, g.sy * ct)
        leaf.sz = kd.sz * g.sz
        if leaf.t == 'base' and g.team: leaf.team = g.team
    return leaf


def expand_group(model, g, depth=0):
    """Every plain object inside group instance g (nested groups followed), in g's parent frame."""
    out = []
    if depth > MAX_NEST: return out
    for kd in model.defines.get(g.name, []):
        leaf = _place(kd, g)
        if kd.t == 'group': out += expand_group(model, leaf, depth + 1)
        else: out.append(leaf)
    return out


def expand(model):
    """Yield (leaf, source) pairs: for a plain object, leaf is o itself; for a group instance,
    one leaf per object in its prefab (and in any groups nested inside it), transformed into
    world space. Groups may contain other groups; a define may not contain a define."""
    out = []
    for o in model.objs:
        if o.t == 'group':
            out += [(leaf, o) for leaf in expand_group(model, o)]
        else:
            out.append((o, o))
    return out


# ---------------------------------------------------------------------------
# 2D layout view
# ---------------------------------------------------------------------------
def cpen(color, w=1, style=Qt.PenStyle.SolidLine):
    p = QPen(QColor(color), w, style); p.setCosmetic(True); return p


HINT = {'select': 'Click to select and drag to move. Ctrl+click to multi-select for grouping, or Shift+drag a box '
                   '(Ctrl+Shift+drag adds to the selection), then Ctrl+G. '
                   'Drag empty space or use the right mouse button to pan; scroll to zoom.',
        'library': 'Click to place the chosen custom object as a group (choose another from Objects > Custom Objects).',
        'box': 'Drag corner to corner, or click for a default box.',
        'pyramid': 'Drag corner to corner, or click for a default pyramid.',
        'base': 'Click to place a base, then set its team in the panel.',
        'teleporter': 'Click to place a teleporter, then set its links in the panel.',
        'arc': 'Drag corner to corner, or click for a default arc. Set sweep angle and hollow ratio in the panel.',
        'cone': 'Drag corner to corner, or click for a default cone.',
        'zone': 'Drag corner to corner, or click for a default zone. Set spawn teams, safety teams and '
                'flags in the panel -- zones are invisible in the real game, shown here as a checkered '
                'aid colored by which teams spawn there.',
        'weapon': 'Click to place a world weapon. Rotation sets its aim direction, Tilt its vertical aim; '
                  'set what it fires and how often in the panel. Shown here as an arrow, since it has no '
                  'appearance in the real game either.'}


class Editor(QWidget):
    def __init__(s, m):
        super().__init__()
        s.m, s.v, s.drag, s.space, s.fitted = m, [0, 0, 1.0], None, False, False
        s.setFocusPolicy(Qt.FocusPolicy.StrongFocus); s.setMinimumSize(320, 240)
        m.changed.connect(s.update); m.selected.connect(s.update); m.reset.connect(s.fit)

    def fit(s):
        s.v = [0, 0, min(s.width(), s.height()) / (s.m.W * 2.2)]; s.update()

    def showEvent(s, e):
        if not s.fitted: s.fitted = True; s.fit()

    def sn(s, v): return round(v / 5) * 5 if s.m.snap else v

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
            elif leaf.t == 'weapon': hx, hy = 14, 6  # matches the arrow marker's drawn extent
            a = math.radians(shape_turn(leaf)); dx, dy = wx - leaf.x, wy - leaf.y
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
        p.save(); p.translate(leaf.x, leaf.y); p.rotate(shape_turn(leaf))  # the view is y-up, so +rotation is counter-clockwise, same as the 3D view
        if leaf.t == 'group':
            hx, hy = s.m.group_bounds(leaf.name)
            p.setPen(cpen('#98a0a8', 1, Qt.PenStyle.DashLine)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(-hx, -hy, 2 * hx, 2 * hy))
        elif leaf.t in ('arc', 'cone'):
            path = QPainterPath()
            if leaf.t == 'cone' or leaf.angle >= 360:
                path.addEllipse(QRectF(-leaf.sx, -leaf.sy, 2 * leaf.sx, 2 * leaf.sy))
            else:
                ph = arc_phase(leaf) if leaf.t == 'arc' else 0.0  # same points as the 3D view, so the sweep starts where bzfs starts it
                path.moveTo(0, 0)
                for i in range(65):
                    a = math.radians(ph + leaf.angle * i / 64); path.lineTo(leaf.sx * math.cos(a), leaf.sy * math.sin(a))
                path.closeSubpath()
            c = QColor(COL[leaf.t]); p.setPen(cpen('#22262b')); p.setBrush(c); p.drawPath(path)
            if leaf.t == 'arc' and leaf.ratio:
                ir = 1 - leaf.ratio
                p.setBrush(QColor('#f6f5f0')); p.drawEllipse(QRectF(-leaf.sx * ir, -leaf.sy * ir, 2 * leaf.sx * ir, 2 * leaf.sy * ir))
            if leaf.t == 'cone':
                p.setPen(cpen('#22262b')); p.drawEllipse(QRectF(-leaf.sx * .12, -leaf.sy * .12, leaf.sx * .24, leaf.sy * .24))
        elif leaf.t == 'zone':
            # Zones are invisible in the actual game -- this checkerboard is an editor aid only,
            # one color per spawning team (0=rogue=black), so you can see coverage at a glance.
            if not s.m.show_zones:
                pass  # View → Show Zones toggles this off so zones stop obscuring the map
            else:
                colors = zone_colors(leaf)
                nx, ny, cw, ch = zone_grid(leaf.sx, leaf.sy)
                p.setPen(Qt.PenStyle.NoPen)
                for ix in range(nx):
                    for iy in range(ny):
                        col = QColor(colors[(ix + iy) % len(colors)]); col.setAlpha(100)
                        p.setBrush(col); p.drawRect(QRectF(-leaf.sx + ix * cw, -leaf.sy + iy * ch, cw, ch))
                p.setPen(cpen('#22262b', 1, Qt.PenStyle.DashLine)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(-leaf.sx, -leaf.sy, 2 * leaf.sx, 2 * leaf.sy))
        elif leaf.t == 'weapon':
            # Weapons have no appearance in the real game either -- an arrow along the
            # rotation (already applied above via p.rotate) shows aim direction at a glance.
            c = QColor(TEAM[leaf.wcolor] if 1 <= leaf.wcolor <= 4 else COL['weapon'])
            p.setPen(cpen(c, 2.5)); p.drawLine(QPointF(0, 0), QPointF(14, 0))
            p.drawLine(QPointF(14, 0), QPointF(9, 4)); p.drawLine(QPointF(14, 0), QPointF(9, -4))
            p.setBrush(c); p.drawEllipse(QPointF(0, 0), 3, 3)
        else:
            a, b = max(leaf.sx, 1.2 / S if leaf.t == 'teleporter' else 0), leaf.sy
            c = QColor(TEAM[leaf.team] if leaf.t == 'base' else COL[leaf.t])
            if leaf.t == 'base': c.setAlpha(140)
            p.setPen(cpen('#22262b')); p.setBrush(c); p.drawRect(QRectF(-a, -b, 2 * a, 2 * b))
            if leaf.t == 'pyramid':
                p.drawLine(QPointF(-a, -b), QPointF(a, b)); p.drawLine(QPointF(a, -b), QPointF(-a, b))
                if leaf.flipz:  # small dot marks a flipped (point-down) pyramid
                    p.setBrush(QColor('#22262b')); p.drawEllipse(QPointF(0, 0), min(a, b) * .18, min(a, b) * .18)
        if sel:
            pad = 3 / S
            hx = s.m.group_bounds(leaf.name)[0] if leaf.t == 'group' else (14 if leaf.t == 'weapon' else max(leaf.sx, 1))
            hy = s.m.group_bounds(leaf.name)[1] if leaf.t == 'group' else (6 if leaf.t == 'weapon' else leaf.sy)
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
                    if q is leaf or (q and q.x == leaf.x and q.y == leaf.y and q.name == leaf.name):
                        p.save(); p.setBrush(Qt.BrushStyle.NoBrush)  # self-link: a ring round the teleporter
                        rr = max(leaf.sy, 3 / S) + 5 / S; p.drawEllipse(QPointF(leaf.x, leaf.y), rr, rr); p.restore()
                    elif q: p.drawLine(QPointF(leaf.x, leaf.y), QPointF(q.x, q.y))
        for leaf, src in expand(s.m):
            twin = src is not s.m.sel and s.m.sel and src.fam and src.fam == s.m.sel.fam
            s.draw_leaf(p, leaf, src is s.m.sel)
            if twin:
                p.save(); p.translate(leaf.x, leaf.y); p.rotate(shape_turn(leaf))  # the view is y-up, so +rotation is counter-clockwise, same as the 3D view
                pad = 3 / S; a, b = max(leaf.sx, 1), leaf.sy
                p.setPen(cpen(ACC, 1.5, Qt.PenStyle.DotLine)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(-a - pad, -b - pad, 2 * a + 2 * pad, 2 * b + 2 * pad)); p.restore()
            if src in s.m.msel:
                p.save(); p.translate(leaf.x, leaf.y)
                p.setPen(cpen('#c8453b', 2)); p.drawEllipse(QPointF(0, 0), 3 / S, 3 / S); p.restore()
        for leaf, src in expand(s.m):  # launch-pad landing points, top-down (no arc shape needed from above)
            if not leaf.phydrv: continue
            pd = s.m.physics.get(leaf.phydrv)
            if not pd or not pd.has_linear or pd.linear[2] <= 0: continue
            lx, ly = leaf.x + pd.linear[0] * (2 * pd.linear[2] / GRAVITY), leaf.y + pd.linear[1] * (2 * pd.linear[2] / GRAVITY)
            p.setPen(cpen('#e0b84f', 1.5, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(leaf.x, leaf.y), QPointF(lx, ly))
            p.setPen(cpen('#e0b84f', 2)); p.setBrush(QColor(224, 184, 79, 90))
            p.drawEllipse(QPointF(lx, ly), 4 / S, 4 / S)
        d = s.drag
        if d and d[0] == 'marquee':
            (ax, ay), (bx, by) = d[1], d[2]; p.setPen(cpen('#c8453b', 1.5, Qt.PenStyle.DashLine)); p.setBrush(QColor(200, 69, 59, 40))
            p.drawRect(QRectF(min(ax, bx), min(ay, by), abs(ax - bx), abs(ay - by)))
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
        if s.m.tool == 'select' and e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Shift+drag = box select (Ctrl+Shift+drag adds to the current selection)
            s.drag = ['marquee', (wx, wy), (wx, wy), bool(ctrl)]; return
        if s.m.tool == 'select':
            o = s.hit(wx, wy)
            if ctrl and o:
                m.toggle_msel(o); return
            if not ctrl: m.msel = []
            m.select(o)
            if o: m.push(); s.drag = ('move', o, o.x - wx, o.y - wy)
            else: s.drag = pan
        elif s.m.tool in ('box', 'pyramid', 'arc', 'cone', 'zone'):
            a = (s.sn(wx), s.sn(wy)); s.drag = ['make', a, a, p.x(), p.y()]
        else:
            m.push(); o = m.add(s.m.tool, s.sn(wx), s.sn(wy)); m.make_family(o); m.select(o); m.changed.emit()

    def mouseMoveEvent(s, e):
        d = s.drag
        if not d: return
        p = e.position(); wx, wy = s.tow(p)
        if d[0] == 'pan': s.v[0] = d[3] + p.x() - d[1]; s.v[1] = d[4] + p.y() - d[2]; s.update()
        elif d[0] == 'move':
            o = d[1]
            o.x, o.y = s.sn(wx + d[2]), s.sn(wy + d[3])
            if s.m.sym and not o.fam:
                # a plain object (built while symmetry was off) picks up a symmetry family the
                # moment you actually drag it with symmetry on -- not on a mere click/select
                s.m.make_family(o)
            s.m.sync_family(o); s.m.changed.emit()
        elif d[0] == 'marquee': d[2] = (wx, wy); s.update()
        else: d[2] = (s.sn(wx), s.sn(wy)); s.update()

    def mouseReleaseEvent(s, e):
        d = s.drag; s.drag = None
        if d and d[0] == 'marquee':
            s.m.select_in_rect(d[1][0], d[1][1], d[2][0], d[2][1], add=d[3])
        if d and d[0] == 'make':
            (ax, ay), (bx, by) = d[1], d[2]; p = e.position()
            s.m.push(); o = s.m.add(s.m.tool, ax, ay)
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
def screen_ray(B, sx, sy, w, h):
    """Inverse of Preview.proj(): turn a screen pixel back into a world-space ray
    (origin, direction) using the same camera basis the 3D view is drawn with."""
    pos, f, r, u = B
    k = h * .9  # matches the k = height*.9/zc used to project points; zc cancels out of the direction
    rx = (sx - w / 2) / k; ry = -(sy - h / 2) / k
    d = [f[i] + rx * r[i] + ry * u[i] for i in range(3)]
    n = math.sqrt(sum(c * c for c in d)) or 1e-9
    return pos, [c / n for c in d]


def ray_ground(origin, direction, z=0.0):
    """Where a ray crosses the world's flat ground plane, or None if it never does."""
    if abs(direction[2]) < 1e-9: return None
    t = (z - origin[2]) / direction[2]
    if t < 0: return None
    return origin[0] + t * direction[0], origin[1] + t * direction[1], t


def ray_box_hit(origin, direction, x, y, z, sx, sy, sz, r):
    """Nearest ray/axis-rotated-box intersection distance, or None. Used to click-select
    objects in the 3D view; the box is the same footprint x height used for 2D hit-testing."""
    a = math.radians(-r); c, sn = math.cos(a), math.sin(a)
    ox, oy = origin[0] - x, origin[1] - y
    lo = (ox * c - oy * sn, ox * sn + oy * c, origin[2] - z)
    dx, dy = direction[0], direction[1]
    ld = (dx * c - dy * sn, dx * sn + dy * c, direction[2])
    bmin = (-max(sx, 1), -max(sy, 1), 0); bmax = (max(sx, 1), max(sy, 1), max(sz, .3))
    tmin, tmax = -1e18, 1e18
    for i in range(3):
        if abs(ld[i]) < 1e-9:
            if lo[i] < bmin[i] or lo[i] > bmax[i]: return None
        else:
            t1, t2 = (bmin[i] - lo[i]) / ld[i], (bmax[i] - lo[i]) / ld[i]
            if t1 > t2: t1, t2 = t2, t1
            tmin, tmax = max(tmin, t1), min(tmax, t2)
            if tmin > tmax: return None
    return None if tmax < 0 else max(tmin, 0)


def ray_aabb_hit(origin, direction, bmin, bmax):
    """Same as ray_box_hit but axis-aligned, with explicit world-space bounds -- used for
    meshes, which (unlike every other object type) aren't a simple rotated box."""
    tmin, tmax = -1e18, 1e18
    for i in range(3):
        if abs(direction[i]) < 1e-9:
            if origin[i] < bmin[i] or origin[i] > bmax[i]: return None
        else:
            t1, t2 = (bmin[i] - origin[i]) / direction[i], (bmax[i] - origin[i]) / direction[i]
            if t1 > t2: t1, t2 = t2, t1
            tmin, tmax = max(tmin, t1), min(tmax, t2)
            if tmin > tmax: return None
    return None if tmax < 0 else max(tmin, 0)


GRAVITY = 9.81  # matches the acceleration BZFlag's own documentation uses for its trampoline example


def launch_trajectory(start, linear, steps=24):
    """Where a tank launched from `start` (x, y, z) by a physics driver's `linear` velocity
    would land, assuming it comes back down to the same height it launched from -- the usual
    case for a trampoline or launch pad sitting on flat ground. Returns (points, landing) or
    None if this driver doesn't launch anything upward (linear[2] <= 0) for there to be an
    arc to show. `points` is a list of (x, y, z) samples along the parabola, in order."""
    vx, vy, vz = linear
    if vz <= 0: return None
    t_total = 2 * vz / GRAVITY
    pts = []
    for i in range(steps + 1):
        t = t_total * i / steps
        pts.append((start[0] + vx * t, start[1] + vy * t, start[2] + vz * t - 0.5 * GRAVITY * t * t))
    return pts, pts[-1]


# 16 x 16 RGB versions of BZFlag's own default textures, built into the program (base64 of the raw pixels).
# BZFlag draws a box, pyramid, arc or cone with these when it has no material, so the 3D preview does too.
DEFAULT_TEX_DATA = {
    'boxwall': (
        'jmJhjWFhhVdWgVVVd0pLg1ZWhlVQi1xYi1lXfVBOgVFPeE1IiFtWfE1Ie09LglZUdUxNflNSgFVU'
        'cEhIdERBeU5NfE5LgFFNfExKbENEfExKdkpHhFNOektGgFBLgVFMfVNTelBOeU9NaEFBfkxFdUpH'
        'fU5JhFNNb0NAakFAfVBPZj47ekpDZTo2VjEucUVBfFBPfVJRbEVDa0VEd0pHc0hEcUZCdEVCc0xO'
        'iF1efVRVelNTeUdDaTw5gVVVil9diVxaek9NaT47Xzg2bkE+dEdEc0ZDekZCdU9PbkdKeE9Sd01O'
        'cT47aTk2g1dVkWRihlhWh1hYgFJPfFBNfExHfFBOaj8+dEZDeFFSfVVVf1ZXcUlKazs2eElGd01M'
        'lWZjeUhGbD8+bz49b0FAeUZGdEJCdEREeUlHaT4+aT49Zjg4WDAvZTk4dUZGbUFBfE9Oe0VChU9P'
        'bDo5gUtKfURDf0pKgElJhU5OgExMdkVEYzMzYDU1fklFekRBdUNCd01KgkxKfktLajo5e0dHfEZE'
        'f0tLh1BOgUtKfEdGdkREaDY3XzIzfUhFdkE+dkNEekxIdUhJZDw9Zz09cURFdUdHcUNCd0hIgE1M'
        'bkNDelJSakNFXzs8ZkFBYzw8a0NCdEhHgVFSdEhHd0pJfk9MgE5Md0ZEfE1MhU1Lf09Pil5fgVhZ'
        'flZYbERHcEhJa0NEeUpJfU1McUVFdEdGhVNRekhGfUtId0dGilFNfk9Oek5Nek9PflRWb0dIa0RF'
        'cEhJgU9Nd0lKa0BAcUZFbUNCYDY1c0ZDdkpMcUNDbENFZDs6ZT08Yjw+bEVGa0NDaEJBbEJChVta'
        'eU9NeVJUiF9hdUxNjGNjhVxde1NWflZabklJd09QeVJVZj9CfFJPfFNWg1pbgVdVdEtKeFBRhFtb'
        'fVZUg1lZelJRflZZfFRYYz4+bEZHfVVWakJEg1lYeVBQgFdYfVJQeU5McktMfVJRelBPgFRUgVVT'
        'g1ZUglRTZ0FAcUhFhltZf1VWgVZTcElKelBP'
    ),
    'pyrwall': (
        'F05qJWF9K2mELWuGIFt3E0ZjF0xoH1l1KGWAG1NvEkZiFUtnF05qJ2N/JWF9JGB8IVx5H1h1Hlh1'
        'KGR/KGSAFktoFkxoGlJuIVx4IFp2FEhlE0ZjEURgF05qHFRwG1RwHVZzIFp2Hld0JmJ+J2SAHFRw'
        'HFZyHVZyH1l1HFRwFkxpD0BcDj5aDDxXGlJuIlx5HVZyIFp2H1dzIFp3IFp2HVZyHFVxI115JmF9'
        'HVZyF05qEEBcEURgEUNfFEhkF0xpGlJuHVZyHld0Hlh0HFVyHFRxHFVxFEpmH1l1GE9sF01qFktn'
        'DT5aDj5aCjVQFUpmGE5rIVx4Il56G1RwHVZzH1p2J2N+IVt3JmJ9Il56GVBsF01pE0djEEFdCjZR'
        'D0BbEEJeFEllFkxoH1h0Hlh0IFp2KGWBIFp3J2F9I115F05qGE9rEURfDz1ZEUNfCjZREkdjGE9r'
        'FEllEENgG1RwG1NwHldzI156GlJuHVd0GVFtHFVyEUJeCzhTDTxYFEllH1l2H1h0EUNfFUlmFUll'
        'IFt3JWB8JF97F01pJWB8KGWBJWB8JF56EkVhF0xoHFRwJWF9GlJvGE5qFEhkD0BcGVFuGVFtHldz'
        'JmF9GlNvH1p2KGWBI197HFRwFEhlGVFtIl15GlJuF05qEkVhDz9bD0BcHlZyHlh0IV15IFl2Il15'
        'K2iDI197H1l1HVZyH1p2Il56IFp3G1NwGVBsDT1ZDz9bFEllHVZzJWF9H1h0HFRxH1l2JmJ+JWB8'
        'IFp3Hlh0G1RwHFRxFUtnD0BcFEhlCjVQDDpWFEhkFUpmIFt3G1RxHVdzHlh0GVFtGFBsFkxpIFp2'
        'J2J9E0hlFUtnE0ZiDjxXDDhTDTtXFkxoIlx4IVx4GlFtHFVxHFVxJ2J+IVt3IVt4HldzHFNvFkxo'
        'Dj1ZCzhTCzhTDTxXEUNfEUNfFEllFUtnIFp2I115JWF9HFVyIFp3HVZyHFVxIVt4EUFdDj5ZE0Zi'
        'EkVhGE9rF05qF05qGU9sFkxpI156Hld0JWB8'
    ),
    'roof': (
        'm5ubnZ2doaGhnJyck5OTm5ubl5eXmZmZm5ubmZmZmpqam5ubm5ubnJycl5eXoKCgoKCgm5ubnZ2d'
        'nZ2dmZmZpKSklJSUlZWVmpqampqanZ2dlpaWmpqam5ubnJycoaGhnp6em5ubl5eXlJSUm5uboqKi'
        'np6empqampqanJycn5+fl5eXoKCgoKCgm5ubn5+fo6Ojm5ubnp6em5ubkZGRlZWVoaGhnp6em5ub'
        'nZ2dnJycmpqaoqKioKCgmZmZoaGhmpqanp6enJycm5ubkJCQlZWVm5ubm5ubmZmZlZWVmZmZmZmZ'
        'oKCgoqKil5eXnZ2dlpaWk5OTmpqalJSUlpaWmpqamZmZl5eXlpaWlZWVnZ2dk5OTkJCQn5+fnp6e'
        'lpaWnZ2dkpKSmJiYm5ublZWVmJiYmJiYm5ubl5eXmJiYnZ2dnp6el5eXmJiYlpaWm5ubnZ2dmZmZ'
        'mpqan5+foKCgmJiYlZWVmJiYm5ubl5eXmpqanZ2dn5+flJSUmZmZnp6enp6emJiYmJiYn5+fl5eX'
        'mJiYlpaWmpqanJycmZmZl5eXmZmZnZ2dmpqan5+fn5+fn5+fmJiYlpaWnJycmpqanJyckZGRmZmZ'
        'mpqamZmZmZmZmpqak5OTlZWVmJiYmpqanp6em5ublZWVmZmZmZmZm5ublJSUnp6enJycm5ubk5OT'
        'lJSUnp6em5ubnp6eoaGhm5ublZWVmpqanZ2do6Ojnp6empqampqamJiYo6OjnJyco6Ojm5ubn5+f'
        'nZ2dnZ2dmpqanJycnZ2dnZ2dmZmZmpqalpaWoKCgpaWlmpqan5+foKCgmpqam5ubk5OTn5+foKCg'
        'oKCgmJiYm5ubl5eXmpqampqampqal5eXmZmZlJSUl5eXkpKSn5+foKCgoaGhoaGhlpaWl5eXl5eX'
        'lpaWmpqaoKCglpaWmZmZmZmZmpqalpaWoaGhnZ2dmJiYn5+fmpqanZ2do6OjnJycmJiYk5OTmJiY'
        'mJiYm5ubnZ2dmpqamZmZoaGhm5ubl5eXn5+f'
    ),
    'red_basetop': (
        'i4uJjo6LjY2KkZCNjIuIjIqHhYJ/h4J/iYSBioOBiIB+k4qIjoWCkIaEkoiFmI6LlYuJm5GPop'
        'mWmpGPnpaTnZaTlpCOkIuIlJGOk5COlZOQkpGOjo6Lj4+MkJCNioqHkZGOk5OQjo2Kin17jmNh'
        'il1cjV5djl1cj19djIB+jIF/k4iFlXh2lFxbl19dmWBfnmZlm2JhnWVknGVknmlnnGhmmGVklW'
        'RjlGVkk2ZklGlnkGdmi4OBk5KPlZWSiYmGlJOQlZSSjYB+kjk4mjAwnTExoTIypTMzqDQ0lFJR'
        'kIKAl3Z0rDw8szg4szg4szg4tDg4sjg4sTc3sDc3rjY2qzU1qTU1pjQ0ojMznzIymjAwly8vjz'
        '09lIyJjYyKjo6LmJeUnZSSlDs7mjAwnjExpDY2qTo6rDs7rjw8mWlomHh2sT09uDk5uTk5vD4+'
        'vT8/vkBAvD8/uj8/uT8+tz4+sz09sT09rTw7qTo6pTk5pDY2mzAwly8vjz08jIiGlZSSnZuYmo'
        'WCmjAwnzExoEBAjHl3j4KAjn58jn17k399nWhmvDs7vjs7sElIkHJwmH17mn99kXd1ln58l399'
        'mIKAmISCkoB+lYWCkoSCkoaEm4SCqkNCmzAwli8vlYmHmZiVmZeUmoOBnjExojMzjG9ti317j3'
        '99kX98jHd1kXp4oWdmwzw8wz09j2tpknNxmHl3l3h2mHl3lnl3lnp4mn99l399k317kX58lISB'
        'nY+MlImHmXFvnzExmjAwkoWDlJOQl5OQk3t5oTIypzQ0im5skIB+kX58lX99kHh2k3h1lHRzs1'
        'VVp1pZpoWFu5mZvJmavpucvJqbqomJlHV0mHp3lXl3lXt5k317loOBm4qIlomGmnRyozMznjEx'
        'i316lpOQko2LlXt5pjQ0qzU1jW5skH57knx6lHt5lXh2knJwj25ssI6OzqmqxZ6fsYqJ3re4xZ'
        '2dto+P1q+wyKOjt5WWlnd1mHt5mH57l4B+moeEmYmHnHNxpzQ0oTIyjn98lpKPjoiFkWlnqTU1'
        'rjc3j3FvkHp4jHRylXh2lnVzlnVzyqanxp+f37a3wpiZ0qio68LE1aqryKCg1aus5by9uJKS0q'
        '6vnn9+nX99moB+lX58m4iGnXh3rjc3pDMzkXNxlI+NkHJwozY2qjY2mmZlkn58jnZ0jnJwlnZ0'
        'mXh31K+xvJSU572+y6Ch162u1q6v1q+x0Kipy6Sl0aip2rGy5Lq8upOT2bO1n4B/m358moF/mo'
        'WCmIaEp2tqqzY2njc3knt5nFpZqDU1o0FBjnt5lX99k3l3lXZ0lXRyz6usu5OT5ry9z6Slzqan'
        '1Kyu1KSkzJaVzZeX1qWl06ytyKCh1Kqr6L7AvpeXz6usmHl3mX17mYF/mIWDm4uJq0BApDMzmW'
        'NhnFlYqjU1p0NDlIF+m4OBmXx6mnl3s5GRvJWV4ri52K2uyqKj2Kqru3t6t3JxtnBvt3Jxs3Bu'
        't3l41aany6OkzqSl6sDCw5yct5WWmnx6mH99m4aEmomHrUFBpjQ0lV1cmVRTrDY2qURDmIKAnI'
        'KAoIF/mHd1yKKj5Lq73rO0y6Kj2Kqru3d2vXRzvXFwu25tuWxruG5ttnBvsnJx1KamzKWmz6an'
        '576/z6qrmXt5mn58moSBmYeFr0FBqDQ0klpYoVxbrTY2rEVFnIaDnYJ/o4OBqoiI17Cx2K2u06'
        'ip1Kytv39+uG9tvnBvvmxrvWppvGppumtqtGlosm5ssnVz1a2u1qytxp6ey6WmsI+Plnp4lX17'
        'mmlnrjk5qTQ0mWBeoVtarjY2rUVFnoeFooaEpYSCvZqauJCQzKGizqWm1qent3Fvum1su2lovG'
        'dmvmdmvGZlv2xrumtqtGxqsG9u1KOk0qqrzqWlromJvZucmXx5qlFQtjk5sTc3qjU1mmBfoFpZ'
        'rzY2rkZGo4yKpIeFo4F/wp6fxJqa1qqrz6eo0JqauG9tumtqvmppvWVkwGZkwGdmvWhnumlotm'
        'xrsW5tx5GR1q+x6sHC3LS1vJqblXd1v0BAolhWmn17rkFBnmRioVxbrzY2r0hHn4eFpIiFpYSC'
        'wp+g17Cx68LE1a+w1J2euHBuu2xqvWlnv2dmvmNivmVkvmhnu2tpuW9tsW5tyZOT1K2u3LKzyq'
        'KjvJqbk3VzwEBAnF1ck4B+p0FBm2FgoVtarjY2rEZFnYaEooaEpIOBvZqatY2N1Kmp27Kz1aan'
        'unNyum1sv2xrwWtqu2Rjv2lowGxrvG1rtm1ssnBu0aGizaanyaChs42NvJqalXh2uE1MtTk5qT'
        'g4qjU1l15coVxarTY2q0ZFm4WCnIB+nn58qoiHzaWlxZyc16yt1a6vwoKBu3Fwv3FvvGppvGlo'
        'v21swHBvum9ts25tsnV01K2u0qipzqWl0qytp4eHl3t5mH99pV9dsDg4qTQ0mmBfoFxbrDU1p0'
        'NCk317l358nH17nHt5yqan47m7zKGi0aip16mqunZ1unFvuW1svW9uv3Jwwnh2vnh2s3Nx06an'
        'yqKj1qyt6b/Bx6KjlXd1mn99mIF/kH57rkFApzQ0mF9en1xbqjU1pkNDlYF/mYF/nH99m3l3sY'
        '+PyKCg6sDBzqOkzaSl16qrvn9+unVzunNyvHd1u3d2wYOC16qqyqOk0Kan6L/AuZOTs5KTmXt5'
        'lXx6kXx6kX99qz8/pTQ0mGBfmlhXpzQ0okFBkX98mIF/mX58mHl3l3VzyaWmwZmZ6L6/06ipzK'
        'Ok1K2u1qeo0Zuc0p2d16ip1q+wzaWmzqOk6sDCuZKS0KytkHFvlHh2k3t5jHl3jX57qT8+ozMz'
        'k1xbmnFvpjU1pjc3kmxqk358k3p4lnp4mXl3nHp506+wvpWV572+16yt0qipz6an1a2v1a+x1a'
        'yu1aqrzKKi6sDCuZKS2LK0mnt5lHZ0knh2jnl3iHZ0j2VkpjY2nzMzkXFwjYeEl2NiqDQ0qTk5'
        'knd1kn16ln16mXx6nXx6n317zKipvZaW572/zqSlyqCh37S168HCzKKjxJub576/v5iYzamqmX'
        'l4l3l3k3l3jHZ0iHVzjW1rqDc3pDMzjmVkkY2KkIuJj3VzpTMzqjU1jW5sj3x6lH58moB+m358'
        'mXl3nnx6spCQxqGh1q+wto6OyKCh1q6vsYqJzaany6eosI+PknNxj3Fvk3l3j3h2i3d1inl3km'
        'popjQ0oTIyhnd0joqIkY2LlXx6oTIypjQ0jG9tkH99k4B+loB9mH99m399nX99nn17nHt5q4qJ'
        'vJqawJ2dvpucuJWWoICAoWJhoV5ckXRyjHJwkHl3j3t5iXh2h3l2kWtqozMznTExjn99j42KlZ'
        'KPmIF/nTExojMzjHJwkoWClYWClIF/k317lX58mH98m399n4KAm3x6n4B+ooKAm3t5knNxk2xq'
        'wT09vjs7lGlnj3l3j3x6kYB+iXt5iX17kGxqnzIymjAwjX99kZCNk5GPj3p3mjAwnjExl0dGkY'
        'OAj4J/kYJ/k4F/k399lYB9m4OBm4OAk3l3mH17lnp5mH58knd1q0xMuzo6uTo6lmxqkX99lIOB'
        'kIOAjYF/i316mUJCmjAwli4uj4OBkpGPi4qIjoF/ljQ0mjAwnjExmTs7m0JBn0NCokRDpENDqE'
        'REqkVFrUZGrUVErkVErkVErkVEsEJBtzk5tTg4sDs7mHt5l21roENCm0FBmEFAmDk5mzAwli8v'
        'kTY2kouJkpKPi4uIiomGiXFvljU1mTAwnTExoDIypDMzpzQ0qjU1rDY2rjY2sDc3sTc3sjc3sj'
        'c3sjc3sTc3sDc3rTo6mXNxmo2Lo1JRpTMzoTIynjExmjAwli8vkDU0j358kJCNj4+NiYmGkJCN'
        'iomHjXZzjlJRk1RTmFhWmFZVmFRTmlRTnFRUnVRTn1VUolhXoVZVoVZVoFVUnVNSoFZVnHl3m4'
        '+Nm5GPmIOBnFlYl1dVklJRkFNSj1RTi3p4iomGioqIj4+Mjo6MjIyJiYmGiomGi4mHjImHjImG'
        'j4uIi4WCiYKAiYF/i4OAjoSClIqHmI2LmI2LkIaElYuJl42Ln5aTnZWSmZKPmJKPko2KjoqHio'
        'eEiIaEj46Mi4qIkJCNioqHj4+M'
    ),
    'red_basewall': (
        'amBXZFpOYFZKZltRYllPYVlQX1ZOWlFJW1FGWk5BWk5BZllObGFVa19Sal5SZlxSZ15UYllPY1'
        'pQZFpQYllOYVhOYFhQYVdPXFJHYldMXVJIZltQY1dMbGBVbWFWa2FYZV1UY1pRZ15UZFtSYFlQ'
        'YVhPYllQX1VMX1RJZFlOY1dLZVlPY1ZLZllNZFhOZ19XZFtSZFtSZVxRZ11TZ19WaWFZZV1UYl'
        'hPY1lPZFpPZFhOZFhOYVRJYlVKYldLZVxUck1HbklBaUQ8aEM8bklDb0tEa0lCaUY/ZUM9Y0A3'
        'akQ7bUc/bkU8cEhAbUc+cEpEhwkKhwkKhgkKfwgJhQkKfggJfwgJdwgJcwcIegcIewgIfggIhw'
        'gJiwkKiQkKhAgKhwkKhwgKfwgJewgJgggKfggJfggJdQcIdAcIeAcIdAcHfggIhwgKhAgJigkK'
        'gwgKiAkKhggJfAgIeQgIgQgJeggJeQgJdQcIcAcHbwcHcAcHeQcIhAgJgAgIhAgJgggJgggJgQ'
        'gJeQcIeQgJfggJfAgJdQcIdQcIcQcIbgcHbQcHdAcIeAcIegcIfwgJfggJewgJewgIdQcIeQcI'
        'ewgJfAgJdgcIcQcIcQcIaQYHaQYHbQcHcAcHcwcHeAcIfQgJfwgJdwcIbwcHeAcIfAgJfAgJdQ'
        'cIcAcIawcHYwYGaQYHbwcHbwcHagYGfAgIeggJZFJIYlBFXEo+W0pAX09HXExEWEg/VkU6VkQ5'
        'UUA0V0Q5XEs/Y1BFZFFEYk9DYVFHY1hOYVVKXVJHXVJJXVNIV09GVk5EVEk+TkM4VEc9V0tAWE'
        'xBV0xBXVFFWk5DYFVMYFdNYlhPYVhOYlhPYlhPVExEV09HVExCVEtCV01BWU5CWE1DW1FGXFFG'
        'YlVLZFtRaF5VYVdOXVRKY1pPY1lPVU5FWVFIVU1FV05GW05EXlRJXVFGXVJGXFBFX1RJY1pSaW'
        'BWZ1xRYFRJZlxSaV9WWlNKWFBIWlBHXFFGWU1BYFRHZlpOZltPXlFEX1RIX1ZN'
    ),
    'green_basetop': (
        'i4uJjo6LjY2KkJGNioyIiYyHgYV/gIZ+gYiAgYh/fYZ8h5GGgYyAgo6ChJCDipaJh5OHjZmMla'
        'CUjpiNk5yRk5uRjpWMiY+Hj5ONj5KNkpSQkZKOjo6Lj4+MkJCNioqHkZGOk5OQjY6KeId4UoVX'
        'S4FSTINTSoNRTIVSe4l7fYl8g5GDbY9wRohOSItRSo1SUJJYTI9UT5FXUJFYU5NbU5FbUo1ZUY'
        'tYU4pZVIlaWItdV4hcgIp/kpKPlZWSiYmGk5SQlJWRe4t7FX8kBoMYBoYZBooZB4waB5AaN4ZB'
        'fY19aZBsEJUjB5gcB5kcB5kcB5kcB5gcB5ccB5YbB5UbB5IbB5AaB40aBosZBocZBoQYBoEXHX'
        '4qiJKHjI2Kjo6LlpeTkZuQGIEnBoMYBogZCo0dD5EhDpQhD5YiVo9dbJFvD5gjB50dB54dDKEh'
        'DqIjD6MkDqIjDqAjDp8jD50jDpoiD5giD5UiD5IhDo4gCo0dBoUYBoAXHH0qhoyElJWSmpyYfJ'
        'Z+BoMYBocZG4wqcYhyfY19eYp5dot3eI95U5FaCKEdCKIeIZoxZ4pqc5J1dZR3bYtvdJF2dpJ4'
        'epN7fJN9eY56f5F/f49/go+Be5Z8GpQrBoUYBoAXhZKEmJmVlpmTe5V8BocZBosZY4ZmeIh4eY'
        'x6d414b4hwcYxzUZRZCKYeCKYeXIdhZ4xqbJFwbJFvbZFwbZBwb5BydZR3dpJ4dI52d414fZF+'
        'iZmJhZKEYpBmBogZBoQYgI+AkpSPkpaPcY5zBooZB44aY4Rmeo16d414dpB4botwbY1vaI1rMJ'
        '8/PZdIf6KAlrmWl7qXmbyYmLqYg6aEa45ubpJxbo9wcY9zdI92e5J8hJeEg5ODZZJpB4sZBocZ'
        'd4h3kpWQjJGKcZBzB40aB5IbYYZldox3dI11cY9zbY9wZotpYolliq2Kqc2omsKagayDt962l8'
        'GYh7GJrtWtoMagkrWSbJBvb5Jyc5J2d5J5f5aAg5aDY5NoB44aBooZeYt5kZWPhY2EWYleB5Aa'
        'CZUdZYhocotzaodsbY9waI9sapBtpcmkmsOatd60kr6To8+kw+vCptKnm8Wbp9Knu+S6i7SMrt'
        'Ktdpl4dJd2dpV4dZB3gZeBapVuCJUcB4waZ4prjpOMZoppC4sdCZIcUo9Zdo53bYlvZ4hqapBt'
        'bZRwsNSvjbiOvOa7m8ecqdWqrdSsr9Wups6mocmhpM+krtiuueO5jLaNtNmzd5p5c5V1d5V5fJ'
        'V9f5SAVJpdCJIcD4ggc450QI5KB5AaG44rdIp1dpB4b45xao5tZ49rq86qi7eNuuS6n8ygo8yj'
        'rNOrm8+eiMSMicWOnNGfqtKqncWdp9Gnvue9kLqSq8+qb5JxcpN1eJR6fZR+hZiFFpQoB4waTY'
        '5VP45JB5EaHJIseZB6eZZ7cZNzbJNvjLCNjriPtuC2qtaqn8efpNSlZK5tV6hhVadfV6lhVaRf'
        'YqtqoNGhoMigoMugwem/l7+YkrSScJNzdZN3fpd/gpaDF5YoB44aR4lPOopEB5MbHJMsepN7eJ'
        'Z6dZl4a5FuoMagueK4sNuwn8mfpdSmXaxnWK1iVK1fUKtcTqhaUqhcVadfWaVin9Cgosqiocyi'
        'vua9qc2ocZN0dJR2e5V8gJaBFpgoB48aQ4ZLQZJLB5QbHZYtfZd+d5d5d516gaaCr9auqdWppN'
        'Clq9KraLJwUqhdUa5dTKxYSqtWSqpWTKlYTKRXUqRdXqVmrNOsqdOpmMOaocmhiq2Lb5BxdI91'
        'VZBcC5UfB5AaSY1RQJJKB5UbHJcufpl/e5x9d556l7uXiLOKm8icocyioNKiVahfT6paSapVRa'
        'pSRaxTRKpSS61YTKlYUKVaVaNfm8+dqNCooMqhgamDmbuYcJNzLpc8B5scB5cbB5EaSY5RP5FJ'
        'B5UbHZgugp6EfJ5/dJx4m8Cbk7+VptOmpM2kjcmRU6hdTKlXSaxWQqpQQq1QRK1RR6tUSqlWT6'
        'ZaVKNehL+IsNWvwenAs9uymLqXa45uDqQjOpJGcZR0FpcoTZFVQJNLB5UbH5kwfpp/fJ5/dp56'
        'nMCcr9auw+vCrtStkcyVU6leTKpYSKtURKxSP6tNQqtQR6xUS6pXUqlcVKNdhsGKrNOrr9mvns'
        'eel7uXao1sD6QkRI5OeI95GpEqSo9TQJJKB5QbHZYufZh+e5x9dp15lruWhLCHo9Cksdqwn9Gh'
        'V6tiTqpaTK1YSa9WQqlPR61UTK5YTqtZUKZbVqRgmcybo8ujm8achq6Il7qXbI9vI6E0B5ocDJ'
        'EfB5EaR4tPQZJLB5QbHZYufJZ9dpZ4cZd1gKWCocqhlcGWqNSprdSsbLZ0VKtfUq9dSqpWSatV'
        'Ta5ZUa9dUapcU6VdX6ZnrNOrpM+koMugqtGqgqSDcJFydZN3Q5ZOCZcdB5AaSo1SQZJMB5IbG5'
        'IsdY52c5J2cZV0bpZypcmluOG4m8idpc6lpNOlXKxmVKpfT6laUaxcVK9fW7JlXK9mWqZjoNCh'
        'n8efqdSpv+i+oMWgbY9vdZV3eJN6dox3FpcnB48aSYxRQZFLB5EaHZEteZF7d5R5dJZ3bJRwiq'
        '6LmsWcv+m+nsqfocuhpdOmaLJwWqxkWKtiW61lXaxmbbV1pNSloMigos2ivue9jbWOj7GPb5Jy'
        'cpB0dI11eY15FZQnB40aSoxSPoxIB48aG44rd414eZN6dJN2bZFwaJBrpcikkr2Tvee8pNCloM'
        'mgrNOrodKij8qTkMuUodOjrtStosuinsqfwOm/irSMrNCrZoppbY5wco50cYhyd4p4FZImB4sZ'
        'R4dPYZJmCY4bDI8eXYpido53cY1zb5BybJJwb5Zyr9OujbmPvOW7qdSppM+ko82krNSrr9Wuqt'
        'OqptKnnciewOm/irWMs9eycZV0ao1tboxxcYpyb4RwVYZaCY4cCIgbZYpphIyDT4xWB5AaDZEg'
        'bIxvdI11c5B1cZNzb5ZycZl1p8unj7mRvea8n8ugm8acsdyxwerAncielcCWvua9kruTqcyob5'
        'NybZBwb45xbYdvbYRvYYZkCpAdB4waVYVbi5CJiY+IaoltB40aB5EaYYZldYt2dY93dpV4c5V1'
        'bJJwb5dziq+MnsSerdSthrGImsWcrNSrgauDo8ujpsqli66LZ4xqZolpbo1xb4pxb4dwc4ZzWo'
        'pfB44aBokZcINxiY2HjJGKcpB0BooZB44aZIZneYx5eY95d5F4dZJ3dJR2c5d2cJd0b5VygqeE'
        'lrqWmr6ambyYkrWSeZx7SpRTQ5NNaYpsaIZqcItyc4t0cYVyc4RzXYlhBosZBoYZeot6jI+JkZ'
        'SPeJN6BoYZBooZaIZqf49/fpJ/epB7dY52dJB2dJN2dJV2dpl5b5Rzc5l3dpt5bpRxZ4xqXYti'
        'CaUfCKIeWItecYpydIt1eY16doZ2eIZ4XohiBogZBoMYeop6j5GNkZOOcYpzBoMYBocZJ4Y0fo'
        '59fYx9e458eo97eI95d5F4epV7eZZ7b45xc5J1cJByc5N2bI1uJ5c3B6AdB54dW41geI15fZB+'
        'fY59fYp8d4h3IIcuBoQYBoAXf41/kZKOiouHfYt8DoEeBoMYBocZFYUlH4gtH4stHo4uHZAtHp'
        'IuHpUuHZcuG5ctG5ctG5gsG5gtFpkoB5wcB5scDJcgcJJyXI5iHowtHogtHoUtE4QjBoQYBoAX'
        'En4hiZCHkpKPi4uIiIqGaIRqDoIeBoMYBoYYBokZB4waB48aB5EaB5MbB5QbB5YbB5ccB5gcB5'
        'gcB5gcB5gcB5cbDZQgZJFoiJiIMpI/B40aBooZBocZBoQYBoAXEXwgeIx4j5CNj4+NiYmGj5CN'
        'iYqGbIhuOoJDO4VFPotIO4pGOYpDOYtEOY1DOI1DOY9EO5JGOZFEOZFEOJBDNo5BOZBEbJVvi5'
        'iKjZmMe5R8Po5JPYpHOoRDO4RFPYNGdId0iYqGioqIj4+Mjo6MjIyJiYmGiImGiYuGiIuGh4yF'
        'iY6Hg4qBf4h+fod9f4p+gIyAhpKFiZaJiZaIgo6BiJOHiZWIk52RkpuQj5eNj5eOi5GJiY2Hho'
        'mEhYiDjo+LiouIkJCNioqHj4+M'
    ),
    'green_basewall': (
        'amBXZFpOYFZKZltRYllPYVlQX1ZOWlFJW1FGWk5BWk5BZllObGFVa19Sal5SZlxSZ15UYllPY1'
        'pQZFpQYllOYVhOYFhQYVdPXFJHYldMXVJIZltQY1dMbGBVbWFWa2FYZV1UY1pRZ15UZFtSYFlQ'
        'YVhPYllQX1VMX1RJZFlOY1dLZVlPY1ZLZllNZFhOZ19XZFtSZFtSZVxRZ11TZ19WaWFZZV1UYl'
        'hPY1lPZFpPZFhOZFhOYVRJYlVKYldLZVxUUWtTTmZMSmBHSF9HTWdPT2hPTWRNS2FKSFxHRVpB'
        'Sl9FTmJJS2JITmdMTWNJUGdQAIM7AIE5AIA4AHk1AIA6AHk2AHo2AHAxAGsuAHEwAHAwAHQxAH'
        '03AII5AYA4AH45AIE5AH84AHUyAHUzAH04AHg1AHg1AG4vAGsuAG0vAGosAHQxAH43AHo0AIE4'
        'AH44AII6AH02AHMxAHExAHo2AHMyAHMzAG4vAGgsAGUpAGYpAHAvAHs0AHYxAHs0AHs2AHw2AH'
        'k0AG4vAHExAHc1AHY0AG4xAG4wAGksAGQpAGIoAGorAG4uAG8uAHYxAHc0AHQ0AHIwAGstAHAw'
        'AHQyAHUzAG8xAGotAGotAF4mAF4mAGMoAGYpAGoqAHAuAHY0AHk2AG4uAGUpAG8wAHUzAHUzAG'
        '4vAGgtAGIpAFkkAF4mAGQpAGQpAGAlAHMwAHQyXFlLW1ZHVVBAVFBCWFZKVVJGUU5BT0o9T0k7'
        'S0U2UEo7VVFBXFdIXFdGWlZGWlhJY1hOYVVKXVJHXVJJXVNIV09GVk5EVEk+TkM4VEc9V0tAWE'
        'xBV0xBXVFFWk5DYFVMYFdNYlhPYVhOYlhPYlhPVExEV09HVExCVEtCV01BWU5CWE1DW1FGXFFG'
        'YlVLZFtRaF5VYVdOXVRKY1pPY1lPVU5FWVFIVU1FV05GW05EXlRJXVFGXVJGXFBFX1RJY1pSaW'
        'BWZ1xRYFRJZlxSaV9WWlNKWFBIWlBHXFFGWU1BYFRHZlpOZltPXlFEX1RIX1ZN'
    ),
    'blue_basetop': (
        'i4uJjo6LjY2KkJCOi4uKioqKgoODgoOEhISHhISIgYGGi4uQhYaMh4eOiImPj4+WjI2TkpOYmZ'
        'qfkpKXlpeblpaakZGUi4yNkZGSkJGQk5OSkZGPjo6Lj4+MkJCNioqHkZGOk5OQjY2Lfn+IZWmN'
        'YGSJYWWNYGSNYWaOgYKKgoKJiImRenyTX2SUYmeXY2iZaW6eZWqbaG2caG2ca3Ceam+caG2YZ2'
        'uVaGyUaGySa2+TaW2Qg4SJkpKQlZWSiYmGk5ORlJWSgYKLPUWTNT+cN0CgOEKkOUOnOkWqVVuU'
        'g4SOd3qWQkyuPkm1Pkm2Pkm2Pkm2Pkm1Pkm0PUiyPEewPEauO0WrOUSoOEKlN0GhNj+cND6ZQU'
        'mRjI2SjI2Kjo6Ml5eVlZabQEiWNT+cN0GhPEamQEqqQUuuQkywbHCZen2WQ06zQEu6QEy7RFC+'
        'RlG/R1LARlG/RVC9RVC7RE+5Q062Q02zQkyvQEqrPkinPEamNj+dND2ZQUiQiImKlZWSm5uaho'
        'iYNj+cN0GhRU6henuKg4SNf4GMfoCMgIKRam+cQU2/Qk7ATlexdHePf4GWgYOYeHuPf4GVgYOV'
        'g4WWhYeWgYORhoeThYaPh4iPhYeZSFGsNkCdND6YiouTmJiWl5eWhYeYN0GgOEKlcHOLfn+JgI'
        'KNgIGPeHqKe32Qam+gQ0/FRFDFbXCOdXiRen2XenyWe32Wen2Ve36UgYOYgYOWfoCRf4GQhYaS'
        'j5GaiouSc3eYN0GhNj+choeQk5ORk5SUfH6SOEKkOkSpb3KJgYKOf4GPgIKTeXuPeXuSdnmTWm'
        'O0WWOmcYCieI61eI62epC4eY+2c4OlcXeSe36Xen2TfH+TfoCShIaVi42ZiYuUdnmZOUOlN0Gg'
        'fn+Jk5OTjo6QfX+UOUSoO0atb3KMf4COfX+RfH+SenyTdHeRbXKOcoWqfprGfpS/d4atiKbWgZ'
        'XAeoqxhaDOf5fBeIyxdXmUfH+Xf4GWgoSViIqYiouXdXmbOkSpOEKkgIGMkpKTiImMa2+RO0Wr'
        'PkiwcnWNe32OdXeLen2Ud3qUbnaUfZfDf5XAiqfXfJC8hJzMjq/ihp/Of5XChp/OjavdeouzgZ'
        '7Kdn6bgYSbgoSZf4GTiYuZen6cPUixOUOndHeQkJCRdHaPO0WkPEetaW6Zf4CQeHqNdHaNeHuV'
        'cnmXgp/Neo23jazfgJfFhaDQgp/Ogp/Of5rIfZfEg5zLiKPTjarcfI21hKLRd3+cf4Kag4WZho'
        'iYh4mWb3SnPEetPUagfX+RXWOcOkWrRk+kfH6MgIKTen2SeHqUc3iUf5vHeoy2javegpnJf5jH'
        'gZ3Mg5vOgZLIg5TJhpzQgZ3Me5TBhJ7Nj63gf5G5gJzId3yWf4GXgoWXhoeWjI6ZRk+tOUOmZm'
        'qZXWKdO0atSFGogoOThIaZfoCXe36Zdomue423jKnahqHRfJXDhZ/SeYK5dny2dHq2dny3c3my'
        'eIC2hJzPfZbEgprHj6/hgJS9eIyxfYCYgYOXh4mZiouYR1CvOkSoYGWVWF6ZPEavSVKqg4WWg4'
        'aag4aedXqWf5fBjarbiaXWfpbFhZ/ReYC6eH69dXy9cnm7cHe5cnm4dHq2dXuyg5vOfpjFhJvJ'
        'jq3egJvHd32XgIKYhYeYiIqXR1GxOkWqXGGSX2ahPEewSlSth4mag4WbhYiid4SmhaHPiKHRg5'
        '3MgZ3Me4W9cnm4dHu+cHi+bna9bna8b3a7bXS0cXeyc3uwgZ7NhZ/PgZbBg5rFeYmre36VfoCT'
        'a3CaQEqwO0WrY2iZX2WhPUexS1SviIqch4qghYijfJG3fIy0gZjGf5jHhJ3QdHu3cXi7bXW7a3'
        'O9bHS/a3K8cHi/b3a6cHa0cniwhJvOgZzKg5rHdoWqepC3fYCXVl6rP0q4PUi0O0WsY2iaXmSg'
        'PUixS1WwjY+iiYyig4aifZO8gJO+hp/Pf5nIgZXLc3m4b3a6bna+anK+anLBa3PAbXS+bnW6cH'
        'a2cnixfY/Dgp/Njq/hiKXUeI+2eHuTR1LBW2KjfoGZR1CwZ2yeX2aiPUexTVaxiIqdiYyihomk'
        'fpS8haHQjq/igp/OhJjPc3q4cHe7bXW9a3PAaHC/aXG/bXW+b3a8cnm5cnixf5HEgZ7NiaTUgp'
        'jEeI+2d3mSR1PCYGacgYKRR1CpZGmbXmWhPEewS1Suh4mbh4qghYijfJC4eomxhp7OhqPThJzP'
        'd326cXi7cXi/cHfBaXC8bXXAcXjAcXi8cXe2c3mxgZjLf5jGf5bDeYmveY+2eXyUUly6P0q3Pk'
        'irO0WsYGaXX2ahPEewS1SthoiZgoSaf4Kcd4SmgprGgJTAhZ/QgZ7NfYfAdXy7dXzAbnW8bnW8'
        'cXm/dHvAcnm6cnizc3uxgZ3MgpzLg5rIg57LcoKjfH+WgIOWYmmlP0qzOkWrY2iZYGagPEauSF'
        'GpfoCRf4GWf4Kaen6bfZjDi6nagJfGgJvJhJ7Qd3+5dHu6cXi5c3q9dn2/fILCe4K9dXuygpvN'
        'fJXDhqDPjq7gfZbAdHmTgYOZgoSWfoCORlCwOkSpYmeXX2WfO0asSFGngoSUgoSYgYSbe36ad4'
        'itgZbCj67hgZnIfpjGhJ7QeoS8eX+6d325eoG8e4G6gIm/hp/RfZbDgpvKj67ge4y0domufH+X'
        'fYCUfX+PgIKPRU+tOUSoY2iYW2GaOkSqRk+jgIGPg4WWgIKXen2WdXmWfZfCgZK9ja3fg5zMfp'
        'fFgZ7NhJ3Qg5bMhZjOhp7Rgp/OfpjGgpnHj6/ifIy0gJzJcHSOeXySfX+RenyKfoCMRE6rOUOm'
        'X2STc3eZO0WoPUeobnGRf4GRfH6RfH6Ven2Ydnyagp7Lfo+5jazehqDQgpzLgJrIg57Ngp/Ng5'
        '7OhJ7OgJfGj67he4y1g6HPc3uXeHqSenyQenyNd3iGZ2uOPEapOUOhc3aQh4iLZmqXOkWrP0mr'
        'eHuRfoCQfoCUfoCYfoGbeH+df5nEfI+4jazeg5rIgJbEi6bXj6/igZjGfpO/jazefJC6f5rFcn'
        'qWen2Wen2Sd3mLdniGb3KMPUeqOUOnZ2uNjY2PjIyOdniNOUOnO0asb3KMfX+Nf4GSgoSYgIKa'
        'e36YfYCdd4mtfJS/haDOeoqxgpfChqDOd4asgprHfpnEdIercnaRc3WOen2SeXuOeHqJenuIbH'
        'CROkSoOEKjd3mFi4uMjo6PfYCTOEKjOkSocXOKgIKNgYORgYOUgIKWgIOZgIOcf4Kden+aeYan'
        'e5C3e5K6epC4douybnucYmmhYWehdXiPc3aKe32PfH6NeXqHeXuFbXGQOEKlN0CfgIGMjY2Nkp'
        'OSgoSWN0CgOEKkc3WKhYeQhoeTgoSSfoCRf4GUgIKXgIOZg4aefYCZgYSehIehfH+ZdXiRbnKS'
        'RFDEQk7Aa2+TenyNfX6NgYKPfH2Hfn+HbnGPN0GhNT+cgIGKkJCPkZKQe32NNT+cN0GgS1KYhI'
        'WPgoSNgoSPgoSRgIKRgYOThIaZhIaae32Rf4GWfH6Uf4GXeHuRUVqsQUy+QEy7bnKVgIKPhIaS'
        'g4WOgoOLfX+JR0+aNj+cND2YhIWNkZGQiouJgoOMOUKYNT+cN0GgQEibRk6cR1CgSFGjSVGmSV'
        'OpS1SsS1WuSlOuSlSwSlSwSlSwR1GyQEu5P0q3QUuyfH+Wb3OWSFChRk6cRU2ZPkeaNj+dND6Y'
        'O0OTjIyPkpKQi4uJiYmHcnSHOkOYNT+bNkCfOEKjOUOmOkSpO0WsPEavPEewPUizPkm0Pkm0Pk'
        'm1Pkm1Pkm0PUizQEqudXiXjo+YVl2kOUOnOEKkN0GgNj+cND6YOUKRf4GNkJCNj4+NiYmGkJCN'
        'ioqId3mLVVqOV12TW2GYWV+ZV16YWF6bWF+cWF6dWWCfXGKiWmGhWmGiWWCgV16eWmGge36bkJ'
        'GYkpOZhIaWXGKcWmCYVluSVlyQV12Qe32JiYmHioqIj4+Mjo6MjIyJiYmGiYmHiYqIiYqJiYmK'
        'i4uNhYaIgoOHgYKHg4SJhYWLiouRjo+Vjo+Vh4iOjI2Tjo+UlpeclpaakpOWkpOVjY6PiouLh4'
        'eHhoaGjo+Ni4uIkJCNioqHj4+M'
    ),
    'blue_basewall': (
        'amBXZFpOYFZKZltRYllPYVlQX1ZOWlFJW1FGWk5BWk5BZllObGFVa19Sal5SZlxSZ15UYllPY1'
        'pQZFpQYllOYVhOYFhQYVdPXFJHYldMXVJIZltQY1dMbGBVbWFWa2FYZV1UY1pRZ15UZFtSYFlQ'
        'YVhPYllQX1VMX1RJZFlOY1dLZVlPY1ZLZllNZFhOZ19XZFtSZFtSZVxRZ11TZ19WaWFZZV1UYl'
        'hPY1lPZFpPZFhOZFhOYVRJYlVKYldLZVxUW1VfV1BXU0tRUUpSVlBaWFJaVU9YVExUUUlQTkZK'
        'U0tPV05TVUxTWFBYVk5TWVFbJydqJyZmJyZkJSRgJyZoJSRhJSRhIyFZIiBSJCFWJCFWJSJYKC'
        'ViKSZmKCZkJyVmJyZnJyZlJSNbJCJcJiVkJSNgJSRgIiBVIiBTIyBVIh9PJSJYJyViJyReKCZk'
        'JiVlKCZoJyVhJCJYIyJYJiRhJCJbIyJbIiFWIR9PIB5LIR5LIyFUJyReJSNZJiReJiRhJiViJi'
        'ReIyFUIyFYJSNfJCNdIiFXIiFWIR9QIB5JIB1IIh9OIyFTJCFTJSNZJSNdJCNdJCJXIiBQIyFW'
        'JCJbJCNcIiFYISBRIR9QHxxEHxxEIB1HIR5LIh9MIyFTJSNdJSRgIyBTIR5KIyFXJCNcJCNdIi'
        'BVIR9QHx1KHRpBHhxEIB5KIB5KHxxDJCJWJCJaXlRNXVJJV0xDVkxFWlFMV05IU0pDUUY/UUU9'
        'TUE4UkY9V0xDXlJKXlJJXFFIXFNMY1hOYVVKXVJHXVJJXVNIV09GVk5EVEk+TkM4VEc9V0tAWE'
        'xBV0xBXVFFWk5DYFVMYFdNYlhPYVhOYlhPYlhPVExEV09HVExCVEtCV01BWU5CWE1DW1FGXFFG'
        'YlVLZFtRaF5VYVdOXVRKY1pPY1lPVU5FWVFIVU1FV05GW05EXlRJXVFGXVJGXFBFX1RJY1pSaW'
        'BWZ1xRYFRJZlxSaV9WWlNKWFBIWlBHXFFGWU1BYFRHZlpOZltPXlFEX1RIX1ZN'
    ),
    'purple_basetop': (
        'i4uJjo6LjY2KkZCOjIqKjIqJhYGDhoGEiIKGiIKHhn6FkYiPjIOLjoSNj4WPloyVk4mSmY+YoJ'
        'aemI+XnJSbm5SZlY+Tj4qNk5CRkpCQlJKSkpGPjo6Lj4+MkJCNioqHkZGOk5OQjo2Lh3qHhFiJ'
        'gFKGg1KIg1GJhFKKiX2IiX6IkYWQjnGRh02PilCSjFKUkleZjlSWkFeXkFeXkluZkFqXjViTil'
        'eQilmPiVqOil6Qh1yMioGJkpKQlZWSiYmGlJORlZSSin2KfiGLghSShRWWiBaZixadjhaghUCO'
        'jX6Mj22Tkx+klxiqlxiqlxirlxirlxiqlhiplRinkxelkRejjxehjBadiRaahhWXghWTfxSPfS'
        'iJkomRjYyKjo6Ml5eVm5KagCSOghWShhWXixqckB2hkh6klB6mj12UkXCUlx+pmxivnBmwnx2z'
        'oB+0oSC0oB+znh+xnR+wmx+umR+rlx+olB6lkB2hjR2dixmcgxWUfxSPfCiIjIeKlZSSnJuZlX'
        '+WghWThhWXiiiZiHSJjH+MinuKi3mLjnqPkVqXnxmzoBm1mC6oiWuMknaUlHiWi3CNkXeTknmT'
        'k32Uk3+UjnyPkYGRj4GOj4SOln6XkyijgxWUfxSPkoaSmZiWmZaWlX6WhRWWiRaahWeIiHqHjH'
        'uMjXqNh3KIjHSOlFibpBq5pBq5h2GLi2uOkXCUkHCTkXGTkHGSkHOSlHmWknmUjnePjXmOkH+Q'
        'mYuZkoaRkGeUhhWXghWTj4KPlJKRlpKUjnSQiBaZjRaehGaGjHyNjXmNkHmRi3KNjXCPjWyQnj'
        '2slUafnH2drpCvr5GwsZOysJKxn4GhjW6PkXKUjnGRj3WRj3eQkn6Tl4aYk4WTkWqVihabhhWW'
        'iHmIlZKTkYyPj3WRjBadkBeihmaJjHmNjXePjnSQjnGRi2qOh2aLpIamv6DAuJW5pYCoz67PuJ'
        'O6qoWsx6bIupm7q42sjm+RkXOUkneTknuTloKXloWWk2mXjRaeiBaZi3uLlZGTjYaLiF6Njheg'
        'kxmliGmKi3WMh22Jj3GRjm2RjWyQvJ28uZW60KzRtI62w53F2rnaxqDIupa8xqHH1bLWrIiuxK'
        'XElXeXlniZlHqWj3iRloOXlW+ZkximixacimuNk46RimqMihqbkBiijlmUjXiOiXCLiGuKj26S'
        'kW+UxqfGr4qy17PXvJa+yKPKx6XHx6bIwZ/CvJq+wp7Ey6fM1LHVromwyqvKl3iZlHaXlHuWlX'
        '+WlIKUmVyhkBiihx2XjXaPjUmWjhagjSibineKkHmRjnKPjm6RjmuRwKLBromx1rHWwJnCv5zB'
        'xaPFxJjIvIjBvonDxpnJxKLFuZe7xaDG2LXZso60waLBkHGTk3aVlHuVlICVl4eXkyWjihacjV'
        'SUjUiWkBeikCqfkHuRln2XknWVk3CWp4ipr4uy0q7TyaPKu5i9yJ7Lq2uzp2Cwpl6vqGCxo16s'
        'qWmwxZvIvJq9v5rB2rfatpO4q42sk3SWk3mVloGXloSWlSWljBaeiE6QiUOTkhekkiqhk32Uln'
        'yYmXmbkG6Tupm71LDVzqnPvZi+yJ/Kq2a0rGG2rF62qlu0p1myp1yxpl+vpGKsxZrHvpu/wZzD'
        '17TXwKHBkXOUk3iWlX6WlYKWliWnjhafhUuNkUqbkhellSukl4CYl3uZnHufoIChyKfJyaPKxJ'
        '7GxKPFr263qFyxrVy4q1e2qlW2qVW1qFazo1ato1yso2WrxaTGx6LIuZS7vpy/pYinkHOSj3eR'
        'j1yVlBumjxagjFGUkUmblBemliulmYGam36dnXugsZKyrIavvZe/v5vBx5vJp16wqVm0qVS0qV'
        'G1q1G3qFC1rFe4qFezpFmuol6qxJjHw6HEv5vBo4ClsZOxknSVljqjmRitlhipjxehjVGVkEia'
        'lBemlyumnoafnYCgm3ietZa2tpC5x6DJwJ3BwIzFp1yxqFazq1S3qU62rE65rFC4qlK2qFSzpl'
        'mvol2ruIS8xqbH2rfazavNsJKxjm+Qoh+2kUSclHWWlSWmkVWYkkqblBemmC2omYGbnYCgnnuh'
        'tpa3yKfJ2rnbxqXHxJDIqF2xqVe0qlO2q1C4qUu2qk62q1K2qVa0qVyyol2ruYa+xaTGzKfNvJ'
        'm+sJKxjG2PoyC2jU2WjnuPkCegjlKWkUmakxellSulmICZnH+enXqgsZGyqYOsxZ7HzKjMxZvI'
        'qmG0qVm0rFe4rVW5qE60rFO4rVe4qli1pVqvo1+rwZbEvpzAu5a9qISqr5Kwj3CRoDGwmRisjx'
        'uhjxehik+SkUqbkhellCukln+XlXmXl3aan3+ivpvAuJK6yKLJxqTGsnK6ql60rly4qVW0qVS1'
        'rVi4rly5qVuzpFytpGarxaTGw57EwJrBxKPEnX+fkXSTknmUlUyflhqojxagjVKUkUqakRejkS'
        'mgjniPkneUlXWYlXKYvJ690q/TvZfAwp7Dx57KqmWzqV6zqFmyq1u2rl64sWW7rma3pGKtxJvG'
        'u5m9x6LJ2LbYupm6jW+QlHiWk3yUjHmMlSSmjRafi1CSkEqZkBeikCqfkXySlHuVlniYk3GXpo'
        'aou5a82bbav5nBvpvAx5/Jrm+2q2O0qmGzrGW2q2a0snS5yJ/KvJm9wZzD2LXZrYqvqIqpknOU'
        'kHWRjXeOjXuNkySjjBadi1KTi0eUjRafjCibjXqOk3yUk3eVkXGUj2yTu528tY+317TYxJ7FvZ'
        'm/xaTGxpzJwY7FwpDHx5zKxqXHvpu/v5nB2rbarYivwqPCiWmLjXGQjnWPiHSJinqKkSOhihab'
        'h06OkWeWjRiejhueiWKNjnmPjXSPkHOSknCVlHKXxabFsYuz1rPXyKLJw57FwJ3CxqTHxqbHxq'
        'PHxaDHvZi/2bbarYivyKrJknOUjW6PjHKOinSLhHGEhVqKjRiehxeYimmNi4WKi1aSjhagkByh'
        'jHCOjXeOkHaSk3WVlXOYlnSZvp++sYyz1rTXwJrCu5a+z6nQ2rfavpjAt5G517TXso60v6C/kH'
        'GTkHGTjXKPh3CJhHCEhmWJjhqgixachFuJkIuOj4qNiW6LixadjxehhmaJi3eMj3iQlHqWlXeX'
        'knGVlnSZpoeouJi5x6XIqoWsu5a8x6TIpYCnv53AvZ6+pYemi2uOiGqLjXKPinKLhnKHhnWGiV'
        '+OjBaeiBaZg3KDjYmLkYyOj3aRiBaZjBaehWiIjHuMj3uPkXqSkniUlHiWl3eZl3WalHOXoYGj'
        'sJGxs5S0spOzq42slniYk1Gbkkybim2NhmuIi3SNi3aLhXOFhHWEiWKNiRabhRWWinyKj4yNlJ'
        'KSk3uUhRWWiRaahmuIj4GPkYGRkH2QjniPkHiRkniUlXiXmXqblHSWmHibm3qek3OWi2uOi2KP'
        'oxu4oBm0il6PinSLi3eMjXuNhneGhnqGh2OLhhWXghSSinyJkY+Ok5GQinSLghSShRWWhTKRjn'
        '+NjH6Mjn6Oj32Pj3qQkHqSlX2XlnyXjXOPknaUj3SSkneUjG+OlTSjnhmynBmwjWGRjXqOkH+Q'
        'jX+Nin6KiHmIhSySghWTfhSOjYCMkpGPi4qJi36LgBuPghSShRWWhCOShiuUiiuYjSubjiqdkS'
        'uglCujlSullSqlliqmlimmliqmlyWomhiumRislRyokXSUjWKSiyuZhyqUhCuRgyCRgxWTfxSP'
        'fB+KkIqPkpKQi4uJiomHg2uFgBuPgRSShRWViBaZihacjRafjxehkhekkxellRiolhiolhiplx'
        'iplxiplhiplRiokxykkGmUl4qXkT2dixadiBaahRWWghWTfxSPex2Ji3qMkI+Nj4+NiYmGkJCN'
        'iomIiG+JgUKJhUSNikeTiUWSiUKSikKUjEKWjEGWjkKZkUWckEOakEObj0KajUCXj0OalHCXmI'
        'yXmY+YlH6UjUiWiUaSg0KMg0SLgkWKh3aHiomHioqIj4+Mjo6MjIyJiYmGiYmHi4mIi4iJi4iK'
        'jomNiYSIh4CGh3+GiYCIi4KKkYeQlYuUlYuUjoONk4mSlYuUnZScm5Oal5CWlpCVkYyPjYmLiY'
        'aHiIaFj46Ni4qIkJCNioqHj4+M'
    ),
    'purple_basewall': (
        'amBXZFpOYFZKZltRYllPYVlQX1ZOWlFJW1FGWk5BWk5BZllObGFVa19Sal5SZlxSZ15UYllPY1'
        'pQZFpQYllOYVhOYFhQYVdPXFJHYldMXVJIZltQY1dMbGBVbWFWa2FYZV1UY1pRZ15UZFtSYFlQ'
        'YVhPYllQX1VMX1RJZFlOY1dLZVlPY1ZLZllNZFhOZ19XZFtSZFtSZVxRZ11TZ19WaWFZZV1UYl'
        'hPY1lPZFpPZFhOZFhOYVRJYlVKYldLZVxUbV5jaVlbZFRUY1JWaFlealteZlhbZFVXYVFTXk5O'
        'ZVNSaFZWaFVXa1lcaFZXa1pfckx6ckt1cUp0a0ZvcEp4akZwa0dvZEFmYT5fZ0FjZ0FjakNlck'
        'lxdUt2c0p0b0l2ckt2cUl0a0RoaERqbkhzakVuakVuYj9iYT5fZT9hYj1aakNlcUlxb0dsdEtz'
        'bkl0ckt4cUhwaEJlZkJlbEdwZkNoZkNpY0BiXjxbXTpWXztWZkFhb0dsbERmb0dsbUdwbUhwbU'
        'ZsZkBhZkFmakVtaERrY0BkY0BkXz1cXTpUXDlTYj1aZUBgZ0Bga0RnakVraENraEJkYz5cZUFj'
        'Z0NpaERpY0BlXz5eXz1cWDZOWTZOXDlSXjtWYT1XZkBfaUVra0ZvZEBgXjpVZUFkaERqaERqYj'
        '9iXjxdWjlVVDRLWDdOXTpVXTpVWTdOaENjZ0NoYlZOYVNKW01DWk1FXlNNW09JV0tEVUg/VUc+'
        'UEM5Vkg+W05EYlRLY1RJYVNJYFVMY1hOYVVKXVJHXVJJXVNIV09GVk5EVEk+TkM4VEc9V0tAWE'
        'xBV0xBXVFFWk5DYFVMYFdNYlhPYVhOYlhPYlhPVExEV09HVExCVEtCV01BWU5CWE1DW1FGXFFG'
        'YlVLZFtRaF5VYVdOXVRKY1pPY1lPVU5FWVFIVU1FV05GW05EXlRJXVFGXVJGXFBFX1RJY1pSaW'
        'BWZ1xRYFRJZlxSaV9WWlNKWFBIWlBHXFFGWU1BYFRHZlpOZltPXlFEX1RIX1ZN'
    ),
}
_DEFAULT_TEX = {}


BASE_TEAM_NAMES = ('', 'red', 'green', 'blue', 'purple')  # indexed by a base's team number (1 = red ... 4 = purple)


def default_texture_name(kind, face, team=0):
    """The texture BZFlag puts on a face when no material is given: boxes and arcs have boxwall walls and a roof
    top and bottom, pyramids are pyrwall, cones are boxwall, and a base has its team's basetop on the top and
    bottom and its team's basewall on the sides. None for everything else."""
    if kind == 'base' and 1 <= team <= 4: return BASE_TEAM_NAMES[team] + ('_basetop' if face in ('top', 'bottom') else '_basewall')
    if kind in ('box', 'arc'): return 'roof' if face in ('top', 'bottom') else 'boxwall'
    if kind == 'pyramid': return 'pyrwall'
    if kind == 'cone': return 'boxwall'
    return None


def default_texture(name):
    """The built-in square texture called name (16 or 32 pixels a side), as a rows x columns x 3 array."""
    if name not in _DEFAULT_TEX:
        raw = np.frombuffer(base64.b64decode(''.join(DEFAULT_TEX_DATA[name])), dtype=np.uint8)
        side = math.isqrt(len(raw) // 3)
        _DEFAULT_TEX[name] = raw.reshape(side, side, 3)
    return _DEFAULT_TEX[name]


TEX_MAX_SIDE = 16  # a texture kept for the 3D preview is shrunk until its longer side is at most this many pixels
TEX_TILE = 10.0    # world units one repeat of a texture covers in the preview (an approximation of BZFlag's mapping)


def fit_side(w, h, side=TEX_MAX_SIDE):
    """Size an image shrinks to so its longer side is at most `side` pixels, keeping its shape
    (1000 x 1600 becomes 10 x 16). Never enlarges."""
    if max(w, h) <= side: return max(w, 1), max(h, 1)
    k = side / max(w, h)
    return max(1, round(w * k)), max(1, round(h * k))


def micro_texture(img, side=TEX_MAX_SIDE):
    """A tiny RGB copy of a QImage as a numpy array (rows x columns x 3), for the 3D preview. Keeping only
    this, never the full picture, caps a texture at about a kilobyte however large the file was."""
    if img.isNull(): return None
    w, h = fit_side(img.width(), img.height(), side)
    small = img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    small = small.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = small.constBits(); ptr.setsize(small.sizeInBytes())
    return np.frombuffer(ptr, dtype=np.uint8).reshape(h, small.bytesPerLine() // 4, 4)[:, :w, :3].copy()


def planar_uvs(tri, tile=TEX_TILE):
    """Texture coordinates for a triangle of three (x, y, z) points: flat faces map by their ground position,
    walls run along their own horizontal direction with the picture upright. Texture units, not pixels."""
    (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = tri
    ax, ay, az = x1 - x0, y1 - y0, z1 - z0; bx, by, bz = x2 - x0, y2 - y0, z2 - z0
    nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
    hor = math.hypot(nx, ny)
    if abs(nz) >= hor or hor < 1e-9: return [(p[0] / tile, p[1] / tile) for p in tri]  # floor or roof
    tx, ty = -ny / hor, nx / hor
    return [((p[0] * tx + p[1] * ty) / tile, -p[2] / tile) for p in tri]


def span_uvs(tri, origin, eu, ev):
    """Texture coordinates that stretch the picture exactly once across a rectangle: origin is its corner (x, y) and
    eu / ev are the two edge vectors leaving that corner."""
    uu = eu[0] * eu[0] + eu[1] * eu[1] or 1.0; vv = ev[0] * ev[0] + ev[1] * ev[1] or 1.0
    return [(((p[0] - origin[0]) * eu[0] + (p[1] - origin[1]) * eu[1]) / uu,
             ((p[0] - origin[0]) * ev[0] + (p[1] - origin[1]) * ev[1]) / vv) for p in tri]


def raster_triangle(zbuf, cbuf, pts, depths, rgba, tex=None, uvs=None, shade=1.0):
    """Fill zbuf/cbuf in place with this triangle wherever it's the nearest thing seen so
    far at that pixel. pts: ((x0,y0),(x1,y1),(x2,y2)) screen coords; depths: (z0,z1,z2),
    smaller = nearer the camera. This replaces sorting whole faces back-to-front (which is
    what let a long face's single average position misplace it relative to other objects)
    with a genuine per-pixel depth test, so draw order stops mattering entirely.
    With tex (a small rows x columns x 3 array) and uvs (one (u, v) per corner) the pixels take
    their colour from the texture, repeating it, shaded by `shade`, instead of the flat rgba.
    """
    h, w = zbuf.shape
    (x0, y0), (x1, y1), (x2, y2) = pts
    minx = max(int(math.floor(min(x0, x1, x2))), 0); maxx = min(int(math.ceil(max(x0, x1, x2))), w - 1)
    miny = max(int(math.floor(min(y0, y1, y2))), 0); maxy = min(int(math.ceil(max(y0, y1, y2))), h - 1)
    if minx > maxx or miny > maxy: return
    denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(denom) < 1e-9: return  # edge-on / degenerate triangle
    # A row and a column broadcast against each other: same numbers as a full meshgrid, without building one
    xs = np.arange(minx, maxx + 1, dtype=np.float32)
    ys = np.arange(miny, maxy + 1, dtype=np.float32)[:, None]
    w0 = ((y1 - y2) * (xs - x2) + (x2 - x1) * (ys - y2)) / denom
    w1 = ((y2 - y0) * (xs - x2) + (x0 - x2) * (ys - y2)) / denom
    w2 = 1.0 - w0 - w1
    inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
    if not inside.any(): return
    depth = w0 * depths[0] + w1 * depths[1] + w2 * depths[2]
    zslice = zbuf[miny:maxy + 1, minx:maxx + 1]
    nearer = inside & (depth < zslice)
    if not nearer.any(): return
    zslice[nearer] = depth[nearer]
    block = cbuf[miny:maxy + 1, minx:maxx + 1]
    if tex is None or uvs is None:
        block[nearer] = rgba
        return
    # Perspective-correct texture coordinates: u/z, v/z and 1/z vary evenly across the screen
    i0, i1, i2 = 1.0 / depths[0], 1.0 / depths[1], 1.0 / depths[2]
    a0, a1, a2 = w0[nearer] * i0, w1[nearer] * i1, w2[nearer] * i2
    inv = a0 + a1 + a2
    u = (a0 * uvs[0][0] + a1 * uvs[1][0] + a2 * uvs[2][0]) / inv
    v = (a0 * uvs[0][1] + a1 * uvs[1][1] + a2 * uvs[2][1]) / inv
    th, tw = tex.shape[:2]
    texel = tex[np.floor(v * th).astype(np.int64) % th, np.floor(u * tw).astype(np.int64) % tw]
    block[nearer, :3] = (texel * shade).astype(np.uint8)
    block[nearer, 3] = rgba[3]


def edge_shade(zbuf, cbuf, rel=0.05, dark=0.62):
    """Darken the farther side of every sharp step in depth, like the contact shadow where one thing
    stands in front of another. Works on the finished depth buffer, so it costs a few array operations
    and no lighting maths. Steps against empty background are left alone."""
    h, w = zbuf.shape
    if h < 2 or w < 2: return
    fin = np.isfinite(zbuf); z = np.where(fin, zbuf, 0.0); dark_px = np.zeros((h, w), dtype=bool)
    for (a, b_, both, sa, sb) in (
            (z[:, :-1], z[:, 1:], fin[:, :-1] & fin[:, 1:], (slice(None), slice(None, -1)), (slice(None), slice(1, None))),
            (z[:-1, :], z[1:, :], fin[:-1, :] & fin[1:, :], (slice(None, -1), slice(None)), (slice(1, None), slice(None)))):
        step = both & (np.abs(a - b_) > rel * np.minimum(a, b_))
        dark_px[sa] |= step & (a > b_)  # the farther pixel of the pair is the one in shadow
        dark_px[sb] |= step & (b_ > a)
    if dark_px.any(): cbuf[dark_px, :3] = (cbuf[dark_px, :3] * dark).astype(np.uint8)


def choose_lod(ms_full, ms_half, budget=70.0):
    """Resolution to draw at while the view is being moved: 1 (full) or 2 (half). Dropping resolution only
    pays when pixels are what the frame costs; on a map of many small shapes it barely helps, so it is used
    only when the full-size frame is slow and the half-size one measurably faster. None = not measured yet."""
    if ms_full is not None and ms_full <= budget: return 1
    if ms_full is not None and ms_half is not None and ms_half > 0.85 * ms_full: return 1
    return 2


class Preview(QWidget):
    LIGHT = (.4, -.5, .77)

    def __init__(s, m):
        super().__init__()
        s.m, s.yaw, s.pitch, s.dist, s.last, s.act, s.space = m, .5, .55, 1100., None, None, False
        # _lod > 1 = render at 1/_lod resolution while orbiting/dragging (big speed-up on complex maps)
        s._lod, s._rw, s._rh, s._hires_timer = 1, None, None, QTimer(s)
        s.edge_shading, s.show_textures = True, True  # View menu; Win sets them from the saved settings
        s._faceuv = {}  # id of a face colour -> (origin, edge u, edge v) when its texture should span the face once (base tops)
        s._deftex = {}  # id of a face colour -> built-in default texture name (faces with no material)
        s._ms, s._matof = {}, {}  # recent frame cost (ms) at each resolution; material name of each face's colour
        s._hires_timer.setSingleShot(True)
        s._hires_timer.timeout.connect(s._finish_lod)
        s.setMinimumSize(320, 240); s.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
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
        w = s._rw if s._rw is not None else s.width()
        h = s._rh if s._rh is not None else s.height()
        k = h * .9 / zc
        return QPointF(w / 2 + sum(d[i] * r[i] for i in range(3)) * k,
                       h / 2 - sum(d[i] * u[i] for i in range(3)) * k)

    def _begin_lod(s):
        """Drop to half-res while the user is orbiting or dragging, but only when that measurably helps
        (see choose_lod); full-res resumes shortly after."""
        if s._lod == 1:
            s._lod = choose_lod(s._ms.get(1), s._ms.get(2))
        s._hires_timer.stop()

    def _finish_lod(s):
        if s._lod != 1:
            s._lod = 1
            s.update()

    def _schedule_hires(s):
        s._hires_timer.start(120)  # ms of idle before a full-resolution redraw

    def solids(s):
        """Return (points, color, source_obj, texinfo) faces, texinfo=None or (pixmap, uv0..uv3)."""
        F = []; s._matof = {}; s._deftex = {}; s._faceuv = {}
        for leaf, src in expand(s.m):
            o = leaf
            if o.t == 'mesh':
                F += s.mesh_faces(o, src); continue
            a = math.radians(o.r); c, sn = math.cos(a), math.sin(a)
            P = lambda lx, ly, z: (o.x + lx * c - ly * sn, o.y + lx * sn + ly * c, z)
            col = QColor(TEAM[o.team] if o.t == 'base' else COL.get(o.t, '#8d939a'))
            C = lambda face, o=o, col=col: s.face_color(o, face, col)
            z0 = o.z; z1 = z0 + max(o.sz, .3)
            if o.t == 'zone':
                if s.m.show_zones:
                    F += s.zone_faces(o, P, z1)
                continue
            if o.t == 'arc':  # the ellipse is only turned by enclosing groups; o.r is where its corners start (see shape_turn)
                ta = math.radians(o.trot); ct, st = math.cos(ta), math.sin(ta)
                PA = lambda lx, ly, z: (o.x + lx * ct - ly * st, o.y + lx * st + ly * ct, z)
                F += s.arc_faces(o, PA, z0, z1, C); continue
            if o.t == 'cone':
                F += s.cone_faces(o, P, z0, z1, C('')); continue
            if o.t == 'pyramid':
                apex_z, base_z = (z0, z1) if o.flipz else (z1, z0)  # flipz: point down instead of up
                b = [P(-o.sx, -o.sy, base_z), P(o.sx, -o.sy, base_z), P(o.sx, o.sy, base_z), P(-o.sx, o.sy, base_z)]
                ap = P(0, 0, apex_z)
                F += [([b[i], b[(i + 1) % 4], ap], C(''), o, None) for i in range(4)] + [(b, C(''), o, None)]
            else:
                b = [P(-o.sx, -o.sy, z0), P(o.sx, -o.sy, z0), P(o.sx, o.sy, z0), P(-o.sx, o.sy, z0)]
                t = [(x, y, z1) for x, y, _ in b]
                cb_, ct_ = C('bottom'), C('top')
                if o.t == 'base':  # a base's top and bottom picture covers the whole face once, turning with the base
                    fr = (b[0][:2], (b[1][0] - b[0][0], b[1][1] - b[0][1]), (b[3][0] - b[0][0], b[3][1] - b[0][1]))
                    s._faceuv[id(cb_)] = s._faceuv[id(ct_)] = fr
                F.append((b, cb_, o, None)); F.append((t, ct_, o, None))
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
                s._matof[id(col)] = mat
            out.append((pts, col, src, None))
        return out

    def zone_faces(s, o, P, ztop):
        """Zones have no appearance in the actual game -- this is purely an editor aid, so it's
        drawn as a thin, translucent, checkered top surface (one color per spawning team) rather
        than a solid box, to make clear it isn't real geometry. Reuses the existing flat-color
        rasterizer by subdividing into small flat cells instead of needing real texture support."""
        colors = zone_colors(o)
        nx, ny, cw, ch = zone_grid(o.sx, o.sy)
        F = []
        for ix in range(nx):
            for iy in range(ny):
                col = QColor(colors[(ix + iy) % len(colors)]); col.setAlpha(110)
                x0, y0 = -o.sx + ix * cw, -o.sy + iy * ch
                quad = [P(x0, y0, ztop), P(x0 + cw, y0, ztop), P(x0 + cw, y0 + ch, ztop), P(x0, y0 + ch, ztop)]
                F.append((quad, col, o, None))
        return F

    def face_color(s, o, face, default):
        """Color of one face: its own material, else the object's all-faces material, else default."""
        name = o.mats.get(face) or o.matref
        if name in s.m.materials:
            c = s.m.materials[name].color
            col = QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255), int(c[3] * 255))
            s._matof[id(col)] = name  # lets the renderer find this face's texture
            return col
        kind = default_texture_name(o.t, face, o.team)
        if kind:  # no material: BZFlag's default texture for this kind of face (a copy, so each face can differ)
            col = QColor(default.red(), default.green(), default.blue(), default.alpha())
            s._deftex[id(col)] = kind
            return col
        return default

    def arc_faces(s, o, P, z0, z1, C):
        N, A, ratio = max(3, o.divisions), o.angle, o.ratio
        full = A >= 360
        pts = N + 1 if not full else N
        ph = math.radians(arc_phase(o))
        ang = lambda i: ph + math.radians(A * i / N)
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
                F.append(([cb, V(outer[j], z0), V(outer[i], z0)], C('bottom'), o, None))
                F.append(([ct, V(outer[i], z1), V(outer[j], z1)], C('top'), o, None))
            if not full:
                F.append(([cb, V(outer[0], z0), V(outer[0], z1), ct], C('startside'), o, None))
                F.append(([ct, V(outer[-1], z1), V(outer[-1], z0), cb], C('endside'), o, None))
        else:
            ir = 1 - ratio
            inner = [P(o.sx * ir * math.cos(ang(i)), o.sy * ir * math.sin(ang(i)), 0) for i in range(pts)]
            for i in range(segs):
                j = (i + 1) % pts
                F.append(([V(inner[i], z0), V(inner[j], z0), V(outer[j], z0), V(outer[i], z0)], C('bottom'), o, None))
                F.append(([V(outer[i], z1), V(outer[j], z1), V(inner[j], z1), V(inner[i], z1)], C('top'), o, None))
                F.append(([V(inner[i], z0), V(inner[i], z1), V(inner[j], z1), V(inner[j], z0)], C('inside'), o, None))
            if not full:
                F.append(([V(inner[0], z0), V(outer[0], z0), V(outer[0], z1), V(inner[0], z1)], C('startside'), o, None))
                F.append(([V(inner[-1], z1), V(outer[-1], z1), V(outer[-1], z0), V(inner[-1], z0)], C('endside'), o, None))
        return F

    def cone_faces(s, o, P, z0, z1, col):
        N = max(3, o.divisions)
        ring = [P(o.sx * math.cos(2 * math.pi * i / N), o.sy * math.sin(2 * math.pi * i / N), 0) for i in range(N)]
        apex = P(0, 0, z1); ctr = P(0, 0, z0)
        F = []
        for i in range(N):
            j = (i + 1) % N
            F.append(([(ring[i][0], ring[i][1], z0), (ring[j][0], ring[j][1], z0), apex], col, o, None))
            F.append(([ctr, (ring[j][0], ring[j][1], z0), (ring[i][0], ring[i][1], z0)], col, o, None))
        return F

    def render_buffers(s, w_px, h_px, B):
        """Rasterize the map into a depth buffer and an RGBA colour buffer of w_px x h_px, with no Qt painting
        involved. Returns (zbuf, cbuf, faces of the selected object). Each vertex of a face is projected once,
        however many of the face's triangles share it."""
        pos, f, r_, u_ = B; L = math.sqrt(sum(x * x for x in s.LIGHT))
        px, py, pz = pos; fx, fy, fz = f; rx, ry, rz = r_; ux, uy, uz = u_
        half_w, half_h, kk = w_px / 2, h_px / 2, h_px * .9
        zbuf = np.full((h_px, w_px), np.inf, dtype=np.float32)
        cbuf = np.zeros((h_px, w_px, 4), dtype=np.uint8)
        sel_faces = []  # original (untriangulated) faces of the selected object, for its outline
        light = s.LIGHT; materials = s.m.materials
        for pts, col, o, tex in s.solids():
            if o is s.m.sel: sel_faces.append(pts)
            opaque = (col.alpha() or 255) >= 250
            texarr = None
            if s.show_textures:
                name = s._matof.get(id(col))
                if name and name in materials: texarr = s.m.texture_array(materials[name].texture)
                elif id(col) in s._deftex: texarr = default_texture(s._deftex[id(col)])
            n = len(pts)
            corners = ((0, 1, 2),) if n <= 3 else [(0, i, i + 1) for i in range(1, n - 1)]
            proj = {}
            for ia, ib, ic in corners:
                a, b_, c = pts[ia], pts[ib], pts[ic]
                # Back-face cull (opaque only). Camera looks along +f; outward normal n_ facing
                # the same way as f means the face is facing away and can be skipped.
                u = (b_[0] - a[0], b_[1] - a[1], b_[2] - a[2]); w_ = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
                n_ = (u[1] * w_[2] - u[2] * w_[1], u[2] * w_[0] - u[0] * w_[2], u[0] * w_[1] - u[1] * w_[0])
                if opaque and n_[0] * fx + n_[1] * fy + n_[2] * fz > 0: continue
                screen = []
                for i in (ia, ib, ic):
                    if i not in proj:
                        q = pts[i]; dx, dy, dz = q[0] - px, q[1] - py, q[2] - pz
                        zc = dx * fx + dy * fy + dz * fz
                        proj[i] = None if zc < 5 else (half_w + (dx * rx + dy * ry + dz * rz) * (kk / zc),
                                                       half_h - (dx * ux + dy * uy + dz * uz) * (kk / zc), zc)
                    screen.append(proj[i])
                if None in screen: continue  # a vertex behind the camera: skip
                nl = math.sqrt(n_[0] * n_[0] + n_[1] * n_[1] + n_[2] * n_[2]) or 1
                lit = abs(n_[0] * light[0] + n_[1] * light[1] + n_[2] * light[2]) / (nl * L)
                k = .5 + .5 * lit
                rgba = (int(col.red() * k), int(col.green() * k), int(col.blue() * k), col.alpha() or 255)
                xy = [(q[0], q[1]) for q in screen]; zz = [q[2] for q in screen]
                # a texture is only lightly shaded (down to 75%) so its colours stay close to the picture's own
                if texarr is not None:
                    fr = s._faceuv.get(id(col))
                    uvs = span_uvs((a, b_, c), *fr) if fr else planar_uvs((a, b_, c))
                    raster_triangle(zbuf, cbuf, xy, zz, rgba, texarr, uvs, .75 + .25 * lit)
                else: raster_triangle(zbuf, cbuf, xy, zz, rgba)
        if s.edge_shading: edge_shade(zbuf, cbuf)
        return zbuf, cbuf, sel_faces

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
        t0 = time.perf_counter()
        lod = max(1, int(s._lod))
        full_w, full_h = max(1, s.width()), max(1, s.height())
        w_px, h_px = max(1, full_w // lod), max(1, full_h // lod)
        s._rw, s._rh = w_px, h_px  # proj() uses these while we rasterize at reduced size
        zbuf, cbuf, sel_faces = s.render_buffers(w_px, h_px, B)
        s._rw = s._rh = None
        img = QImage(cbuf.data, w_px, h_px, w_px * 4, QImage.Format.Format_RGBA8888).copy()
        if lod > 1:
            img = img.scaled(full_w, full_h, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.FastTransformation)
        ms = (time.perf_counter() - t0) * 1000  # remembered so choose_lod can tell whether pixelating pays
        if lod in (1, 2): s._ms[lod] = ms if lod not in s._ms else .6 * s._ms[lod] + .4 * ms
        p.drawImage(0, 0, img)
        for pts in sel_faces:  # outline the selected object on top of the rasterized image
            pg = poly(pts)
            if pg: p.setPen(QPen(ACC.lighter(150), 2)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPolygon(pg)
        for o in s.m.msel:  # small marker for Ctrl-selected objects, same idea as the 2D view's dot
            mark = s.proj(B, (o.x, o.y, o.z + max(o.sz, .3)))
            if mark:
                p.setPen(QPen(QColor('#c8453b'), 2)); p.setBrush(QColor('#c8453b'))
                p.drawEllipse(mark, 5, 5)
        s.draw_trajectories(p, B)
        s.draw_weapons(p, B)
        p.setPen(QColor('#98a0a8'))
        p.drawText(10, s.height() - 10, 'Click/drag: select, move, place \u00b7 Shift+drag: rotate selected \u00b7 '
                                        'Right/middle-drag or Space+drag: orbit \u00b7 Scroll: zoom')
        s.draw_gizmo(p, B)

    def draw_weapons(s, p, B):
        """Weapons have no appearance in the real game -- an arrow along the aim direction
        (rotation, tilted by the vertical aim) shows where it actually points."""
        for leaf, src in expand(s.m):
            if leaf.t != 'weapon': continue
            a = math.radians(leaf.r); t = math.radians(leaf.tilt); L = 25
            tip = (leaf.x + L * math.cos(a) * math.cos(t), leaf.y + L * math.sin(a) * math.cos(t), leaf.z + L * math.sin(t))
            base, tipscr = s.proj(B, (leaf.x, leaf.y, leaf.z)), s.proj(B, tip)
            if not base or not tipscr: continue
            c = QColor(TEAM[leaf.wcolor] if 1 <= leaf.wcolor <= 4 else COL['weapon'])
            p.setPen(QPen(c, 2.5)); p.drawLine(base, tipscr)
            p.setBrush(c); p.drawEllipse(base, 4, 4)

    def draw_trajectories(s, p, B):
        """For every object whose physics driver launches tanks upward, draw the predicted
        flight path and landing point -- physically computed, not just a guess at direction."""
        for leaf, src in expand(s.m):
            if not leaf.phydrv: continue
            pd = s.m.physics.get(leaf.phydrv)
            if not pd or not pd.has_linear: continue
            start = (leaf.x, leaf.y, leaf.z + max(leaf.sz, .3))
            result = launch_trajectory(start, pd.linear)
            if not result: continue
            points, landing = result
            screen = [s.proj(B, pt) for pt in points]
            if any(pt is None for pt in screen): continue
            path = QPainterPath(); path.moveTo(screen[0])
            for pt in screen[1:]: path.lineTo(pt)
            p.setPen(QPen(QColor('#e0b84f'), 2, Qt.PenStyle.DashLine)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            land = s.proj(B, landing)
            if land:
                p.setPen(QPen(QColor('#e0b84f'), 2)); p.setBrush(QColor(224, 184, 79, 90))
                p.drawEllipse(land, 10, 10)
                dist = math.hypot(landing[0] - leaf.x, landing[1] - leaf.y)
                p.setPen(QColor('#e0b84f'))
                p.drawText(land + QPointF(12, 4), '%.0f units' % dist)

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

    # ---- editing directly in the 3D view -------------------------------------------
    # Left button/drag places, selects, moves and (Shift+drag) rotates objects, the same
    # jobs the 2D view's left button does. Orbiting the camera -- the 2D view's "pan" --
    # moves to the right or middle button, or Space+drag, so the two don't collide.
    def sn(s, v): return round(v / 5) * 5 if s.m.snap else v

    def ray(s, p):
        return screen_ray(s.basis(), p.x(), p.y(), s.width(), s.height())

    def pick(s, p):
        """Nearest object under the screen point p, or None. Mirrors Editor.hit()."""
        origin, direction = s.ray(p)
        best, best_t = None, None
        for leaf, src in expand(s.m):
            if leaf.t == 'mesh':
                W, _ = mesh_world(leaf)
                if not W: continue
                bmin = tuple(min(v[i] for v in W) for i in range(3))
                bmax = tuple(max(v[i] for v in W) for i in range(3))
                t = ray_aabb_hit(origin, direction, bmin, bmax)
            else:
                if leaf.t == 'group': hx, hy = s.m.group_bounds(leaf.name)
                elif leaf.t == 'weapon': hx, hy = 14, 6  # matches the arrow marker's drawn extent
                else: hx, hy = leaf.sx, leaf.sy
                t = ray_box_hit(origin, direction, leaf.x, leaf.y, leaf.z, hx, hy, leaf.sz, shape_turn(leaf))
            if t is not None and (best_t is None or t < best_t): best, best_t = src, t
        return best

    def mousePressEvent(s, e):
        s.setFocus(); b = e.button(); p = e.position()
        if b in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton) or s.space:
            s.last = p; s.act = None; return
        if b != Qt.MouseButton.LeftButton: return
        m = s.m
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier and m.sel:
            s.act = ('rotate', m.sel, m.sel.r, p.x()); m.push(); return
        if m.tool == 'select':
            ctrl = e.modifiers() & Qt.KeyboardModifier.ControlModifier
            o = s.pick(p)
            if ctrl and o:
                m.toggle_msel(o); s.act = None; return
            if not ctrl: m.msel = []
            m.select(o)
            if o: m.push(); s.act = ('move', o, o.z)
            else: s.act = None
        elif m.tool in ('box', 'pyramid', 'arc', 'cone', 'zone'):
            hit = ray_ground(*s.ray(p))
            if hit:
                a = (s.sn(hit[0]), s.sn(hit[1])); s.act = ('make', a, a, p)
        else:
            hit = ray_ground(*s.ray(p))
            if hit:
                m.push(); o = m.add(m.tool, s.sn(hit[0]), s.sn(hit[1])); m.make_family(o); m.select(o); m.changed.emit()
            s.act = None

    def mouseMoveEvent(s, e):
        p = e.position()
        if s.last is not None:  # orbiting
            s.yaw -= (p.x() - s.last.x()) * .008
            s.pitch = min(1.5, max(.05, s.pitch + (p.y() - s.last.y()) * .008)); s.last = p
            s._begin_lod(); s.update(); return
        a = getattr(s, 'act', None)
        if not a: return
        m = s.m
        if a[0] == 'move':
            _, o, z0 = a
            hit = ray_ground(*s.ray(p), z0)  # ray recomputed from the current mouse position each move
            if hit:
                o.x, o.y = s.sn(hit[0]), s.sn(hit[1])
                if m.sym and not o.fam: m.make_family(o)  # see Editor.mouseMoveEvent for why this is here
                m.sync_family(o); s._begin_lod(); m.changed.emit()
        elif a[0] == 'rotate':
            _, o, r0, x0 = a
            o.r = (r0 + (p.x() - x0) * .5) % 360
            m.sync_family(o); s._begin_lod(); m.changed.emit()
        elif a[0] == 'make':
            hit = ray_ground(*s.ray(p))
            if hit: s.act = (a[0], a[1], (s.sn(hit[0]), s.sn(hit[1])), a[3]); s._begin_lod(); s.update()

    def mouseReleaseEvent(s, e):
        a = getattr(s, 'act', None); s.act = None; s.last = None
        s._schedule_hires()
        if not a or a[0] != 'make': return
        (ax, ay), (bx, by), p0 = a[1], a[2], a[3]
        m = s.m; m.push(); o = m.add(m.tool, ax, ay)
        if math.hypot(e.position().x() - p0.x(), e.position().y() - p0.y()) > 4 and ax != bx and ay != by:
            o.x, o.y, o.sx, o.sy = (ax + bx) / 2, (ay + by) / 2, abs(ax - bx) / 2, abs(ay - by) / 2
        m.make_family(o); m.select(o); m.changed.emit()

    def wheelEvent(s, e):
        s.dist = min(8000, max(40, s.dist * (.9 if e.angleDelta().y() > 0 else 1 / .9)))
        s._begin_lod(); s.update(); s._schedule_hires()

    def keyPressEvent(s, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat(): s.space = True

    def keyReleaseEvent(s, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat(): s.space = False


def _qpath(poly):
    path = QPainterPath(); path.addPolygon(poly); return path


# ---------------------------------------------------------------------------
# Materials dialog
# ---------------------------------------------------------------------------
class ObjectInfoDialog(QDialog):
    """Name, category and description for a new library object."""

    def __init__(s, parent, title, name='', categories=(), description='', allow_split=False):
        super().__init__(parent); s.setWindowTitle(title); s.resize(440, 0)
        lay = QVBoxLayout(s); form = QFormLayout(); lay.addLayout(form)
        s.name = QLineEdit(name); form.addRow('Name', s.name)
        s.cat = QComboBox(); s.cat.setEditable(True)
        s.cat.addItems(sorted(set(categories) | {'My Objects'}, key=str.lower)); s.cat.setCurrentText('My Objects')
        form.addRow('Category', s.cat)
        s.desc = QLineEdit(description); s.desc.setPlaceholderText('optional, shown as a tooltip'); form.addRow('Description', s.desc)
        s.split = QCheckBox('The file holds several parts: make a separate object from each one')
        s.split.setVisible(allow_split); lay.addWidget(s.split)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(s.check); bb.rejected.connect(s.reject); lay.addWidget(bb)

    def check(s):
        if not s.name.text().strip():
            QMessageBox.information(s, 'Name needed', 'Please give the object a name.'); return
        s.accept()

    def values(s):
        return s.name.text().strip(), s.cat.currentText().strip() or 'My Objects', s.desc.text().strip(), s.split.isChecked()


class LibraryDialog(QDialog):
    """Browse the custom object library: search, pick one to place, or manage your own."""

    def __init__(s, win):
        super().__init__(win); s.win, s.items = win, []
        s.setWindowTitle('Custom Objects'); s.resize(540, 480)
        lay = QVBoxLayout(s)
        s.search = QLineEdit(); s.search.setPlaceholderText('Search by name, category or description')
        s.search.textChanged.connect(s.fill); lay.addWidget(s.search)
        s.list = QListWidget(); s.list.currentRowChanged.connect(s.show_info)
        s.list.itemDoubleClicked.connect(lambda _item: s.place()); lay.addWidget(s.list, 1)
        s.info = QLabel(); s.info.setWordWrap(True); lay.addWidget(s.info)
        r1 = QHBoxLayout(); lay.addLayout(r1)
        s.place_btn = QPushButton('Place in Map'); s.place_btn.clicked.connect(s.place); r1.addWidget(s.place_btn)
        s.remove_btn = QPushButton('Remove from My Objects'); s.remove_btn.clicked.connect(s.remove); r1.addWidget(s.remove_btn)
        r1.addStretch(1)
        r2 = QHBoxLayout(); lay.addLayout(r2)
        for label, fn in (('Import into My Objects...', lambda: (s.win.library_import(), s.fill())),
                          ('Save Selection to My Objects...', lambda: (s.win.library_save_selection(), s.fill())),
                          ('Open My Objects Folder', s.win.library_open_folder)):
            b = QPushButton(label); b.clicked.connect(lambda _c=False, fn=fn: fn()); r2.addWidget(b)
        r2.addStretch(1)
        close = QPushButton('Close'); close.clicked.connect(s.accept); r2.addWidget(close)
        s.fill()

    def fill(s):
        q = s.search.text().strip().lower()
        s.items = [i for i in scan_library() if q in ' '.join((i.name, i.category, i.description)).lower()]
        s.list.clear()
        for i in s.items: s.list.addItem('%s  /  %s%s' % (i.category, i.name, '' if i.builtin else '   (mine)'))
        if s.items: s.list.setCurrentRow(0)
        else: s.show_info(-1)

    def current(s):
        row = s.list.currentRow()
        return s.items[row] if 0 <= row < len(s.items) else None

    def show_info(s, _row=None):
        it = s.current()
        s.place_btn.setEnabled(bool(it)); s.remove_btn.setEnabled(bool(it) and not it.builtin)
        if not it:
            s.info.setText('No custom objects found. Use "Import into My Objects..." to add one.' if not s.items else '')
            return
        s.info.setText((it.description or 'No description.') + '\n' +
                       ('Comes with BZ Map Maker.' if it.builtin else 'In your own objects folder.'))

    def place(s):
        it = s.current()
        if it: s.win.pick_library(it); s.accept()

    def remove(s):
        it = s.current()
        if not it or it.builtin: return
        if QMessageBox.question(s, 'Remove object', 'Remove "%s" from your objects? Maps that already use it are not affected.' % it.name) \
                != QMessageBox.StandardButton.Yes: return
        try: os.remove(it.path)
        except OSError as e: QMessageBox.warning(s, 'Could not remove', str(e))
        _LIBCACHE.clear(); s.fill()


class OptionsDialog(QDialog):
    """The map's 'options' block: raw BZFS command-line text, written out exactly as typed.
    There's no real structure to this block (see wiki.bzflag.org/Options_(object)) -- it's
    whatever flags you'd otherwise pass to bzfs on the command line, including -set for any
    BZDB server variable -- so a plain text box is the most direct, complete way to edit it."""
    PLACEHOLDER = ('-set _tankSpeed 36\n-j +r -ms 3\n-sb -fb\n+f GM{5} +f SW{5}\n')

    def __init__(s, m):
        super().__init__(); s.m = m
        s.setWindowTitle('Map Options'); s.resize(520, 420)
        col = QVBoxLayout(s)
        note = QLabel('One option per line (or several separated by spaces), exactly as you would '
                      'pass them to bzfs. A few examples:\n'
                      '  +r            ricochet is on for every shot\n'
                      '  -j            allows jumping\n'
                      '  -sb  -fb      allows spawning and flags on top of buildings\n'
                      '  +f SE{2}      two Super flags are randomly placed on the map\n'
                      '  -set _thiefAdLife .2   sets a server variable (any value from the Server '
                      'Variables page)\n\n'
                      'Full option list: wiki.bzflag.org/BZFS_Command_Line_Options\n'
                      'Server variables: wiki.bzflag.org/Server_Variables')
        note.setWordWrap(True); col.addWidget(note)
        s.text = QPlainTextEdit(); s.text.setPlainText(m.options_text)
        s.text.setStyleSheet('font-family: monospace;'); s.text.setPlaceholderText(s.PLACEHOLDER)
        col.addWidget(s.text)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(s.save); bb.rejected.connect(s.reject); col.addWidget(bb)

    def save(s):
        s.m.push(); s.m.options_text = s.text.toPlainText(); s.m.changed.emit(); s.accept()


class LineGutter(QWidget):
    """The strip of line numbers beside a CodeEdit. It only asks the editor to paint it."""

    def __init__(s, ed):
        super().__init__(ed); s.ed = ed

    def sizeHint(s):
        return QSize(s.ed.gutter_width(), 0)

    def paintEvent(s, e):
        s.ed.paint_gutter(e)


class CodeEdit(QPlainTextEdit):
    """A plain text box with line numbers down the left side (the current line's number is bold)."""

    def __init__(s):
        super().__init__()
        s.gutter = LineGutter(s)
        s.blockCountChanged.connect(s.update_gutter_width)
        s.updateRequest.connect(s.update_gutter)
        s.cursorPositionChanged.connect(lambda: s.gutter.update())
        s.update_gutter_width()

    def gutter_width(s):
        return 14 + s.fontMetrics().horizontalAdvance('9') * max(2, len(str(max(s.blockCount(), 1))))

    def update_gutter_width(s, _count=0):
        s.setViewportMargins(s.gutter_width(), 0, 0, 0)

    def update_gutter(s, rect, dy):
        if dy: s.gutter.scroll(0, dy)
        else: s.gutter.update(0, rect.y(), s.gutter.width(), rect.height())
        if rect.contains(s.viewport().rect()): s.update_gutter_width()

    def resizeEvent(s, e):
        super().resizeEvent(e)
        cr = s.contentsRect(); s.gutter.setGeometry(QRect(cr.left(), cr.top(), s.gutter_width(), cr.height()))

    def paint_gutter(s, event):
        pal = s.palette(); p = QPainter(s.gutter)
        p.fillRect(event.rect(), pal.window().color())
        cur = s.textCursor().blockNumber(); fh = s.fontMetrics().height()
        block = s.firstVisibleBlock(); n = block.blockNumber()
        top = round(s.blockBoundingGeometry(block).translated(s.contentOffset()).top())
        bottom = top + round(s.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                f = p.font(); f.setBold(n == cur); p.setFont(f)
                p.setPen(pal.text().color() if n == cur else pal.mid().color())
                p.drawText(0, top, s.gutter.width() - 6, fh, Qt.AlignmentFlag.AlignRight, str(n + 1))
            block = block.next(); top = bottom; bottom = top + round(s.blockBoundingRect(block).height()); n += 1
        p.end()


def replace_all_text(doc, find, repl, flags, limit=200000):
    """Replace every match in a QTextDocument as a single undo step. Returns how many were replaced.
    Scanning resumes after each inserted text, so a replacement that contains the search text can't loop."""
    edit = QTextCursor(doc); edit.beginEditBlock(); n = 0; pos = 0
    try:
        while n < limit:
            c = doc.find(find, pos, flags)
            if c.isNull(): break
            c.insertText(repl); n += 1; pos = c.position()
    finally:
        edit.endEditBlock()
    return n


class CleanupDialog(QDialog):
    """Objects > Remove Unused: choose which leftovers to delete from the map."""
    KINDS = (('defines', 'Group definition'), ('materials', 'Material'), ('texmats', 'Texturematrix'), ('physics', 'Physics driver'))

    def __init__(s, m):
        super().__init__(); s.m = m
        s.setWindowTitle('Remove Unused'); s.resize(480, 420)
        lay = QVBoxLayout(s)
        s.intro = QLabel('Everything listed is saved into the map file but not used by anything in it. Untick anything you want to keep. '
                         'Undo (Ctrl+Z) brings it back.'); s.intro.setWordWrap(True); lay.addWidget(s.intro)
        s.list = QListWidget(); lay.addWidget(s.list, 1)
        s.empty = QLabel('Nothing to remove.'); lay.addWidget(s.empty)
        s.more = QCheckBox('Also list materials, texturematrix and physics drivers that nothing uses at all')
        s.more.toggled.connect(lambda _v: s.fill()); lay.addWidget(s.more)
        s.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        s.bb.button(QDialogButtonBox.StandardButton.Ok).setText('Remove Checked')
        s.bb.accepted.connect(s.accept); s.bb.rejected.connect(s.reject); lay.addWidget(s.bb)
        s.fill()

    def fill(s):
        plan = s.m.cleanup_plan(s.more.isChecked()); s.list.clear()
        for key, label in s.KINDS:
            for name in plan[key]:
                it = QListWidgetItem('%s:  %s' % (label, name)); it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked); it.setData(Qt.ItemDataRole.UserRole, (key, name)); s.list.addItem(it)
        s.empty.setVisible(s.list.count() == 0)

    def chosen(s):
        out = {k: [] for k, _l in s.KINDS}
        for i in range(s.list.count()):
            it = s.list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                key, name = it.data(Qt.ItemDataRole.UserRole); out[key].append(name)
        return out


class TexMatrixDialog(QDialog):
    """Objects > Texturematrix: named matrices that make a material's texture shift, spin or
    scale (fixed, or moving over time). A material uses one through its 'Texturematrix' field.
    Only the settings ticked here are written to the file."""
    # key, checkbox text, value prefixes, tooltip (from the BZFlag wiki's description of each parameter)
    ROWS = (('fixedshift', 'Fixed shift', ('u ', 'v '), 'Time-invariant shift: the texture doesn\'t move with time.'),
            ('fixedspin', 'Fixed spin', ('',), 'Time-invariant rotation: the texture doesn\'t move with time.'),
            ('fixedscale', 'Fixed scale', ('u ', 'v '), 'Time-invariant scale: the texture doesn\'t move with time.'),
            ('shift', 'Shift over time', ('u freq ', 'v freq '), 'The texture moves linearly: u and v frequencies.'),
            ('spin', 'Spin over time', ('freq ',), 'The texture spins around the center point: the rotational frequency.'),
            ('scale', 'Scale over time', ('u freq ', 'v freq ', 'u scale ', 'v scale '),
             'The texture dynamically changes size: u and v frequencies, then u and v scales.'),
            ('center', 'Center for spin and scale', ('u ', 'v '), 'The central point that spin and scale work around.'))

    def __init__(s, m):
        super().__init__(); s.m, s.cur, s.cb, s.sp = m, None, {}, {}
        s.setWindowTitle('Texturematrix'); s.resize(620, 480)
        root = QHBoxLayout(s)
        s.list = QListWidget(); s.list.addItems(sorted(m.texmats)); s.list.currentTextChanged.connect(s.pick)
        root.addWidget(s.list, 1)
        col = QVBoxLayout(); root.addLayout(col, 3)
        s.name = QLineEdit(); form = QFormLayout(); form.addRow('Name', s.name)
        for key, label, prefixes, tip in s.ROWS:
            cb = QCheckBox(label); cb.setToolTip(tip); row = QHBoxLayout(); spins = []
            for pre in prefixes:
                sp = QDoubleSpinBox(); sp.setRange(-1000, 1000); sp.setDecimals(3); sp.setSingleStep(.1)
                sp.setPrefix(pre); sp.setEnabled(False); sp.setToolTip(tip); row.addWidget(sp); spins.append(sp)
            cb.toggled.connect(lambda v, sps=spins: [x.setEnabled(v) for x in sps])
            s.cb[key], s.sp[key] = cb, spins; form.addRow(cb, row)
        col.addLayout(form)
        btns = QHBoxLayout()
        add = QPushButton('Add / Update'); add.clicked.connect(s.save)
        rm = QPushButton('Remove'); rm.clicked.connect(s.remove)
        btns.addWidget(add); btns.addWidget(rm); col.addLayout(btns)
        note = QLabel('Give a material one with its "Texturematrix" field in Objects > Materials (the material also needs a '
                      'texture). The 3D preview shows textures as small approximations and doesn\'t animate them, so you\'ll see the movement in BZFlag itself.')
        note.setWordWrap(True); col.addWidget(note); col.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); bb.rejected.connect(s.accept); bb.accepted.connect(s.accept)
        col.addWidget(bb)

    def pick(s, name):
        s.cur = s.m.texmats.get(name)
        if not s.cur: return
        s.name.setText(s.cur.name)
        for key, _n in TEXMAT_FIELDS:
            v = getattr(s.cur, key); s.cb[key].setChecked(v is not None)
            vals = v if isinstance(v, (tuple, list)) else ((v,) if v is not None else ())
            for sp, x in zip(s.sp[key], vals): sp.setValue(x)
            if v is None:
                for sp in s.sp[key]: sp.setValue(0)

    def values(s):
        out = {}
        for key, n in TEXMAT_FIELDS:
            if not s.cb[key].isChecked(): out[key] = None; continue
            v = tuple(sp.value() for sp in s.sp[key]); out[key] = v[0] if n == 1 else v
        return out

    def save(s):
        name = s.name.text().strip()
        if not name or any(c.isspace() for c in name):
            QMessageBox.information(s, 'Texturematrix', 'Give it a name with no spaces, such as scroll_left.'); return
        old = s.m.texmats.get(name); s.m.push()
        s.m.texmats[name] = TexMatrix(name, extra=list(old.extra) if old else [], **s.values())  # keep lines we don't edit
        if s.list.findItems(name, Qt.MatchFlag.MatchExactly) == []: s.list.addItem(name)
        s.m.changed.emit()

    def remove(s):
        name = s.name.text().strip()
        if name not in s.m.texmats: return
        users = [m.name for m in s.m.materials.values() if name in _texmat_names(m)]
        if users and QMessageBox.question(s, 'Remove texturematrix', '%s use%s "%s". Remove it anyway? (They keep pointing at it, '
                'and BZFlag will complain until you change them.)' % (', '.join(users), 's' if len(users) == 1 else '', name)) \
                != QMessageBox.StandardButton.Yes: return
        s.m.push(); del s.m.texmats[name]
        for it in s.list.findItems(name, Qt.MatchFlag.MatchExactly): s.list.takeItem(s.list.row(it))
        s.m.changed.emit()


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
        form.addRow('Texture', trow)
        s.texmat = QComboBox(); s.fill_texmat('')
        s.texmat.setToolTip('How the texture moves, spins or scales; define them in Objects > Texturematrix')
        form.addRow('Texturematrix', s.texmat); form.addRow('Color R G B A', rgb)
        col.addLayout(form)
        s.texwarn = QLabel(); s.texwarn.setWordWrap(True); s.texwarn.setStyleSheet('color:#b5651d')
        col.addWidget(s.texwarn)
        btns = QHBoxLayout()
        add = QPushButton('Add / Update'); add.clicked.connect(s.save)
        rm = QPushButton('Remove'); rm.clicked.connect(s.remove)
        fetch_all = QPushButton('Fetch All URL Textures'); fetch_all.clicked.connect(s.fetch_all)
        fetch_all.setToolTip('Internet textures load by themselves in the background; this tries again for any that failed (or loads them all if View > Load Remote Textures Automatically is off)')
        btns.addWidget(add); btns.addWidget(rm); btns.addWidget(fetch_all); col.addLayout(btns)
        note = QLabel('The 3D preview draws a tiny (16 pixel) version of a material\'s texture when it has the picture: a PNG with '
                       'the texture\'s name beside the map (or in a textures folder there), one you Load PNG, or an internet texture, which '
                       'loads by itself in the background (View > Load Remote Textures Automatically). Fetch All URL Textures '
                       'retries any that failed.\n'
                       'An addtexture line is read as the material\'s texture, the same as texture. Every other material setting (ambient...) '
                       'is kept in the file but not drawn.\nFor a texture on the internet, paste its full URL (e.g. ' + s.APPROVED +
                       'someone/name.png). The game only loads http://, so a pasted https:// link is switched '
                       'to http:// automatically.')
        note.setWordWrap(True); col.addWidget(note)
        col.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); bb.rejected.connect(s.accept); bb.accepted.connect(s.accept)
        col.addWidget(bb)

    def pick(s, name):
        s.cur = s.m.materials.get(name)
        if s.cur:
            s.name.setText(s.cur.name); s.tex.setText(s.cur.texture)
            s.fill_texmat((_texmat_names(s.cur) or [''])[0])
            for sp, v in zip((s.r, s.g, s.b, s.al), s.cur.color): sp.setValue(v)

    def fill_texmat(s, current):
        """The combo lists the map's texturematrix; one the material names but the map lacks is kept so it isn't lost."""
        names = sorted(set(s.m.texmats) | ({current} if current else set()))
        s.texmat.clear(); s.texmat.addItems(['(none)'] + names); s.texmat.setCurrentText(current or '(none)')

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
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(s, 'Load texture', "That file couldn't be read as an image."); return
        s.tex.setText(base); s.m.texcache[base] = micro_texture(img)  # keyed by texture name, so it doesn't matter when you name the material

    def save(s):
        name = s.name.text().strip()
        if not name: return
        old = s.m.materials.get(name)
        extra = [ln for ln in (old.extra if old else []) if ln.split()[:1] and ln.split()[0].lower() != 'texmat']  # keep addtexture etc.
        if s.texmat.currentText() != '(none)': extra.append('texmat ' + s.texmat.currentText())
        mat = Material(name, s.tex.text().strip(), (s.r.value(), s.g.value(), s.b.value(), s.al.value()), extra)
        s.m.materials[name] = mat
        if s.m.texcache.get(mat.texture, 0) is None: del s.m.texcache[mat.texture]  # not found before? look again now
        if s.list.findItems(name, Qt.MatchFlag.MatchExactly) == []: s.list.addItem(name)
        s.m.changed.emit()

    def fetch_all(s):
        """Download every internet texture the map's materials use, once, because you asked, so the 3D view can draw them."""
        urls = sorted({m.texture for m in s.m.materials.values() if is_remote_texture(m.texture)
                       and s.m.texcache.get(m.texture) is None and m.texture not in s.m._tex_pending})
        if not urls:
            QMessageBox.information(s, 'Fetch textures', 'Nothing to fetch: every internet texture in this map is loaded (or still loading in the background), or the map has none.'); return
        ok, bad = 0, []  # textures are normally fetched automatically as you work; this retries the ones that failed (or all of them if automatic loading is off)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for url in urls:
                try:
                    data = download_texture(url)
                    img = QImage()
                    if not img.loadFromData(data): raise ValueError('not an image')
                    s.m.texcache[url] = micro_texture(img); ok += 1
                except Exception: bad.append(url.rsplit('/', 1)[-1] or url)
        finally:
            QApplication.restoreOverrideCursor()
        s.m.changed.emit()
        QMessageBox.information(s, 'Fetch textures', 'Loaded %d of %d.' % (ok, len(urls)) + ((' Could not get: ' + ', '.join(bad) + '.') if bad else ''))

    def remove(s):
        name = s.name.text().strip()
        if name in s.m.materials:
            del s.m.materials[name]
            for it in s.list.findItems(name, Qt.MatchFlag.MatchExactly): s.list.takeItem(s.list.row(it))
            s.m.changed.emit()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class RadarHost(QWidget):
    """3D view fills the window; the 2D view sits on top as a small fixed, non-interactive
    minimap in a corner -- purely for orientation, since all editing happens in the 3D view."""
    CORNERS = {'tl': (True, True), 'tr': (False, True), 'bl': (True, False), 'br': (False, False)}

    def __init__(s):
        super().__init__(); s.ed = s.pv = None; s.corner = 'tl'; s.radar_size = 260

    def attach(s, ed, pv):
        s.ed, s.pv = ed, pv
        pv.setParent(s); ed.setParent(s)
        ed.setStyleSheet('border: 2px solid #666d74; background: #e9e7e0;')
        ed.raise_(); s.do_layout()

    def resizeEvent(s, e): s.do_layout()

    def do_layout(s):
        if not s.pv: return
        s.pv.setGeometry(0, 0, s.width(), s.height())
        left, top = s.CORNERS[s.corner]; m, sz = 10, s.radar_size
        x = m if left else max(m, s.width() - sz - m)
        y = m if top else max(m, s.height() - sz - m)
        s.ed.setGeometry(x, y, min(sz, s.width() - 2 * m), min(sz, s.height() - 2 * m)); s.ed.raise_()

    def set_corner(s, c):
        s.corner = c; s.do_layout()


class WorldDialog(QDialog):
    """The map's single 'world' block (see wiki.bzflag.org/World_(object)): name, size,
    flagHeight, noWalls and freeCtfSpawns. Size is the same value as the toolbar's world-size box."""

    def __init__(s, m):
        super().__init__(); s.m = m
        s.setWindowTitle('World Settings'); s.resize(460, 0)
        w = m.world
        col = QVBoxLayout(s); form = QFormLayout(); col.addLayout(form)
        s.name = QLineEdit(w['name']); s.name.setPlaceholderText('(unnamed -- generally unused by BZFlag)')
        form.addRow('Name', s.name)
        s.size = QDoubleSpinBox(); s.size.setRange(50, 5000); s.size.setSingleStep(50); s.size.setValue(m.W)
        s.size.setToolTip('Distance from the map center to each outer wall; the world is twice this across. Default 400.')
        form.addRow('Size (half-width)', s.size)
        s.use_fh = QCheckBox('Set flag height'); s.use_fh.setChecked(w['flagheight'] is not None)
        s.fh = QDoubleSpinBox(); s.fh.setRange(0, 1000); s.fh.setSingleStep(1)
        s.fh.setValue(w['flagheight'] if w['flagheight'] is not None else 10.0); s.fh.setEnabled(s.use_fh.isChecked())
        s.fh.setToolTip('Z clearance needed for a flag to spawn. Matters on maps with objects stacked in Z.')
        s.use_fh.toggled.connect(s.fh.setEnabled)
        form.addRow(s.use_fh); form.addRow('  Flag height', s.fh)
        s.nowalls = QCheckBox('No walls -- remove the outer walls (also the collision wall and the radar outline)')
        s.nowalls.setChecked(w['nowalls']); form.addRow(s.nowalls)
        s.freectf = QCheckBox('Free CTF spawns -- after a team flag is captured, players spawn randomly, not at the base')
        s.freectf.setChecked(w['freectf']); form.addRow(s.freectf)
        if w['extra']:
            note = QLabel('Also kept unchanged: ' + '; '.join(w['extra'])); note.setWordWrap(True); col.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(s.save); bb.rejected.connect(s.reject); col.addWidget(bb)

    def save(s):
        m = s.m; m.push()
        m.world = {'name': s.name.text().strip(),
                   'flagheight': s.fh.value() if s.use_fh.isChecked() else None,
                   'nowalls': s.nowalls.isChecked(), 'freectf': s.freectf.isChecked(),
                   'extra': list(m.world['extra']), 'rawlinks': list(m.world.get('rawlinks', []))}
        m.W = s.size.value()
        m.changed.emit(); s.accept()


class PhysicsDialog(QDialog):
    def __init__(s, m):
        super().__init__(); s.m, s.cur = m, None
        s.setWindowTitle('Physics Drivers'); s.resize(480, 420)
        root = QHBoxLayout(s)
        s.list = QListWidget(); s.list.addItems(sorted(m.physics)); s.list.currentTextChanged.connect(s.pick)
        root.addWidget(s.list, 1)
        col = QVBoxLayout(); root.addLayout(col, 2)
        s.name = QLineEdit()
        form = QFormLayout(); form.addRow('Name', s.name)

        s.use_lin = QCheckBox('Linear push (x y z) -- conveyors, trampolines')
        lin = QHBoxLayout(); s.lx, s.ly, s.lz = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
        for sp in (s.lx, s.ly, s.lz): sp.setRange(-500, 500); lin.addWidget(sp)
        form.addRow(s.use_lin); form.addRow('  Linear x y z', lin)

        s.use_ang = QCheckBox('Angular spin (rate, center x, center y) -- turntables')
        ang = QHBoxLayout(); s.ar, s.acx, s.acy = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
        s.ar.setRange(-50, 50); s.ar.setSingleStep(.1)
        for sp in (s.acx, s.acy): sp.setRange(-5000, 5000)
        for sp in (s.ar, s.acx, s.acy): ang.addWidget(sp)
        form.addRow(s.use_ang); form.addRow('  Rate, center x, y', ang)

        s.use_slide = QCheckBox('Slide -- a slippery surface (seconds to reach full speed)')
        s.slide = QDoubleSpinBox(); s.slide.setRange(0, 60); s.slide.setSingleStep(.1)
        form.addRow(s.use_slide); form.addRow('  Slide time', s.slide)

        s.use_death = QCheckBox('Death message -- kills the tank on contact (e.g. a landmine)')
        s.death = QLineEdit()
        form.addRow(s.use_death); form.addRow('  Message', s.death)
        col.addLayout(form)

        for cb, fields in ((s.use_lin, (s.lx, s.ly, s.lz)), (s.use_ang, (s.ar, s.acx, s.acy)),
                           (s.use_slide, (s.slide,)), (s.use_death, (s.death,))):
            cb.toggled.connect(lambda v, fs=fields: [f.setEnabled(v) for f in fs])
            for f in fields: f.setEnabled(False)

        btns = QHBoxLayout()
        add = QPushButton('Add / Update'); add.clicked.connect(s.save)
        rm = QPushButton('Remove'); rm.clicked.connect(s.remove)
        btns.addWidget(add); btns.addWidget(rm); col.addLayout(btns)
        note = QLabel('Assign a driver to a box, pyramid, arc, or cone with its "Physics driver" '
                      'field. Only the checked capabilities above are written to the file, so a '
                      'driver that\'s just slippery (Slide) doesn\'t also push or spin anything.')
        note.setWordWrap(True); col.addWidget(note); col.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); bb.rejected.connect(s.accept); bb.accepted.connect(s.accept)
        col.addWidget(bb)

    def pick(s, name):
        s.cur = s.m.physics.get(name)
        if not s.cur: return
        s.name.setText(s.cur.name)
        s.use_lin.setChecked(s.cur.has_linear); s.lx.setValue(s.cur.linear[0]); s.ly.setValue(s.cur.linear[1]); s.lz.setValue(s.cur.linear[2])
        s.use_ang.setChecked(s.cur.has_angular); s.ar.setValue(s.cur.angular[0]); s.acx.setValue(s.cur.angular[1]); s.acy.setValue(s.cur.angular[2])
        s.use_slide.setChecked(s.cur.has_slide); s.slide.setValue(s.cur.slide)
        s.use_death.setChecked(s.cur.has_death); s.death.setText(s.cur.death)

    def save(s):
        name = s.name.text().strip()
        if not name: return
        old = s.m.physics.get(name)
        pd = PhysicsDriver(name, (s.lx.value(), s.ly.value(), s.lz.value()), (s.ar.value(), s.acx.value(), s.acy.value()),
                           s.slide.value(), s.death.text(), s.use_lin.isChecked(), s.use_ang.isChecked(),
                           s.use_slide.isChecked(), s.use_death.isChecked(), list(old.extra) if old else [])
        s.m.physics[name] = pd
        if s.list.findItems(name, Qt.MatchFlag.MatchExactly) == []: s.list.addItem(name)
        s.m.changed.emit()

    def remove(s):
        name = s.name.text().strip()
        if name in s.m.physics:
            del s.m.physics[name]
            for it in s.list.findItems(name, Qt.MatchFlag.MatchExactly): s.list.takeItem(s.list.row(it))
            s.m.changed.emit()


class Win(QMainWindow):
    FIELDS = (('x', 'X'), ('y', 'Y'), ('z', 'Z'), ('sx', 'Half-width X'), ('sy', 'Half-width Y'),
              ('sz', 'Height'), ('r', 'Rotation'))

    def __init__(s, path=None):
        super().__init__(); s.m = Model(); s.resize(1340, 800)
        s.ed, s.pv = Editor(s.m), Preview(s.m)
        s.split_widget = QSplitter(); s.radar_widget = RadarHost()
        s.settings = QSettings('BZMapMaker', 'BZMapMaker')
        s.recent = [p for p in s.settings.value('recentFiles', []) or [] if isinstance(p, str)]
        s.busy = s.pushed = False; s.editing_member = None; s.build_menus(); s.build_panel(); s.statusBar()
        s.m.selected.connect(s.on_select); s.m.changed.connect(s.refresh)
        s.m.warn.connect(lambda msg: s.statusBar().showMessage(msg, 8000))
        s.radar_widget.set_corner(s.settings.value('radarCorner', 'tl'))
        s.set_layout(s.settings.value('layoutMode', 'split'))
        if path: s.open_file(path)
        s.title()

    def set_layout(s, mode):
        s.mode = mode
        s.takeCentralWidget()  # detach without deleting -- setCentralWidget() destroys whatever
        if mode == 'radar':     # widget it replaces unless you take it out first, which was the crash
            s.radar_widget.attach(s.ed, s.pv); s.setCentralWidget(s.radar_widget)
        else:
            s.split_widget.insertWidget(0, s.ed); s.split_widget.addWidget(s.pv)
            s.split_widget.setSizes([670, 670]); s.ed.setStyleSheet('')
            s.setCentralWidget(s.split_widget)
        s.settings.setValue('layoutMode', mode)
        s.ed.fit()  # the whole map should stay visible, whether it's a half-window pane or a small radar

    def set_corner(s, corner):
        s.radar_widget.set_corner(corner); s.settings.setValue('radarCorner', corner)

    def toggle_show_zones(s, on):
        s.m.show_zones = bool(on)
        s.settings.setValue('showZones', 'true' if on else 'false')
        s.m.changed.emit()  # refresh 2D + 3D views

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
        s.act(e, 'Group Selected (Ctrl+click or Shift+drag box to select)', s.do_group, 'Ctrl+G')
        ob = s.menuBar().addMenu('&Objects')
        s.act(ob, 'Materials...', s.open_materials)
        s.act(ob, 'Texturematrix...', s.open_texmats)
        s.act(ob, 'Import Mesh Object...', s.import_mesh)
        s.lib_menu = ob.addMenu('Custom Objects'); s.lib_menu.aboutToShow.connect(s.fill_library_menu)
        s.act(ob, 'World Settings...', s.open_world)
        s.act(ob, 'Map Options...', s.open_options)
        s.act(ob, 'Physics Drivers...', s.open_physics)
        ob.addSeparator(); s.act(ob, 'Remove Unused...', s.open_cleanup)
        s.cleanup_ask_action = QAction('Ask to Remove Unused When Saving', s); s.cleanup_ask_action.setCheckable(True)
        s.cleanup_ask_action.setChecked(s.settings.value('cleanupAtSave', 'ask') != 'never')
        s.cleanup_ask_action.toggled.connect(lambda on: s.settings.setValue('cleanupAtSave', 'ask' if on else 'never')); ob.addAction(s.cleanup_ask_action)
        vw = s.menuBar().addMenu('&View')
        lg = QActionGroup(s); cur_mode = s.settings.value('layoutMode', 'split')
        split_a = QAction('Side by Side', s); split_a.setCheckable(True); split_a.setChecked(cur_mode != 'radar')
        split_a.triggered.connect(lambda: s.set_layout('split')); lg.addAction(split_a); vw.addAction(split_a)
        radar_a = QAction('3D View with 2D Radar', s); radar_a.setCheckable(True); radar_a.setChecked(cur_mode == 'radar')
        radar_a.triggered.connect(lambda: s.set_layout('radar')); lg.addAction(radar_a); vw.addAction(radar_a)
        vw.addSeparator()
        cm = vw.addMenu('Radar Position'); cg = QActionGroup(s); cur_corner = s.settings.value('radarCorner', 'tl')
        for key, label in (('tl', 'Top Left'), ('tr', 'Top Right'), ('bl', 'Bottom Left'), ('br', 'Bottom Right')):
            ca = QAction(label, s); ca.setCheckable(True); ca.setChecked(key == cur_corner)
            ca.triggered.connect(lambda _, k=key: s.set_corner(k)); cg.addAction(ca); cm.addAction(ca)
        vw.addSeparator()
        show_z = QAction('Show Zones', s); show_z.setCheckable(True)
        show_z.setChecked(s.settings.value('showZones', 'true') != 'false')
        s.m.show_zones = show_z.isChecked()
        show_z.toggled.connect(s.toggle_show_zones); vw.addAction(show_z)
        for label, key, attr in (('Edge Shading in 3D', 'edgeShading', 'edge_shading'), ('Show Textures in 3D', 'showTextures', 'show_textures')):
            a = QAction(label, s); a.setCheckable(True); a.setChecked(s.settings.value(key, 'true') != 'false')
            setattr(s.pv, attr, a.isChecked()); a.toggled.connect(lambda on, key=key, attr=attr: s.set_pv_option(key, attr, on)); vw.addAction(a)
        rt = QAction('Load Remote Textures Automatically', s); rt.setCheckable(True)
        rt.setToolTip('Download the http:// textures a map uses in the background so the 3D view can show them')
        rt.setChecked(s.settings.value('remoteTextures', 'true') != 'false'); s.m.remote_textures = rt.isChecked()
        rt.toggled.connect(s.set_remote_textures); vw.addAction(rt)
        tb = s.addToolBar('Tools'); g = QActionGroup(s)
        for t in ('select', 'box', 'pyramid', 'base', 'teleporter', 'arc', 'cone', 'zone', 'weapon'):
            a = QAction(t.capitalize(), s); a.setCheckable(True); a.setChecked(t == 'select')
            a.triggered.connect(lambda _, t=t: setattr(s.m, 'tool', t)); g.addAction(a); tb.addAction(a)
        s.lib_action = QAction('Custom object', s); s.lib_action.setCheckable(True); s.lib_action.setEnabled(False)
        s.lib_action.setToolTip('The custom object chosen from Objects > Custom Objects')
        s.lib_action.triggered.connect(lambda _: setattr(s.m, 'tool', 'library')); g.addAction(s.lib_action); tb.addAction(s.lib_action)
        tb.addSeparator(); sn = QAction('Snap to 5', s); sn.setCheckable(True); sn.setChecked(True)
        sn.toggled.connect(lambda v: setattr(s.m, 'snap', v)); tb.addAction(sn)
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
        s.lfacec, s.bfacec = QComboBox(), QComboBox()  # which face of the target you come out of
        for cb in (s.lfacec, s.bfacec):
            cb.addItems(['its back', 'its front']); cb.setToolTip('Which side of the target teleporter you emerge from')
        s.lfacec.currentIndexChanged.connect(lambda i: s.edit('lface', 'b' if i == 0 else 'f'))
        s.bfacec.currentIndexChanged.connect(lambda i: s.edit('bface', 'b' if i == 0 else 'f'))
        s.link_row, s.blink_row = QWidget(), QWidget()
        for row, cbs in ((s.link_row, (s.link, s.lfacec)), (s.blink_row, (s.blink, s.bfacec))):
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.addWidget(cbs[0], 1); h.addWidget(cbs[1])
        s.div = QSpinBox(); s.div.setRange(3, 64); s.div.valueChanged.connect(lambda v: s.edit('divisions', v))
        s.ang = QDoubleSpinBox(); s.ang.setRange(1, 360); s.ang.valueChanged.connect(lambda v: s.edit('angle', v))
        s.rat = QDoubleSpinBox(); s.rat.setRange(0, .95); s.rat.setSingleStep(.05); s.rat.valueChanged.connect(lambda v: s.edit('ratio', v))
        s.mat = QComboBox(); s.mat.setEditable(True); s.mat.currentTextChanged.connect(lambda t: s.edit('matref', t))
        s.texsize = QLineEdit()
        s.texsize.setToolTip('How big one texture tile is. Box, pyramid and arc: 4 numbers, cone: 2 numbers. A negative number is the tile size in '
                             'world units, a positive one is relative to the object\'s size (1 1 1 1 = one texture per face). '
                             'Leave blank for BZFlag\'s default (nothing is written to the map).')
        s.texsize.editingFinished.connect(s.edit_texsize)
        s.phydrv_combo = QComboBox(); s.phydrv_combo.setEditable(True)
        s.phydrv_combo.currentTextChanged.connect(lambda t: s.edit('phydrv', t))
        s.flip = QCheckBox('Flip (point down)'); s.flip.toggled.connect(lambda v: s.edit('flipz', v))
        s.phys = QComboBox(); s.phys.addItems(['Normal', 'Drivethrough', 'Shootthrough', 'Passable'])
        s.phys.currentIndexChanged.connect(lambda i: s.edit('phys', ['normal', 'drivethrough', 'shootthrough', 'passable'][i]))
        s.zteam = QLineEdit(); s.zteam.setPlaceholderText('e.g. 0 1 2 3 4 (0=rogue, 1-4=team colors)')
        s.zteam.editingFinished.connect(lambda: s.edit('zteam', s._parse_ints(s.zteam.text(), 0, 4)))
        s.zsafety = QLineEdit(); s.zsafety.setPlaceholderText('e.g. 1 2 3 4')
        s.zsafety.editingFinished.connect(lambda: s.edit('zsafety', s._parse_ints(s.zsafety.text(), 1, 4)))
        s.zflags = QPlainTextEdit(); s.zflags.setMaximumHeight(90)
        s.zflags.setPlaceholderText("one per line, e.g.:\nflag L\nzoneflag GM 2\nSee bzflag.org/documentation/flags")
        s.zflags.textChanged.connect(lambda: s.edit('zflags', s.zflags.toPlainText()))
        s.ricochet = QCheckBox('Ricochet (shots always bounce off)')
        s.ricochet.toggled.connect(lambda v: s.edit('ricochet', v))
        s.wcolor = QComboBox(); s.wcolor.addItems(['Rogue'] + TEAM_NAMES[1:] + ['Observer', 'Rabbit'])
        s.wcolor.currentIndexChanged.connect(lambda i: s.edit('wcolor', i))
        s.tilt = QDoubleSpinBox(); s.tilt.setRange(-90, 90); s.tilt.valueChanged.connect(lambda v: s.edit('tilt', v))
        s.initdelay = QDoubleSpinBox(); s.initdelay.setRange(0, 3600); s.initdelay.setSuffix(' s')
        s.initdelay.valueChanged.connect(lambda v: s.edit('initdelay', v))
        s.wdelay = QLineEdit(); s.wdelay.setPlaceholderText('reload times in seconds, e.g. 5 3 3 (cycles through the list)')
        s.wdelay.editingFinished.connect(lambda: s.edit('delay', s._parse_floats(s.wdelay.text())))
        s.wtype = QLineEdit(); s.wtype.setPlaceholderText('flag it fires, e.g. SW (see bzflag.org/documentation/flags)')
        s.wtype.editingFinished.connect(lambda: s.edit('wtype', s.wtype.text().strip()))
        s.trigger = QComboBox(); s.trigger.addItems(['Normal (timed)', 'On flag capture'])
        s.trigger.currentIndexChanged.connect(lambda i: s.edit('trigger', 'oncap' if i == 1 else ''))
        s.eventteam = QComboBox(); s.eventteam.addItems(TEAM_NAMES[1:])
        s.eventteam.currentIndexChanged.connect(lambda i: s.edit('eventteam', i + 1))
        s.tintc = {}
        s.tint_widget = QWidget(); trow = QHBoxLayout(s.tint_widget); trow.setContentsMargins(0, 0, 0, 0)
        for ch in ('R', 'G', 'B', 'A'):
            b = QDoubleSpinBox(); b.setRange(0, 1); b.setSingleStep(.05); b.setValue(1)
            b.valueChanged.connect(lambda v, ch=ch: s.edit_tint(ch, v))
            trow.addWidget(QLabel(ch)); trow.addWidget(b); s.tintc[ch] = b
        s.match_color_btn = QPushButton('Match Team Color'); s.match_color_btn.clicked.connect(s.do_match_team_color)
        s.mirror_phys_btn = QPushButton('Mirror Physics to Family'); s.mirror_phys_btn.clicked.connect(s.do_mirror_physics)
        fl.addRow('Team', s.team); fl.addRow('Group team override', s.gteam); fl.addRow('Group prefab', s.gname)
        s.member_combo = QComboBox(); s.member_combo.currentIndexChanged.connect(s.pick_member)
        fl.addRow('Edit member', s.member_combo)
        fl.addRow('Front links to', s.link_row); fl.addRow('Back links to', s.blink_row)
        fl.addRow('Divisions', s.div); fl.addRow('Sweep angle', s.ang); fl.addRow('Hollow ratio', s.rat)
        fl.addRow(s.flip); fl.addRow('Passability', s.phys); fl.addRow(s.ricochet)
        fl.addRow('Physics driver', s.phydrv_combo); fl.addRow('', s.mirror_phys_btn)
        fl.addRow('Group tint (RGBA)', s.tint_widget); fl.addRow('', s.match_color_btn)
        fl.addRow('Spawn teams', s.zteam); fl.addRow('Safety for teams', s.zsafety); fl.addRow('Flags', s.zflags)
        fl.addRow('Weapon color', s.wcolor); fl.addRow('Tilt', s.tilt); fl.addRow('Init delay', s.initdelay)
        fl.addRow('Reload times', s.wdelay); fl.addRow('Fires flag', s.wtype)
        fl.addRow('Trigger', s.trigger); fl.addRow('Capturing team', s.eventteam)
        fl.addRow('Material (all faces)', s.mat)
        fl.addRow('Texture size', s.texsize)
        s.face = {}
        for fk, fl_ in (('top', 'Top'), ('sides', 'Sides'), ('bottom', 'Bottom'), ('inside', 'Inside'),
                        ('outside', 'Outside'), ('startside', 'Start side'), ('endside', 'End side')):
            cb = QComboBox(); cb.currentTextChanged.connect(lambda t, k=fk: s.edit_face(k, t))
            fl.addRow(fl_ + ' material', cb); s.face[fk] = cb
        d = QDockWidget('Selected object'); d.setWidget(w); s.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d)
        s.dock = d; s.on_select()
        s.build_text_dock()

    def build_text_dock(s):
        """A raw-text view of the map, for hand-editing or just reading what you built.
        Tabbed with the Selected Object panel rather than making the sidebar bigger."""
        tw = QWidget(); tl = QVBoxLayout(tw)
        s.maptext = CodeEdit(); s.maptext.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        s.maptext.setStyleSheet('font-family: monospace;')
        tools = QHBoxLayout()
        find_btn = QPushButton('Find / Replace  (Ctrl+F)'); find_btn.clicked.connect(lambda: s.show_find(False))
        goto_btn = QPushButton('Go to Line  (Ctrl+L)'); goto_btn.clicked.connect(s.goto_line)
        tools.addWidget(find_btn); tools.addWidget(goto_btn); tools.addStretch(1); tl.addLayout(tools)
        tl.addWidget(s.maptext)
        # find / replace bar, hidden until asked for
        s.find_bar = QWidget(); fb = QVBoxLayout(s.find_bar); fb.setContentsMargins(0, 0, 0, 0)
        r1 = QHBoxLayout(); s.find_edit = QLineEdit(); s.find_edit.setPlaceholderText('Find')
        prev_b = QPushButton('Prev'); next_b = QPushButton('Next'); close_b = QPushButton('Close')
        prev_b.clicked.connect(lambda: s.find_next(True)); next_b.clicked.connect(lambda: s.find_next(False)); close_b.clicked.connect(s.hide_find)
        for w_ in (s.find_edit, prev_b, next_b, close_b): r1.addWidget(w_)
        r2 = QHBoxLayout(); s.repl_edit = QLineEdit(); s.repl_edit.setPlaceholderText('Replace with')
        rep_b = QPushButton('Replace'); all_b = QPushButton('Replace All')
        rep_b.clicked.connect(s.replace_one); all_b.clicked.connect(s.replace_all)
        for w_ in (s.repl_edit, rep_b, all_b): r2.addWidget(w_)
        r3 = QHBoxLayout(); s.case_chk = QCheckBox('Match case'); s.word_chk = QCheckBox('Whole words'); s.find_info = QLabel('')
        for w_ in (s.case_chk, s.word_chk): r3.addWidget(w_)
        r3.addStretch(1); r3.addWidget(s.find_info)
        for r_ in (r1, r2, r3): fb.addLayout(r_)
        s.find_edit.textChanged.connect(s.find_incremental); s.find_edit.returnPressed.connect(lambda: s.find_next(False))
        s.repl_edit.returnPressed.connect(s.replace_one)
        s.case_chk.toggled.connect(lambda _v: s.update_find_marks()); s.word_chk.toggled.connect(lambda _v: s.update_find_marks())
        s.find_bar.hide(); tl.addWidget(s.find_bar)
        s.mt_status = QLabel(); tl.addWidget(s.mt_status)
        s.maptext.cursorPositionChanged.connect(s.update_mt_status)
        s.maptext.document().modificationChanged.connect(lambda _m: s.update_mt_status())
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut  # only while the cursor is in this panel
        for key, fn in (('Ctrl+F', lambda: s.show_find(False)), ('Ctrl+H', lambda: s.show_find(True)), ('F3', lambda: s.find_next(False)),
                        ('Shift+F3', lambda: s.find_next(True)), ('Ctrl+L', s.goto_line), ('Esc', s.hide_find)):
            sc = QShortcut(QKeySequence(key), tw); sc.setContext(ctx); sc.activated.connect(fn)
        row = QHBoxLayout()
        refresh = QPushButton('Refresh from Map'); refresh.clicked.connect(lambda: s.refresh_maptext(force=True))
        apply_btn = QPushButton('Apply to Map'); apply_btn.clicked.connect(s.apply_maptext)
        row.addWidget(refresh); row.addWidget(apply_btn); tl.addLayout(row)
        s.sym_chk = QCheckBox('Apply the toolbar\'s symmetry mode to new objects'); s.sym_chk.setChecked(True)
        tl.addWidget(s.sym_chk)
        note = QLabel('Advanced: hand-edit or paste .bzw text, then Apply to Map -- this works the same '
                      'as opening a file, undo works normally afterward. Refresh pulls in the latest '
                      'changes from clicking around; it also updates on its own a moment after you edit.\n'
                      'With the checkbox on and a symmetry mode selected in the toolbar, any object that '
                      'is new in the text you applied gets its symmetric copies built automatically, same '
                      'as placing it with the mouse. Editing an existing object\'s numbers, rather than '
                      'adding a new block, is treated as a plain edit and won\'t trigger this.')
        note.setWordWrap(True); tl.addWidget(note)
        s.textdock = QDockWidget('Map Text (advanced)'); s.textdock.setWidget(tw)
        s.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, s.textdock)
        s.tabifyDockWidget(s.dock, s.textdock); s.dock.raise_()
        s._text_pending = False
        s.textdock.visibilityChanged.connect(lambda vis: s.refresh_maptext(force=False) if vis else None)
        s.m.changed.connect(s.schedule_maptext_refresh)
        s.update_mt_status()

    def schedule_maptext_refresh(s):
        if not s.textdock.isVisible() or s._text_pending: return
        s._text_pending = True
        QTimer.singleShot(300, s._do_maptext_refresh)

    def _do_maptext_refresh(s):
        s._text_pending = False
        if s.textdock.isVisible(): s.refresh_maptext(force=False)

    def refresh_maptext(s, force=True):
        """Show the map's current text. The automatic refreshes (force=False) leave the box alone while it
        holds edits you haven't applied; the Refresh button (force=True) asks before throwing them away.
        The cursor and scroll position are kept so a refresh doesn't send you back to the top."""
        ed = s.maptext
        if ed.document().isModified():
            if not force: return
            if QMessageBox.question(s, 'Refresh from Map', 'The text has changes you have not applied. Discard them and reload from the map?') \
                    != QMessageBox.StandardButton.Yes: return
        cur = ed.textCursor(); line, col = cur.blockNumber(), cur.positionInBlock()
        vs, hs = ed.verticalScrollBar().value(), ed.horizontalScrollBar().value()
        ed.setPlainText(s.m.text())
        blk = ed.document().findBlockByNumber(min(line, ed.document().blockCount() - 1))
        c = QTextCursor(blk); c.setPosition(blk.position() + min(col, max(blk.length() - 1, 0))); ed.setTextCursor(c)
        ed.verticalScrollBar().setValue(vs); ed.horizontalScrollBar().setValue(hs)
        ed.document().setModified(False); s.update_find_marks()

    # ---- find / replace / go to line in the map text ---------------------------------
    def update_mt_status(s):
        cur = s.maptext.textCursor(); doc = s.maptext.document()
        t = 'Line %d of %d, column %d' % (cur.blockNumber() + 1, doc.blockCount(), cur.positionInBlock() + 1)
        s.mt_status.setText(t + ('   |   edited, not applied yet' if doc.isModified() else ''))

    def show_find(s, replace=False):
        s.find_bar.show()
        sel = s.maptext.textCursor().selectedText()
        if sel and '\u2029' not in sel: s.find_edit.setText(sel)  # start from what is selected
        if replace and s.find_edit.text(): s.repl_edit.setFocus()
        else: s.find_edit.setFocus(); s.find_edit.selectAll()
        s.update_find_marks()

    def hide_find(s):
        s.find_bar.hide(); s.maptext.setExtraSelections([]); s.maptext.setFocus()

    def _find_flags(s, backward=False):
        f = QTextDocument.FindFlag(0)
        if s.case_chk.isChecked(): f |= QTextDocument.FindFlag.FindCaseSensitively
        if s.word_chk.isChecked(): f |= QTextDocument.FindFlag.FindWholeWords
        if backward: f |= QTextDocument.FindFlag.FindBackward
        return f

    def find_next(s, backward=False):
        txt = s.find_edit.text(); ed = s.maptext
        if not txt: return False
        flags = s._find_flags(backward)
        if ed.find(txt, flags): return True
        keep = ed.textCursor(); c = ed.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start); ed.setTextCursor(c)
        if ed.find(txt, flags):
            s.statusBar().showMessage('Search wrapped around.', 2500); return True
        ed.setTextCursor(keep); s.find_info.setText('No matches'); return False

    def find_incremental(s, _text=None):
        """Typing in the Find box jumps to the first match at or after where you were."""
        c = s.maptext.textCursor()
        if c.hasSelection(): c.setPosition(c.selectionStart()); s.maptext.setTextCursor(c)
        s.find_next(False); s.update_find_marks()

    def update_find_marks(s):
        """Highlight every match and say how many there are."""
        ed = s.maptext; txt = s.find_edit.text(); sels = []; n = 0; cap = 3000
        if txt and s.find_bar.isVisible():
            fmt = QTextCharFormat(); fmt.setBackground(QColor(255, 235, 59, 120))
            doc, flags, pos = ed.document(), s._find_flags(), 0
            while n < cap:
                c = doc.find(txt, pos, flags)
                if c.isNull(): break
                sel = QTextEdit.ExtraSelection(); sel.cursor = c; sel.format = fmt; sels.append(sel); n += 1; pos = c.position()
        ed.setExtraSelections(sels)
        s.find_info.setText('' if not txt else 'No matches' if n == 0 else '%d%s match%s' % (n, '+' if n >= cap else '', '' if n == 1 else 'es'))

    def replace_one(s):
        txt = s.find_edit.text(); ed = s.maptext
        if not txt: return
        cur = ed.textCursor(); sel = cur.selectedText()
        if cur.hasSelection() and (sel == txt if s.case_chk.isChecked() else sel.lower() == txt.lower()):
            cur.insertText(s.repl_edit.text())
        s.find_next(False); s.update_find_marks()

    def replace_all(s):
        txt = s.find_edit.text()
        if not txt: return
        n = replace_all_text(s.maptext.document(), txt, s.repl_edit.text(), s._find_flags())
        s.update_find_marks()
        s.statusBar().showMessage('Replaced %d match%s. Apply to Map to use the change.' % (n, '' if n == 1 else 'es') if n
                                  else 'Nothing to replace.', 6000)

    def goto_line(s):
        doc = s.maptext.document(); n = max(doc.blockCount(), 1)
        line, ok = QInputDialog.getInt(s, 'Go to Line', 'Line number (1 to %d):' % n, s.maptext.textCursor().blockNumber() + 1, 1, n)
        if not ok: return
        c = QTextCursor(doc.findBlockByNumber(line - 1)); s.maptext.setTextCursor(c); s.maptext.centerCursor(); s.maptext.setFocus()

    def _sig(s, o):
        # Identity by content, not by uid/fam (which text can't express), so this survives
        # a full reparse: two objects with the same shape and place are "the same object".
        return (o.t, round(o.x, 2), round(o.y, 2), round(o.z, 2), round(o.sx, 2), round(o.sy, 2),
                round(o.sz, 2), round(o.r, 2), o.team, o.name)

    def apply_maptext(s):
        before = {s._sig(o) for o in s.m.objs}
        try:
            s.m.load(s.maptext.toPlainText())
        except Exception as e:
            QMessageBox.warning(s, 'Apply failed', str(e)); return
        s.maptext.document().setModified(False)  # in sync with the map now, so refreshes may resume
        added = 0
        if s.sym_chk.isChecked() and s.m.sym:
            for o in list(s.m.objs):  # a plain list, so siblings appended below aren't re-processed
                if not o.fam and s._sig(o) not in before:
                    s.m.make_family(o); added += 1
        s.m.changed.emit()
        msg = 'Map text applied.'
        if added: msg += ' Built symmetry for %d new object%s.' % (added, '' if added == 1 else 's')
        s.statusBar().showMessage(msg, 6000)

    def current_obj(s):
        """The object the panel should actually read/write: normally the selection, but if
        'Edit member' has picked one of a group's prefab children, that child instead --
        editing it mutates the prefab in place, same as editing any other object's fields."""
        o = s.m.sel
        if s.editing_member and o and o.t == 'group' and s.editing_member[0] is o:
            kids = s.m.defines.get(o.name, [])
            if 0 <= s.editing_member[1] < len(kids): return kids[s.editing_member[1]]
            s.editing_member = None
        return o

    def pick_member(s, idx):
        if s.busy: return
        s.editing_member = (s.m.sel, idx - 1) if idx > 0 else None
        s.pushed = False; s.refresh()

    def edit(s, k, v):
        o = s.current_obj()
        if s.busy or not o: return
        if not s.pushed: s.m.push(); s.pushed = True
        if o.t == 'group' and k in ('sx', 'sy', 'sz') and abs(v) < 0.01: v = 0.01  # a zero scale would collapse the group
        setattr(o, k, v)
        if not s.editing_member and k not in ('link', 'blink', 'lface', 'bface', 'name'): s.m.sync_family(o)
        s.busy = True; s.m.changed.emit(); s.busy = False

    def edit_texsize(s):
        """Texture size field: blank, or exactly the 4 (box, pyramid, arc) / 2 (cone) numbers BZFlag reads; anything else is put back."""
        o = s.current_obj()
        if s.busy or not o or o.t not in ('arc', 'cone', 'box', 'pyramid'): return
        want = 2 if o.t == 'cone' else 4
        toks = s.texsize.text().replace(',', ' ').split()
        try:
            for t in toks: float(t)
            ok = not toks or len(toks) == want
        except ValueError: ok = False
        if not ok:
            s.m.warn.emit('Texture size needs %d numbers (or leave it blank for the default).' % want)
            s.busy = True; s.texsize.setText(o.texsize); s.busy = False; return
        new = ' '.join(toks)
        if new != o.texsize: s.edit('texsize', new)

    def edit_face(s, k, v):
        o = s.current_obj()
        if s.busy or not o: return
        if not s.pushed: s.m.push(); s.pushed = True
        if v: o.mats[k] = v
        else: o.mats.pop(k, None)
        if not s.editing_member: s.m.sync_family(o)
        s.busy = True; s.m.changed.emit(); s.busy = False

    def edit_tint(s, ch, v):
        o = s.current_obj()
        if s.busy or not o: return
        if not s.pushed: s.m.push(); s.pushed = True
        i = 'RGBA'.index(ch); t = list(o.tint); t[i] = v; o.tint = tuple(t)
        if not s.editing_member: s.m.sync_family(o)
        s.busy = True; s.m.changed.emit(); s.busy = False

    def on_select(s):
        s.pushed = False; s.editing_member = None; s.refresh()

    def refresh(s):
        if s.busy: return
        grp = s.m.sel if s.m.sel and s.m.sel.t == 'group' else None
        o = s.current_obj(); s.busy = True
        s.dock.widget().setEnabled(s.m.sel is not None)
        title = 'Selected object'
        if o: title += ': ' + o.t + (' ' + o.name if o.name else '') + (' (in %s)' % grp.name if grp and o is not grp else '')
        s.dock.setWindowTitle(title)
        if grp:
            kids = s.m.defines.get(grp.name, [])
            s.member_combo.clear(); s.member_combo.addItem('(whole group)')
            s.member_combo.addItems(['%d: %s%s' % (i, k.t, ' ' + k.name if k.name else '') for i, k in enumerate(kids)])
            want = (s.editing_member[1] + 1) if (s.editing_member and s.editing_member[0] is grp) else 0
            if want < s.member_combo.count(): s.member_combo.setCurrentIndex(want)
        rows = {'x': True, 'y': True, 'z': True, 'sx': o and o.t not in ('mesh', 'weapon'),
                'sy': o and o.t not in ('mesh', 'weapon'), 'sz': o and o.t not in ('mesh', 'weapon'),
                'r': o and o.t != 'mesh', 'team': o and o.t == 'base',
                'gteam': o and o.t == 'group', 'gname': o and o.t == 'group', 'member_combo': grp is not None,
                'link': o and o.t == 'teleporter', 'blink': o and o.t == 'teleporter',
                'div': o and o.t in ('arc', 'cone'), 'ang': o and o.t == 'arc', 'rat': o and o.t == 'arc',
                'texsize': o and o.t in ('arc', 'cone', 'box', 'pyramid'),
                'mat': o and o.t in ('box', 'pyramid', 'arc', 'cone'), 'flip': o and o.t == 'pyramid',
                'phys': o and o.t in ('box', 'pyramid', 'arc', 'cone'), 'zteam': o and o.t == 'zone',
                'zsafety': o and o.t == 'zone', 'zflags': o and o.t == 'zone',
                'ricochet': o and o.t in ('box', 'pyramid', 'arc', 'cone', 'group'),
                'wcolor': o and o.t == 'weapon', 'tilt': o and o.t == 'weapon', 'initdelay': o and o.t == 'weapon',
                'wdelay': o and o.t == 'weapon', 'wtype': o and o.t == 'weapon', 'trigger': o and o.t == 'weapon',
                'eventteam': o and o.t == 'weapon' and o.trigger == 'oncap',
                'phydrv_combo': o and o.t in ('box', 'pyramid', 'arc', 'cone'), 'tintrow': o and o.t == 'group',
                'match_color_btn': o and o.t == 'group' and o.fam,
                'mirror_phys_btn': o and o.t in ('box', 'pyramid', 'arc', 'cone') and o.fam and o.phydrv}
        for name, vis in rows.items():
            widget = {'x': s.sp['x'], 'y': s.sp['y'], 'z': s.sp['z'], 'sx': s.sp['sx'], 'sy': s.sp['sy'],
                      'sz': s.sp['sz'], 'r': s.sp['r'], 'team': s.team, 'gteam': s.gteam, 'gname': s.gname,
                      'member_combo': s.member_combo,
                      'link': s.link_row, 'blink': s.blink_row, 'div': s.div, 'ang': s.ang, 'rat': s.rat, 'mat': s.mat, 'texsize': s.texsize,
                      'flip': s.flip, 'phys': s.phys, 'zteam': s.zteam, 'zsafety': s.zsafety,
                      'zflags': s.zflags, 'ricochet': s.ricochet, 'phydrv_combo': s.phydrv_combo,
                      'wcolor': s.wcolor, 'tilt': s.tilt, 'initdelay': s.initdelay, 'wdelay': s.wdelay,
                      'wtype': s.wtype, 'trigger': s.trigger, 'eventteam': s.eventteam,
                      'match_color_btn': s.match_color_btn, 'mirror_phys_btn': s.mirror_phys_btn,
                      'tintrow': s.tint_widget}[name]
            row = s.dock.widget().layout().labelForField(widget)
            if row and name in ('sx', 'sy', 'sz'):  # a group's 'size' is its scale
                row.setText({'sx': 'Scale X', 'sy': 'Scale Y', 'sz': 'Scale Z'}[name] if o and o.t == 'group' else dict(s.FIELDS)[name])
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
                cb.addItems(s.m.tele_names())  # includes o itself (a teleporter may link to itself) and ones inside groups
                cb.setCurrentText(cur or 'Nothing')
            s.lfacec.setCurrentIndex(0 if o.lface == 'b' else 1); s.bfacec.setCurrentIndex(0 if o.bface == 'b' else 1)
            s.div.setValue(o.divisions); s.ang.setValue(o.angle if o.angle else 360); s.rat.setValue(o.ratio)
            s.mat.clear(); s.mat.addItems([''] + sorted(s.m.materials)); s.mat.setCurrentText(o.matref)
            s.texsize.setText(o.texsize if o.t in ('arc', 'cone', 'box', 'pyramid') else '')
            s.texsize.setPlaceholderText({'arc': '4 numbers, e.g. -2 -2 -8 -8 (blank = default)', 'cone': '2 numbers, e.g. -8 -8 (blank = default)',
                                          'box': '4 numbers, e.g. 1 1 1 1 = one texture per face (blank = default)',
                                          'pyramid': '4 numbers, e.g. 1 1 1 1 = one texture per face (blank = default)'}.get(o.t, ''))
            for fk, cb in s.face.items():
                cur = o.mats.get(fk, '')
                cb.clear(); cb.addItems([''] + sorted(set(s.m.materials) | ({cur} if cur else set())))
                cb.setCurrentText(cur)
            s.flip.setChecked(o.flipz if o.t == 'pyramid' else False)
            if o.t == 'weapon':
                s.wcolor.setCurrentIndex(o.wcolor); s.tilt.setValue(o.tilt); s.initdelay.setValue(o.initdelay)
                s.wdelay.setText(' '.join(num(d) for d in o.delay)); s.wtype.setText(o.wtype)
                s.trigger.setCurrentIndex(1 if o.trigger == 'oncap' else 0); s.eventteam.setCurrentIndex(o.eventteam - 1)
            s.phys.setCurrentIndex(['normal', 'drivethrough', 'shootthrough', 'passable'].index(o.phys)
                                    if o.phys in ('normal', 'drivethrough', 'shootthrough', 'passable') else 0)
            s.ricochet.setChecked(o.ricochet)
            s.phydrv_combo.clear(); s.phydrv_combo.addItems([''] + sorted(s.m.physics)); s.phydrv_combo.setCurrentText(o.phydrv)
            for ch, b in s.tintc.items(): b.setValue(o.tint['RGBA'.index(ch)] if o.t == 'group' else 1.0)
            s.zteam.setText(' '.join(str(t) for t in o.zteam) if o.t == 'zone' else '')
            s.zsafety.setText(' '.join(str(t) for t in o.zsafety) if o.t == 'zone' else '')
            s.zflags.setPlainText(o.zflags if o.t == 'zone' else '')
        s.ws.setValue(s.m.W); s.busy = False

    def _parse_ints(s, text, lo, hi):
        out = []
        for tok in text.replace(',', ' ').split():
            try:
                v = int(tok)
                if lo <= v <= hi: out.append(v)
            except ValueError: pass
        return sorted(set(out))

    def _parse_floats(s, text):
        out = []
        for tok in text.replace(',', ' ').split():
            try: out.append(float(tok))
            except ValueError: pass
        return out

    def set_world(s, v):
        if not s.busy and v != s.m.W: s.m.push(); s.m.W = v; s.ed.fit(); s.m.changed.emit()

    def do_match_team_color(s):
        o = s.current_obj()
        if not o or not o.fam: return
        s.m.push(); s.m.match_team_color(o); s.m.changed.emit(); s.refresh()

    def do_mirror_physics(s):
        o = s.current_obj()
        if not o or not o.fam or not o.phydrv: return
        s.m.push(); s.m.mirror_physics_for_family(o); s.m.changed.emit(); s.refresh()

    def do_group(s):
        if not s.m.group_selection():
            QMessageBox.information(s, 'Group', 'Select an object first (click it, or Ctrl+click several).')

    def open_materials(s):
        MaterialsDialog(s.m, os.path.dirname(s.m.path) if s.m.path else None).exec()

    def _removed_text(s, gone):
        names = {'defines': 'group definition', 'materials': 'material', 'texmats': 'texturematrix', 'physics': 'physics driver'}
        parts = ['%d %s%s' % (len(v), names[k], '' if len(v) == 1 else ('es' if k == 'texmats' else 's')) for k, v in gone.items() if v]
        return ', '.join(parts)

    def open_cleanup(s):
        if not any(s.m.cleanup_plan(False).values()):
            if not any(s.m.cleanup_plan(True).values()):
                QMessageBox.information(s, 'Remove Unused', 'Nothing unused: every group definition, material, texturematrix and '
                                        'physics driver in this map is in use.'); return
        dlg = CleanupDialog(s.m)
        if not dlg.exec(): return
        gone = s.m.apply_cleanup(dlg.chosen())
        s.statusBar().showMessage(('Removed ' + s._removed_text(gone) + '.') if any(gone.values()) else 'Nothing removed.', 8000)

    def offer_cleanup(s):
        """Called before a save: offer to drop unused group definitions (and what only they used).
        Returns False if the person cancelled the save."""
        if s.settings.value('cleanupAtSave', 'ask') == 'never': return True
        plan = s.m.cleanup_plan(False)
        if not plan['defines']: return True
        n = len(plan['defines'])
        box = QMessageBox(s); box.setIcon(QMessageBox.Icon.Question); box.setWindowTitle('Unused groups')
        box.setText('This map has %d group definition%s that nothing places.' % (n, '' if n == 1 else 's'))
        extra = [t for t in (len(plan['materials']) and '%d material%s' % (len(plan['materials']), '' if len(plan['materials']) == 1 else 's'),
                             len(plan['texmats']) and '%d texturematrix' % len(plan['texmats']),
                             len(plan['physics']) and '%d physics driver%s' % (len(plan['physics']), '' if len(plan['physics']) == 1 else 's')) if t]
        box.setInformativeText('Remove %s%s before saving? They only take up space in the file, and Undo (Ctrl+Z) brings them back.'
                               % ('it' if n == 1 else 'them', (' along with ' + ', '.join(extra) + ' only they used') if extra else ''))
        box.setDetailedText('\n'.join(['Group definition: ' + x for x in plan['defines']] + ['Material: ' + x for x in plan['materials']] +
                                       ['Texturematrix: ' + x for x in plan['texmats']] + ['Physics driver: ' + x for x in plan['physics']]))
        rm = box.addButton('Remove and Save', QMessageBox.ButtonRole.AcceptRole)
        box.addButton('Save As Is', QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        never = QCheckBox("Don't ask again (turn it back on under Objects)"); box.setCheckBox(never)
        box.exec(); clicked = box.clickedButton()
        if clicked is cancel or clicked is None: return False
        if never.isChecked(): s.cleanup_ask_action.setChecked(False)
        if clicked is rm:
            gone = s.m.apply_cleanup(plan); s.statusBar().showMessage('Removed ' + s._removed_text(gone) + '.', 8000)
        return True

    def set_pv_option(s, key, attr, on):
        """A View-menu choice for the 3D view (edge shading, textures): apply it now and remember it."""
        setattr(s.pv, attr, on); s.settings.setValue(key, 'true' if on else 'false'); s.pv.update()

    def set_remote_textures(s, on):
        """View > Load Remote Textures Automatically: remember the choice, and when it is turned on, start loading the
        current map's internet textures (including ones that failed earlier, in case you were offline)."""
        s.settings.setValue('remoteTextures', 'true' if on else 'false'); s.m.remote_textures = on
        if on:
            for k in [k for k, v in s.m.texcache.items() if v is None and is_remote_texture(k)]: del s.m.texcache[k]
            s.m.prefetch_remote()
        s.pv.update()

    def open_texmats(s):
        TexMatrixDialog(s.m).exec()

    def open_world(s):
        old = s.m.W
        if WorldDialog(s.m).exec() and s.m.W != old: s.ed.fit()

    def open_options(s):
        OptionsDialog(s.m).exec()

    def open_physics(s):
        PhysicsDialog(s.m).exec()

    # ---- custom object library ----------------------------------------------------
    def fill_library_menu(s):
        """Rebuilt each time the menu opens, so objects added or removed show up without a restart."""
        m = s.lib_menu; m.clear(); items = scan_library(); cats = {}
        for it in items: cats.setdefault(it.category, []).append(it)
        for cat in sorted(cats, key=str.lower):
            sub = m.addMenu(cat)
            for it in cats[cat]:
                a = sub.addAction(it.name + ('' if it.builtin else '  (mine)'))
                a.setStatusTip(it.description); a.setToolTip(it.description)
                a.triggered.connect(lambda _c=False, it=it: s.pick_library(it))
        if not items: m.addAction('No custom objects found').setEnabled(False)
        m.addSeparator()
        for label, fn in (('Browse Library...', s.open_library), ('Import into My Objects...', s.library_import),
                          ('Save Selection to My Objects...', s.library_save_selection),
                          ('Open My Objects Folder', s.library_open_folder)):
            m.addAction(label).triggered.connect(lambda _c=False, fn=fn: fn())

    def pick_library(s, item):
        """Make `item` the object the next click in either view places."""
        s.m.lib_item = item; s.m.tool = 'library'
        s.lib_action.setText('Place: ' + item.name); s.lib_action.setEnabled(True); s.lib_action.setChecked(True)
        s.statusBar().showMessage(HINT['library'], 8000)

    def open_library(s):
        LibraryDialog(s).exec()

    def library_open_folder(s):
        d = user_library_dir(); os.makedirs(d, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def _confirm_replace(s, name):
        _p, exists = library_path_for(name)
        return not exists or QMessageBox.question(s, 'Replace object', 'You already have an object called "%s". Replace it?' % name) \
            == QMessageBox.StandardButton.Yes

    def library_import(s):
        """Copy the objects in another .bzw (with the materials they use) into your own library."""
        folder = os.path.dirname(s.m.path) if s.m.path else ''
        path, _ = QFileDialog.getOpenFileName(s, 'Import into My Objects', folder, 'BZFlag world files (*.bzw);;All files (*)')
        if not path: return
        default = os.path.splitext(os.path.basename(path))[0].replace('_', ' ').replace('-', ' ').strip().title()
        dlg = ObjectInfoDialog(s, 'Import into My Objects', default, {i.category for i in scan_library()}, allow_split=True)
        if not dlg.exec(): return
        name, cat, desc, split = dlg.values()
        if not split and not s._confirm_replace(name): return
        try: made = import_into_library(path, name, cat, desc, split)
        except OSError as e:
            QMessageBox.warning(s, 'Import failed', str(e)); return
        if not made:
            QMessageBox.information(s, 'Nothing to import', 'That file has no meshes, boxes or groups to make an object from. '
                                    '(Teleporters, zones and weapons only make sense in one particular map, so they are left out.)'); return
        s.statusBar().showMessage('Added %d object%s to My Objects. Find %s under Objects > Custom Objects.' %
                                  (len(made), '' if len(made) == 1 else 's', 'it' if len(made) == 1 else 'them'), 10000)

    def library_save_selection(s):
        """Save what is selected in the map as a reusable object in your own library."""
        sel = s.m.msel or ([s.m.sel] if s.m.sel else [])
        if not sel:
            QMessageBox.information(s, 'Save to My Objects', 'Select the object first (click it, or Ctrl+click several), then try again.'); return
        usable = [o for o in sel if o.t not in LIB_SKIP]
        if not usable:
            QMessageBox.information(s, 'Save to My Objects', 'Teleporters, zones and weapons only make sense in one particular map, '
                                    'so they cannot be saved as library objects.'); return
        default = usable[0].name.replace('_', ' ').title() if len(usable) == 1 and usable[0].t == 'group' else ''
        dlg = ObjectInfoDialog(s, 'Save to My Objects', default, {i.category for i in scan_library()})
        if not dlg.exec(): return
        name, cat, desc, _split = dlg.values()
        if not s._confirm_replace(name): return
        try: save_library_object(name, cat, desc, usable, s.m.defines, s.m.materials, s.m.texmats)
        except OSError as e:
            QMessageBox.warning(s, 'Save failed', str(e)); return
        msg = 'Saved "%s" to My Objects.' % name
        if len(usable) < len(sel): msg += ' (Teleporters, zones and weapons were left out.)'
        s.statusBar().showMessage(msg, 8000)

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
        nw, objs, materials, defines, extras, options_text, physics, _world, _texmats = parse(text)
        meshes = [o for o in objs if o.t == 'mesh']
        if not meshes:
            QMessageBox.information(s, 'Import mesh', 'No mesh object was found in that file.'); return
        s.m.push()
        for mo in meshes:
            mo.uid = s.m.nid; s.m.nid += 1
        # Wrapped in a group rather than added as loose top-level meshes: a mesh is often the
        # single biggest thing in a .bzw file, and duplicating it (e.g. with symmetry) would
        # otherwise copy its entire geometry every time. A group only copies a position and a
        # rotation per instance -- the prefab itself (and the mesh data) is stored once.
        g = s.m.make_group(meshes, 'mesh-group')
        s.m.objs.append(g)
        for name, mat in materials.items():  # bring along any materials the mesh's faces refer to
            s.m.materials.setdefault(name, mat)
        s.m.select(g); s.m.changed.emit()
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
        """Called after a map is opened or saved somewhere new: forget what was cached and load the textures
        whose PNGs sit beside the map. (Others are looked for later, the first time they are drawn.)"""
        s.m.texcache = {}
        s.m.prefetch_remote()
        if not folder: return
        for mat in s.m.materials.values():
            p = s.m.texture_file(mat.texture)
            if not p: continue
            img = QImage(p)
            if img.isNull(): continue
            s.m.texcache[mat.texture] = micro_texture(img)  # only a tiny copy is kept, whatever the file's size

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
        if not s.offer_cleanup(): return
        try:
            with open(path, 'w', encoding='utf-8') as f: f.write(s.m.text())
        except OSError as e:
            QMessageBox.warning(s, 'Save failed', str(e)); return
        s.m.path = path; s.load_textures(os.path.dirname(path)); s.title(); s.add_recent(path)


def load_app_icon():
    """The BZMM logo: bzmapmaker.png beside this script, inside a PyInstaller bundle, or an installed theme icon."""
    for base in (getattr(sys, '_MEIPASS', None), os.path.dirname(os.path.abspath(__file__)),
                 '/usr/share/icons/hicolor/512x512/apps'):
        p = base and os.path.join(base, 'bzmapmaker.png')
        if p and os.path.exists(p): return QIcon(p)
    ic = QIcon.fromTheme('bzmapmaker')
    return None if ic.isNull() else ic


if __name__ == '__main__':
    if '--version' in sys.argv[1:]:
        print('BZ Map Maker', __version__); sys.exit(0)
    if sys.platform == 'win32':  # so the taskbar shows our icon rather than python's
        try:
            import ctypes; ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('org.bzflag.bzmapmaker')
        except Exception: pass
    app = QApplication(sys.argv)
    try: QApplication.setDesktopFileName('bzmapmaker')  # Wayland/Linux: ties the window to bzmapmaker.desktop's icon
    except Exception: pass
    _icon = load_app_icon()
    if _icon: app.setWindowIcon(_icon)
    w = Win(sys.argv[1] if len(sys.argv) > 1 else None); w.show()
    sys.exit(app.exec())
