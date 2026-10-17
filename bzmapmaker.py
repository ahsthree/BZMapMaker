#!/usr/bin/env python3
"""BZ Map Maker - a BZFlag .bzw editor with a 2D layout view and a live 3D preview.

Run:  pip install PyQt6   then   python bzmapmaker.py [optional_map.bzw]
Works on Windows, macOS and Linux. The 3D preview is drawn in software (QPainter), so no
OpenGL is required. Textures are applied to box faces with Qt's 2D projective quadToQuad
transform, which is an exact planar homography, not an OpenGL approximation.
"""
import sys, os, math, json, hashlib

__version__ = '1.4.4'
import numpy as np
from dataclasses import dataclass, field, asdict, replace
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QPointF, QRectF, QSettings, QTimer
from PyQt6.QtGui import (QPainter, QColor, QPen, QBrush, QPolygonF, QPainterPath, QAction,
                          QActionGroup, QKeySequence, QPixmap, QTransform, QImage, QIcon)
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QSplitter, QDockWidget, QFormLayout,
                              QDoubleSpinBox, QSpinBox, QComboBox, QFileDialog, QMessageBox, QDialog,
                              QListWidget, QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout, QLabel,
                              QDialogButtonBox, QInputDialog, QPlainTextEdit, QCheckBox)

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
    out = []
    for x, y, z in verts:
        x += o.sx; y += o.sy; z += o.sz
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
    world = new_world()
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
            r = nm(v.get('rotation'), (0,))[0]
            team = int(nm(v.get('team'), (0,))[0])
            go = Obj('group', x, y, z, 1, 1, 1, r, team, name)
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

    inst = {g.iname: g.name for g in objs if g.t == 'group' and g.iname}

    uses = {}
    for g in objs:
        if g.t == 'group': uses[g.name] = uses.get(g.name, 0) + 1

    def find_tele(name):
        """Teleporters a link endpoint names: top-level ones, or one inside a group instance
        (written <instance name>:<teleporter name>). Returns (teleporters, plain name, prefab, instance)."""
        top = [o for o in objs if o.t == 'teleporter' and o.name == name]
        if top: return top, name, None, None
        for i, ch in enumerate(name):
            if ch == ':' and name[:i] in inst:
                kids = [k for k in defines.get(inst[name[:i]], []) if k.t == 'teleporter' and k.name == name[i + 1:]]
                if kids: return kids, name[i + 1:], inst[name[:i]], name[:i]
        return [], name, None, None

    def places(name):  # how many places hold a teleporter with this plain name
        return sum(1 for o in objs if o.t == 'teleporter' and o.name == name) + \
               sum(1 for ks in defines.values() if any(k.t == 'teleporter' and k.name == name for k in ks))

    raw = []
    for f, t in links:
        fn, fface = resolve(f, 'f')
        tn, tface = resolve(t, 'b' if fface == 'f' else 'f')
        if tface not in ('f', 'b'): tface = 'b' if fface == 'f' else 'f'
        srcs, _, sdef, sinst = find_tele(fn); tgts, tplain, tdef, tinst = find_tele(tn)
        ok = bool(srcs)
        if ok and (sdef or tdef):
            # Links are stored per teleporter, and a group's teleporters belong to its prefab, shared by
            # every instance. Keep only links that come back out exactly as they went in; anything else
            # (e.g. between two instances) is carried through untouched below.
            shared = sdef and uses.get(sdef, 0) != 1
            ok = bool(tgts) and not (tdef and places(tplain) != 1 and not (sdef == tdef and sinst == tinst))
            if sdef and not tdef: ok = ok and not shared
            if sdef and tdef and sinst != tinst: ok = ok and not shared and uses.get(tdef, 0) == 1
            if not sdef and tdef: ok = ok and uses.get(tdef, 0) == 1
        if not ok: raw.append((f, t)); continue
        for o in srcs:
            if fface == 'f': o.link, o.lface = (tplain if tdef else tn), tface
            elif fface == 'b': o.blink, o.bface = (tplain if tdef else tn), tface
    world['rawlinks'] = raw
    return nw, objs, materials, defines, extras, options_text, physics, world


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
        ln = ['group %s' % o.name, '  shift %s %s %s' % (num(o.x), num(o.y), num(o.z)),
              '  rotation %s' % num(o.r)]
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


class Model(QObject):
    changed = pyqtSignal(); selected = pyqtSignal(); reset = pyqtSignal(); warn = pyqtSignal(str)

    def __init__(s):
        super().__init__()
        s.W, s.objs, s.sel, s.msel, s.undo, s.path = 400, [], None, [], [], None
        s.materials, s.defines, s.texcache, s.extras = {}, {}, {}, []
        s.options_text = ''
        s.physics = {}
        s.world = new_world()  # Objects -> World Settings
        s.nid, s.sym = 1, 0
        s.tool, s.snap = 'select', True
        s.show_zones = True  # View → Show Zones; zones are editor-only aids and can clutter large maps

    def push(s):
        d = {'W': s.W, 'objs': [asdict(o) for o in s.objs],
             'materials': {k: asdict(v) for k, v in s.materials.items()},
             'defines': {k: [asdict(o) for o in v] for k, v in s.defines.items()}, 'extras': s.extras,
             'options_text': s.options_text, 'physics': {k: asdict(v) for k, v in s.physics.items()},
             'world': s.world}
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
        s.world = d.get('world') or new_world()
        s.nid = max([s.nid] + [o.uid + 1 for o in s.objs])
        s.selected.emit(); s.changed.emit()

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

    def instance_names(s):
        """BZFlag names a teleporter inside a group instance '<instance name>:<teleporter name>',
        so each group instance needs its own name. Returns {id(group object): unique name}."""
        out, used = {}, set()
        for g in s.objs:
            if g.t != 'group': continue
            base = g.iname or g.name or 'group'; n, i = base, 1
            while n in used: i += 1; n = '%s_%d' % (base, i)
            used.add(n); out[id(g)] = n
        return out

    def add(s, t, x, y):
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
        """Move an existing top-level object into an existing group's prefab, converting
        its position into the group's local frame (undoing the group's own shift/rotation)
        so it doesn't visually move. Returns False if either argument isn't valid for this."""
        if grp.t != 'group' or obj.t == 'group' or obj is grp or obj not in s.objs: return False
        a = math.radians(-grp.r); c, sn = math.cos(a), math.sin(a)
        dx, dy = obj.x - grp.x, obj.y - grp.y
        kid = replace(obj)
        kid.x, kid.y, kid.z = dx * c - dy * sn, dx * sn + dy * c, obj.z - grp.z
        kid.r = (obj.r - grp.r) % 360
        kid.fam = kid.fam_k = kid.fold = 0; kid.mode = ''
        s.defines.setdefault(grp.name, []).append(kid)
        s.objs.remove(obj)
        return True

    def group_selection(s):
        """Ctrl+G. If exactly one of the selected objects is already a group, everything
        else selected is added into it. Otherwise all selected non-group objects are
        wrapped into a single new prefab + group instance at their centroid."""
        sel = s.msel or ([s.sel] if s.sel else [])
        groups = [o for o in sel if o.t == 'group']; plain = [o for o in sel if o.t != 'group']
        if not sel: return None
        s.push()
        if len(groups) == 1 and plain:
            g = groups[0]
            for o in plain: s.add_to_group(g, o)
            s.msel = []; s.select(g); s.changed.emit()
            return g.name
        if not plain: return None
        g = s.make_group(plain)
        for o in plain:
            s.objs.remove(o)
        s.objs.append(g)
        s.msel = []; s.select(g); s.changed.emit()
        return g.name

    def load(s, text, folder=None):
        nw, objs, materials, defines, extras, options_text, physics, world = parse(text)
        s.push(); s.W = nw or s.W; s.world = world; s.objs = objs; s.materials = materials; s.defines = defines
        s.extras = extras; s.options_text = options_text; s.physics = physics
        s.sel = None; s.msel = []
        for o in s.objs: o.uid = s.nid; s.nid += 1
        s.texcache = {}
        s.reset.emit(); s.selected.emit(); s.changed.emit()

    def text(s):
        out = ['# Made with BZ Map Maker'] + world_block(s.world, s.W) + ['']
        if s.options_text.strip():
            out += ['options'] + ['  ' + ln for ln in s.options_text.splitlines() if ln.strip()] + ['end', '']
        for pd in s.physics.values(): out += physics_block(pd) + ['']
        for c in s.extras: out += [c, '']  # physics, dynamic colors... they may be referenced below
        for m in s.materials.values(): out += material_block(m) + ['']
        for name, kids in s.defines.items():
            out.append('define %s' % name)
            for kd in kids: out += ['  ' + ln for ln in obj_block(kd)]
            out += ['enddef', '']
        eff = s.instance_names()
        has_tele = lambda g: any(k.t == 'teleporter' and k.name for k in s.defines.get(g.name, []))
        for o in s.objs:
            if o.fam:  # a plain, harmless comment to everything else that reads this file --
                       # only this editor looks for it, to restore symmetry linkage on reopen
                out.append('#bzmm-sym fam=%s k=%d fold=%d mode=%s' % (o.fam, o.fam_k, o.fold, o.mode))
            blk = obj_block(o)
            if o.t == 'group' and has_tele(o): blk.insert(1, '  name %s' % eff[id(o)])  # names its teleporters for links
            out += blk + ['']
        top = {o.name for o in s.objs if o.t == 'teleporter'}
        where = {}  # teleporter name -> names of the group instances whose prefab holds one
        for g in s.objs:
            if g.t == 'group':
                for k in s.defines.get(g.name, []):
                    if k.t == 'teleporter' and k.name: where.setdefault(k.name, []).append(eff[id(g)])

        def full(name, inst=None):
            """BZW name of a link target, seen from inside group instance `inst` (None = top level)."""
            if inst and inst in where.get(name, []): return '%s:%s' % (inst, name)
            if name in top: return name
            w = where.get(name)
            if w: return '%s:%s' % (w[0], name) if len(w) == 1 else '*:' + name
            return name

        def emit(src, o, inst):
            for face, tgt, other in (('f', o.link, o.lface), ('b', o.blink, o.bface)):
                if tgt: out.extend(['link', '  from %s:%s' % (src, face), '  to %s:%s' % (full(tgt, inst), other), 'end', ''])
        for o in s.objs:
            if o.t == 'teleporter': emit(o.name, o, None)
            elif o.t == 'group':
                for k in s.defines.get(o.name, []):
                    if k.t == 'teleporter' and k.name: emit('%s:%s' % (eff[id(o)], k.name), k, eff[id(o)])
        for f, t in s.world.get('rawlinks', []): out.extend(['link', '  from %s' % f, '  to %s' % t, 'end', ''])
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


HINT = {'select': 'Click to select and drag to move. Ctrl+click to multi-select for grouping, or Shift+drag a box '
                   '(Ctrl+Shift+drag adds to the selection), then Ctrl+G. '
                   'Drag empty space or use the right mouse button to pan; scroll to zoom.',
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
                p.save(); p.translate(leaf.x, leaf.y); p.rotate(leaf.r)  # the view is y-up, so +rotation is counter-clockwise, same as the 3D view
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


def raster_triangle(zbuf, cbuf, pts, depths, rgba):
    """Fill zbuf/cbuf in place with this triangle wherever it's the nearest thing seen so
    far at that pixel. pts: ((x0,y0),(x1,y1),(x2,y2)) screen coords; depths: (z0,z1,z2),
    smaller = nearer the camera. This replaces sorting whole faces back-to-front (which is
    what let a long face's single average position misplace it relative to other objects)
    with a genuine per-pixel depth test, so draw order stops mattering entirely.
    """
    h, w = zbuf.shape
    (x0, y0), (x1, y1), (x2, y2) = pts
    minx = max(int(math.floor(min(x0, x1, x2))), 0); maxx = min(int(math.ceil(max(x0, x1, x2))), w - 1)
    miny = max(int(math.floor(min(y0, y1, y2))), 0); maxy = min(int(math.ceil(max(y0, y1, y2))), h - 1)
    if minx > maxx or miny > maxy: return
    denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(denom) < 1e-9: return  # edge-on / degenerate triangle
    xs, ys = np.meshgrid(np.arange(minx, maxx + 1, dtype=np.float32), np.arange(miny, maxy + 1, dtype=np.float32))
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
    cbuf[miny:maxy + 1, minx:maxx + 1][nearer] = rgba


class Preview(QWidget):
    LIGHT = (.4, -.5, .77)

    def __init__(s, m):
        super().__init__()
        s.m, s.yaw, s.pitch, s.dist, s.last, s.act, s.space = m, .5, .55, 1100., None, None, False
        # _lod > 1 = render at 1/_lod resolution while orbiting/dragging (big speed-up on complex maps)
        s._lod, s._rw, s._rh, s._hires_timer = 1, None, None, QTimer(s)
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
        """Drop to half-res while the user is orbiting or dragging; full-res resumes shortly after."""
        if s._lod == 1:
            s._lod = 2
        s._hires_timer.stop()

    def _finish_lod(s):
        if s._lod != 1:
            s._lod = 1
            s.update()

    def _schedule_hires(s):
        s._hires_timer.start(120)  # ms of idle before a full-resolution redraw

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
            if o.t == 'zone':
                if s.m.show_zones:
                    F += s.zone_faces(o, P, z1)
                continue
            if o.t == 'arc':
                F += s.arc_faces(o, P, z0, z1, C); continue
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
        pos, f, _, _ = B; L = math.sqrt(sum(x * x for x in s.LIGHT))

        def tris(pts):  # a face is drawn as one or more planar triangles
            return [pts] if len(pts) <= 3 else [(pts[0], pts[i], pts[i + 1]) for i in range(1, len(pts) - 1)]

        lod = max(1, int(s._lod))
        full_w, full_h = max(1, s.width()), max(1, s.height())
        w_px, h_px = max(1, full_w // lod), max(1, full_h // lod)
        s._rw, s._rh = w_px, h_px  # proj() uses these while we rasterize at reduced size
        zbuf = np.full((h_px, w_px), np.inf, dtype=np.float32)
        cbuf = np.zeros((h_px, w_px, 4), dtype=np.uint8)
        sel_faces = []  # original (untriangulated) faces of the selected object, for its outline
        for pts, col, o, tex in s.solids():
            if o is s.m.sel: sel_faces.append(pts)
            for tri in tris(pts):
                # Back-face cull (opaque only). Camera looks along +f; outward normal n_ facing
                # the same way as f means the face is facing away and can be skipped.
                u = [tri[1][j] - tri[0][j] for j in range(3)]; w_ = [tri[2][j] - tri[0][j] for j in range(3)]
                n_ = (u[1] * w_[2] - u[2] * w_[1], u[2] * w_[0] - u[0] * w_[2], u[0] * w_[1] - u[1] * w_[0])
                if (col.alpha() or 255) >= 250 and sum(n_[i] * f[i] for i in range(3)) > 0:
                    continue
                screen = [s.proj(B, v) for v in tri]
                if None in screen: continue  # a vertex behind the camera: skip, same as before
                depths = [sum((tri[i][j] - pos[j]) * f[j] for j in range(3)) for i in range(3)]
                nl = math.sqrt(sum(x * x for x in n_)) or 1
                k = .5 + .5 * abs(sum(n_[i] * s.LIGHT[i] for i in range(3))) / (nl * L)
                rgba = (int(col.red() * k), int(col.green() * k), int(col.blue() * k), col.alpha() or 255)
                raster_triangle(zbuf, cbuf, [(pt.x(), pt.y()) for pt in screen], depths, rgba)
        s._rw = s._rh = None
        img = QImage(cbuf.data, w_px, h_px, w_px * 4, QImage.Format.Format_RGBA8888).copy()
        if lod > 1:
            img = img.scaled(full_w, full_h, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.FastTransformation)
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
                t = ray_box_hit(origin, direction, leaf.x, leaf.y, leaf.z, hx, hy, leaf.sz, leaf.r)
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
def average_color(img):
    """Mean color of an image, scaled down first so this stays cheap regardless of the
    source resolution. Used so a textured object shows roughly the right color in the 3D
    view instead of plain white, since the view doesn't actually draw texture images."""
    small = img.scaled(8, 8, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    small = small.convertToFormat(QImage.Format.Format_RGBA8888)
    n = small.width() * small.height()
    if not n: return (1.0, 1.0, 1.0, 1.0)
    r = g = b = 0
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y); r += c.red(); g += c.green(); b += c.blue()
    return (r / n / 255, g / n / 255, b / n / 255, 1.0)


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
        fetch = QPushButton('Fetch Color from URL'); fetch.clicked.connect(s.fetch_url_color)
        rgb = QHBoxLayout(); s.r = QDoubleSpinBox(); s.g = QDoubleSpinBox(); s.b = QDoubleSpinBox(); s.al = QDoubleSpinBox()
        for sp in (s.r, s.g, s.b, s.al): sp.setRange(0, 1); sp.setSingleStep(.05); sp.setValue(1); rgb.addWidget(sp)
        form = QFormLayout(); form.addRow('Name', s.name)
        trow = QHBoxLayout(); trow.addWidget(s.tex); trow.addWidget(browse); trow.addWidget(fetch)
        form.addRow('Texture', trow); form.addRow('Color R G B A', rgb)
        col.addLayout(form)
        s.texwarn = QLabel(); s.texwarn.setWordWrap(True); s.texwarn.setStyleSheet('color:#b5651d')
        col.addWidget(s.texwarn)
        btns = QHBoxLayout()
        add = QPushButton('Add / Update'); add.clicked.connect(s.save)
        rm = QPushButton('Remove'); rm.clicked.connect(s.remove)
        btns.addWidget(add); btns.addWidget(rm); col.addLayout(btns)
        note = QLabel('Material colors show in the 3D preview -- actual texture images are not drawn, so '
                       'Load PNG fills in the color automatically from the image (if you haven\'t set one '
                       'yourself), and Fetch Color from URL does the same for an internet texture.\n'
                       'Every other material setting (addtexture, texmat, ambient...) is kept in the file '
                       'but not drawn.\nFor a texture on the internet, paste its full URL (e.g. ' + s.APPROVED +
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
        pix = QPixmap(path)
        s.tex.setText(base); s.m.texcache[s.name.text() or base] = pix
        if (s.r.value(), s.g.value(), s.b.value(), s.al.value()) == (1.0, 1.0, 1.0, 1.0):
            r, g, b, a = average_color(pix.toImage())  # only fills in an untouched default color
            s.r.setValue(r); s.g.setValue(g); s.b.setValue(b); s.al.setValue(a)

    def fetch_url_color(s):
        """Explicit, one-off download -- this app makes no network calls on its own, so
        fetching an image URL only ever happens when you click this button."""
        url = s.tex.text().strip()
        if '://' not in url:
            QMessageBox.information(s, 'Fetch color', 'Paste a full image URL into the Texture field first.'); return
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={'User-Agent': 'BZMapMaker'})
            data = urllib.request.urlopen(req, timeout=6).read()
        except Exception as e:
            QMessageBox.warning(s, 'Fetch failed', "Couldn't download that image:\n%s" % e); return
        img = QImage()
        if not img.loadFromData(data):
            QMessageBox.warning(s, 'Fetch failed', "That didn't look like a valid image."); return
        r, g, b, a = average_color(img)
        s.r.setValue(r); s.g.setValue(g); s.b.setValue(b); s.al.setValue(a)
        s.texwarn.setText('Color fields set from the downloaded image -- click Add/Update to save it.')

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
        s.act(ob, 'Import Mesh Object...', s.import_mesh)
        s.act(ob, 'World Settings...', s.open_world)
        s.act(ob, 'Map Options...', s.open_options)
        s.act(ob, 'Physics Drivers...', s.open_physics)
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
        tb = s.addToolBar('Tools'); g = QActionGroup(s)
        for t in ('select', 'box', 'pyramid', 'base', 'teleporter', 'arc', 'cone', 'zone', 'weapon'):
            a = QAction(t.capitalize(), s); a.setCheckable(True); a.setChecked(t == 'select')
            a.triggered.connect(lambda _, t=t: setattr(s.m, 'tool', t)); g.addAction(a); tb.addAction(a)
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
        s.maptext = QPlainTextEdit(); s.maptext.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        s.maptext.setStyleSheet('font-family: monospace;')
        tl.addWidget(s.maptext)
        row = QHBoxLayout()
        refresh = QPushButton('Refresh from Map'); refresh.clicked.connect(s.refresh_maptext)
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
        s.textdock.visibilityChanged.connect(lambda vis: s.refresh_maptext() if vis else None)
        s.m.changed.connect(s.schedule_maptext_refresh)

    def schedule_maptext_refresh(s):
        if not s.textdock.isVisible() or s._text_pending: return
        s._text_pending = True
        QTimer.singleShot(300, s._do_maptext_refresh)

    def _do_maptext_refresh(s):
        s._text_pending = False
        if s.textdock.isVisible(): s.refresh_maptext()

    def refresh_maptext(s):
        s.maptext.setPlainText(s.m.text())

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
        setattr(o, k, v)
        if not s.editing_member and k not in ('link', 'blink', 'lface', 'bface', 'name'): s.m.sync_family(o)
        s.busy = True; s.m.changed.emit(); s.busy = False

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
        rows = {'x': True, 'y': True, 'z': True, 'sx': o and o.t not in ('group', 'mesh', 'weapon'),
                'sy': o and o.t not in ('group', 'mesh', 'weapon'), 'sz': o and o.t not in ('group', 'mesh', 'weapon'),
                'r': o and o.t != 'mesh', 'team': o and o.t == 'base',
                'gteam': o and o.t == 'group', 'gname': o and o.t == 'group', 'member_combo': grp is not None,
                'link': o and o.t == 'teleporter', 'blink': o and o.t == 'teleporter',
                'div': o and o.t in ('arc', 'cone'), 'ang': o and o.t == 'arc', 'rat': o and o.t == 'arc',
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
                      'link': s.link_row, 'blink': s.blink_row, 'div': s.div, 'ang': s.ang, 'rat': s.rat, 'mat': s.mat,
                      'flip': s.flip, 'phys': s.phys, 'zteam': s.zteam, 'zsafety': s.zsafety,
                      'zflags': s.zflags, 'ricochet': s.ricochet, 'phydrv_combo': s.phydrv_combo,
                      'wcolor': s.wcolor, 'tilt': s.tilt, 'initdelay': s.initdelay, 'wdelay': s.wdelay,
                      'wtype': s.wtype, 'trigger': s.trigger, 'eventteam': s.eventteam,
                      'match_color_btn': s.match_color_btn, 'mirror_phys_btn': s.mirror_phys_btn,
                      'tintrow': s.tint_widget}[name]
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
                cb.addItems(s.m.tele_names())  # includes o itself (a teleporter may link to itself) and ones inside groups
                cb.setCurrentText(cur or 'Nothing')
            s.lfacec.setCurrentIndex(0 if o.lface == 'b' else 1); s.bfacec.setCurrentIndex(0 if o.bface == 'b' else 1)
            s.div.setValue(o.divisions); s.ang.setValue(o.angle if o.angle else 360); s.rat.setValue(o.ratio)
            s.mat.clear(); s.mat.addItems([''] + sorted(s.m.materials)); s.mat.setCurrentText(o.matref)
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

    def open_world(s):
        old = s.m.W
        if WorldDialog(s.m).exec() and s.m.W != old: s.ed.fit()

    def open_options(s):
        OptionsDialog(s.m).exec()

    def open_physics(s):
        PhysicsDialog(s.m).exec()

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
        nw, objs, materials, defines, extras, options_text, physics, _world = parse(text)
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
        s.m.texcache = {}
        if not folder: return
        for name, mat in s.m.materials.items():
            if mat.texture:
                p = os.path.join(folder, mat.texture + '.png')
                if os.path.isfile(p):
                    pix = QPixmap(p); s.m.texcache[name] = pix
                    if tuple(mat.color) == (1.0, 1.0, 1.0, 1.0):  # color untouched -> use the texture's own
                        mat.color = average_color(pix.toImage())

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
