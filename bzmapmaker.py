#!/usr/bin/env python3
"""BZ Map Maker - a BZFlag .bzw editor with a 2D layout view and a live 3D preview.

Run:  pip install PyQt6   then   python bzmapmaker.py [optional_map.bzw]
Works on Windows, macOS and Linux. The 3D preview is drawn in software, so no OpenGL is needed.
"""
import sys, math, json
from dataclasses import dataclass, asdict
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF, QAction, QActionGroup, QKeySequence
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QSplitter, QDockWidget, QFormLayout,
                             QDoubleSpinBox, QComboBox, QFileDialog, QMessageBox)

DEF = {'box': (10, 10, 9.4), 'pyramid': (8.2, 8.2, 8.2), 'base': (20, 20, 0), 'teleporter': (.5, 4, 10)}
TEAM = ['', '#c8453b', '#3c9a5a', '#3a6fc4', '#8a55b5']
TEAM_NAMES = ['', 'Red', 'Green', 'Blue', 'Purple']
TSWAP = {1: 2, 2: 1, 3: 4, 4: 3}  # a twin base plays for the opposing team
SYM_MODES = ['Symmetry: off', 'Symmetry: rotate 180°', 'Symmetry: mirror left-right', 'Symmetry: mirror top-bottom']
COL = {'box': '#8d939a', 'pyramid': '#b08a4e', 'teleporter': '#d0702a'}
ACC = QColor('#0f6b6b')


@dataclass
class Obj:
    t: str
    x: float = 0; y: float = 0; z: float = 0
    sx: float = 1; sy: float = 1; sz: float = 1
    r: float = 0; team: int = 1; name: str = ''; link: str = ''
    blink: str = ''  # teleporter: where the back face leads
    uid: int = 0; twin: int = 0; sym: int = 0  # twin = uid of the mirrored partner


def num(v):
    return ('%.3f' % v).rstrip('0').rstrip('.') or '0'


def parse(text):
    """Read box, pyramid, base, teleporter, link and world blocks from BZW text."""
    res, links, nw, cur = [], [], None, None

    def nm(a, d):
        out = []
        for i, v in enumerate(d):
            try: out.append(float(a[i]))
            except (IndexError, ValueError, TypeError): out.append(v)
        return out

    for ln in text.splitlines():
        p = ln.split('#')[0].split()
        if not p: continue
        k = p[0].lower()
        if cur is None:
            if k in ('world', 'box', 'pyramid', 'base', 'teleporter', 'link'):
                cur = [k, p[1] if len(p) > 1 else '', {}]
            continue
        if k != 'end':
            cur[2][k] = p[1:]; continue
        kind, name, v = cur; cur = None
        if v.get('name'): name = v['name'][0]
        if kind == 'world':
            try: nw = float(v['size'][0])
            except (KeyError, IndexError, ValueError): pass
        elif kind == 'link':
            links.append((v.get('from', [''])[0], v.get('to', [''])[0]))
        else:
            x, y, z = nm(v.get('position', v.get('pos')), (0, 0, 0))
            sx, sy, sz = nm(v.get('size'), DEF[kind])
            r = nm(v.get('rotation', v.get('rot')), (0,))[0]
            team = int(nm(v.get('color'), (1,))[0])
            res.append(Obj(kind, x, y, z, sx, sy, sz, r, team if 1 <= team <= 4 else 1, name))
    seen = set()
    for o in res:  # teleporter names must be unique for links to work
        if o.t == 'teleporter':
            if o.name in seen: o.name = ''
            seen.add(o.name)
    i = 1
    for o in res:
        if o.t == 'teleporter' and not o.name:
            while 't%d' % i in seen: i += 1
            o.name = 't%d' % i; seen.add(o.name)
    for f, t in links:
        fn, _, face = f.rpartition(':'); tn = t.rpartition(':')[0] or t
        for o in res:
            if o.t == 'teleporter' and o.name == fn:
                if face.lower() == 'f': o.link = tn
                elif face.lower() == 'b': o.blink = tn
    return nw, res


class Model(QObject):
    changed = pyqtSignal(); selected = pyqtSignal(); reset = pyqtSignal()

    def __init__(s):
        super().__init__()
        s.W, s.objs, s.sel, s.undo, s.path = 400, [], None, [], None
        s.nid, s.sym = 1, 0
        s.starter()

    def starter(s):
        s.W, s.objs, s.sel = 400, [], None

    def push(s):
        s.undo.append(json.dumps({'W': s.W, 'objs': [asdict(o) for o in s.objs]})); del s.undo[:-80]

    def pop(s):
        if not s.undo: return
        d = json.loads(s.undo.pop())
        s.W, s.objs, s.sel = d['W'], [Obj(**o) for o in d['objs']], None
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

    def delete(s):
        if s.sel:
            s.push(); q = s.twin_of(s.sel); dead = [s.sel] + ([q] if q else [])
            names = {d.name for d in dead if d.name}
            s.objs = [o for o in s.objs if all(o is not d for d in dead)]
            for o in s.objs:
                if o.link in names: o.link = ''
                if o.blink in names: o.blink = ''
            s.sel = None; s.selected.emit(); s.changed.emit()

    def twin_of(s, o):
        return next((q for q in s.objs if o.twin and q.uid == o.twin), None)

    def sync_twin(s, o):
        """Copy o's shape onto its twin, mirrored according to the pair's symmetry mode."""
        q = s.twin_of(o)
        if not q: return
        q.z, q.sx, q.sy, q.sz = o.z, o.sx, o.sy, o.sz
        if o.sym == 1: q.x, q.y, q.r = -o.x, -o.y, (o.r + 180) % 360
        elif o.sym == 2: q.x, q.y, q.r = -o.x, o.y, (180 - o.r) % 360
        else: q.x, q.y, q.r = o.x, -o.y, (-o.r) % 360
        q.team = TSWAP[o.team]
        if o.t == 'teleporter': q.r = (o.r + 180) % 360  # twin faces the opposite way so front/back line up

    def make_twin(s, o):
        """If symmetry is on, add the mirrored partner of o (skipped when it would land on o itself)."""
        if not s.sym or o.twin: return
        q = s.add(o.t, 0, 0); o.sym = q.sym = s.sym; o.twin, q.twin = q.uid, o.uid
        s.sync_twin(o)
        if abs(q.x - o.x) < .01 and abs(q.y - o.y) < .01:
            s.objs.remove(q); o.twin = o.sym = 0; return
        if o.t == 'teleporter': o.link, q.link, o.blink, q.blink = q.name, o.name, q.name, o.name  # front and back both link to the twin

    def duplicate(s):
        if s.sel:
            s.push(); o = s.add(s.sel.t, s.sel.x + 10, s.sel.y - 10)
            for k in ('z', 'sx', 'sy', 'sz', 'r', 'team'): setattr(o, k, getattr(s.sel, k))
            s.make_twin(o); s.select(o); s.changed.emit()

    def load(s, text):
        nw, res = parse(text)
        s.push(); s.W = nw or s.W; s.objs = res; s.sel = None
        for o in res: o.uid = s.nid; s.nid += 1
        s.reset.emit(); s.selected.emit(); s.changed.emit()

    def text(s):
        out = ['# Made with BZ Map Maker', 'world', '  size %s' % num(s.W), 'end', '']
        for o in s.objs:
            out += [o.t] + (['  name ' + o.name] if o.t == 'teleporter' else [])
            out += ['  position %s %s %s' % (num(o.x), num(o.y), num(o.z)),
                    '  size %s %s %s' % (num(o.sx), num(o.sy), num(o.sz)), '  rotation ' + num(o.r)]
            if o.t == 'base': out.append('  color %d' % o.team)
            out += ['end', '']
        for o in s.objs:
            if o.t == 'teleporter':
                for face, tgt, other in (('f', o.link, 'b'), ('b', o.blink, 'f')):
                    if tgt: out += ['link', '  from %s:%s' % (o.name, face), '  to %s:%s' % (tgt, other), 'end', '']
        return '\n'.join(out)


def cpen(color, w=1, style=Qt.PenStyle.SolidLine):
    p = QPen(QColor(color), w, style); p.setCosmetic(True); return p


class Editor(QWidget):
    """Top-down layout view: pick a tool, then click or drag."""

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
        for o in reversed(s.m.objs):
            a = math.radians(o.r); dx, dy = wx - o.x, wy - o.y
            lx = dx * math.cos(a) + dy * math.sin(a); ly = -dx * math.sin(a) + dy * math.cos(a)
            if abs(lx) <= max(o.sx, 1) + pad and abs(ly) <= o.sy + pad: return o

    def paintEvent(s, _):
        p = QPainter(s); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = s.m.W * 2; h = s.m.W; S = s.v[2]  # 'size' is the distance from the center to each edge
        p.fillRect(s.rect(), QColor('#e9e7e0'))
        p.translate(s.width() / 2 + s.v[0], s.height() / 2 + s.v[1]); p.scale(S, -S)
        p.fillRect(QRectF(-h, -h, W, W), QColor('#f6f5f0'))
        p.setPen(cpen('#dedbd1')); g = 10 if S * 10 >= 8 else 50; v = -h
        while v <= h + .01:
            p.drawLine(QPointF(v, -h), QPointF(v, h)); p.drawLine(QPointF(-h, v), QPointF(h, v)); v += g
        p.setPen(cpen('#666d74', 1.5)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawRect(QRectF(-h, -h, W, W))
        p.setPen(cpen('#d0702a', 1, Qt.PenStyle.DashLine))
        for o in s.m.objs:
            for tgt in (o.link, o.blink):
                q = next((z for z in s.m.objs if o.t == 'teleporter' and tgt and z.name == tgt), None)
                if q: p.drawLine(QPointF(o.x, o.y), QPointF(q.x, q.y))
        for o in s.m.objs:
            p.save(); p.translate(o.x, o.y); p.rotate(o.r)
            a = max(o.sx, 1.2 / S if o.t == 'teleporter' else 0); b = o.sy
            c = QColor(TEAM[o.team] if o.t == 'base' else COL[o.t])
            if o.t == 'base': c.setAlpha(140)
            p.setPen(cpen('#22262b')); p.setBrush(c); p.drawRect(QRectF(-a, -b, 2 * a, 2 * b))
            if o.t == 'pyramid':
                p.drawLine(QPointF(-a, -b), QPointF(a, b)); p.drawLine(QPointF(a, -b), QPointF(-a, b))
            if o is not s.m.sel and s.m.sel and o.uid == s.m.sel.twin:
                pad = 3 / S; p.setPen(cpen(ACC, 1.5, Qt.PenStyle.DotLine)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(-a - pad, -b - pad, 2 * a + 2 * pad, 2 * b + 2 * pad))
            if o is s.m.sel:
                pad = 3 / S; p.setPen(cpen(ACC, 2.5, Qt.PenStyle.DashLine)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(-a - pad, -b - pad, 2 * a + 2 * pad, 2 * b + 2 * pad))
            p.restore()
        d = s.drag
        if d and d[0] == 'make':
            (ax, ay), (bx, by) = d[1], d[2]; p.setPen(cpen(ACC, 1.5, Qt.PenStyle.DashLine)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(min(ax, bx), min(ay, by), abs(ax - bx), abs(ay - by)))
        if S > 1:
            p.resetTransform(); p.setPen(QColor('#22262b'))
            for o in s.m.objs:
                if o.t == 'teleporter':
                    p.drawText(QPointF(s.width() / 2 + s.v[0] + (o.x + o.sx + 3 / S) * S, s.height() / 2 + s.v[1] - o.y * S), o.name)

    def mousePressEvent(s, e):
        s.setFocus(); p = e.position(); wx, wy = s.tow(p); b = e.button()
        pan = ('pan', p.x(), p.y(), s.v[0], s.v[1])
        if b in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton) or s.space:
            s.drag = pan; return
        if b != Qt.MouseButton.LeftButton: return
        m = s.m
        if s.tool == 'select':
            o = s.hit(wx, wy); m.select(o)
            if o: m.push(); s.drag = ('move', o, o.x - wx, o.y - wy)
            else: s.drag = pan
        elif s.tool in ('box', 'pyramid'):
            a = (s.sn(wx), s.sn(wy)); s.drag = ['make', a, a, p.x(), p.y()]
        else:
            m.push(); o = m.add(s.tool, s.sn(wx), s.sn(wy)); m.make_twin(o); m.select(o); m.changed.emit()

    def mouseMoveEvent(s, e):
        d = s.drag
        if not d: return
        p = e.position(); wx, wy = s.tow(p)
        if d[0] == 'pan': s.v[0] = d[3] + p.x() - d[1]; s.v[1] = d[4] + p.y() - d[2]; s.update()
        elif d[0] == 'move':
            d[1].x = s.sn(wx + d[2]); d[1].y = s.sn(wy + d[3]); s.m.sync_twin(d[1]); s.m.changed.emit()
        else: d[2] = (s.sn(wx), s.sn(wy)); s.update()

    def mouseReleaseEvent(s, e):
        d = s.drag; s.drag = None
        if d and d[0] == 'make':
            (ax, ay), (bx, by) = d[1], d[2]; p = e.position()
            s.m.push(); o = s.m.add(s.tool, ax, ay)
            if math.hypot(p.x() - d[3], p.y() - d[4]) > 4 and ax != bx and ay != by:
                o.x, o.y, o.sx, o.sy = (ax + bx) / 2, (ay + by) / 2, abs(ax - bx) / 2, abs(ay - by) / 2
            s.m.make_twin(o); s.m.select(o)
        s.m.changed.emit()

    def wheelEvent(s, e):
        p = e.position(); wx, wy = s.tow(p)
        S = min(14, max(.25, s.v[2] * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15)))
        s.v = [p.x() - s.width() / 2 - wx * S, p.y() - s.height() / 2 + wy * S, S]; s.update()

    def keyPressEvent(s, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat(): s.space = True

    def keyReleaseEvent(s, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat(): s.space = False


class Preview(QWidget):
    """Orbit-camera 3D view, drawn with a painter's algorithm (no OpenGL needed)."""
    LIGHT = (.4, -.5, .77)

    def __init__(s, m):
        super().__init__()
        s.m, s.yaw, s.pitch, s.dist, s.last = m, .5, .55, 1100., None
        s.setMinimumSize(320, 240)
        m.changed.connect(s.update); m.selected.connect(s.update)

    def basis(s):
        cp = math.cos(s.pitch); n = s.dist
        pos = (n * cp * math.sin(s.yaw), -n * cp * math.cos(s.yaw), n * math.sin(s.pitch))
        f = (-pos[0] / n, -pos[1] / n, -pos[2] / n); l = math.hypot(f[1], f[0])
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
        F = []
        for o in s.m.objs:
            a = math.radians(o.r); c, sn = math.cos(a), math.sin(a)
            P = lambda lx, ly, z: (o.x + lx * c - ly * sn, o.y + lx * sn + ly * c, z)
            col = QColor(TEAM[o.team] if o.t == 'base' else COL[o.t]); z0 = o.z; z1 = z0 + max(o.sz, .3)
            b = [P(-o.sx, -o.sy, z0), P(o.sx, -o.sy, z0), P(o.sx, o.sy, z0), P(-o.sx, o.sy, z0)]
            if o.t == 'pyramid':
                ap = P(0, 0, z1)
                F += [([b[i], b[(i + 1) % 4], ap], col, o) for i in range(4)] + [(b, col, o)]
            else:
                t = [(x, y, z1) for x, y, _ in b]
                F += [(b, col, o), (t, col, o)] + [([b[i], b[(i + 1) % 4], t[(i + 1) % 4], t[i]], col, o) for i in range(4)]
        h = s.m.W; n = 8; wc = QColor(122, 133, 144, 120)
        for i in range(n):
            a, b2 = -h + 2 * h * i / n, -h + 2 * h * (i + 1) / n
            for e1, e2 in (((a, -h), (b2, -h)), ((a, h), (b2, h)), ((-h, a), (-h, b2)), ((h, a), (h, b2))):
                F.append(([(*e1, 0), (*e2, 0), (*e2, 6), (*e1, 6)], wc, None))
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
        def dist(f):
            cx = [sum(v[i] for v in f[0]) / len(f[0]) - pos[i] for i in range(3)]; return sum(x * x for x in cx)
        for pts, col, o in sorted(s.solids(), key=dist, reverse=True):
            pg = poly(pts)
            if pg is None: continue
            a, b, c = pts[:3]; u = [b[i] - a[i] for i in range(3)]; w = [c[i] - a[i] for i in range(3)]
            n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
            nl = math.sqrt(sum(x * x for x in n)) or 1
            k = .5 + .5 * abs(sum(n[i] * s.LIGHT[i] for i in range(3))) / (nl * L)
            sh = QColor(int(col.red() * k), int(col.green() * k), int(col.blue() * k), col.alpha())
            p.setBrush(sh); p.setPen(QPen(ACC.lighter(150), 2) if o is s.m.sel else QPen(QColor(0, 0, 0, 90), 1))
            p.drawPolygon(pg)
        p.setPen(QColor('#98a0a8')); p.drawText(10, s.height() - 10, 'Drag to orbit, scroll to zoom')

    def mousePressEvent(s, e): s.last = e.position()

    def mouseMoveEvent(s, e):
        if s.last is None: return
        p = e.position(); s.yaw -= (p.x() - s.last.x()) * .008
        s.pitch = min(1.5, max(.05, s.pitch + (p.y() - s.last.y()) * .008)); s.last = p; s.update()

    def wheelEvent(s, e):
        s.dist = min(8000, max(40, s.dist * (.9 if e.angleDelta().y() > 0 else 1 / .9))); s.update()


class Win(QMainWindow):
    FIELDS = (('x', 'X'), ('y', 'Y'), ('z', 'Z'), ('sx', 'Half-width X'), ('sy', 'Half-width Y'), ('sz', 'Height'), ('r', 'Rotation'))

    def __init__(s, path=None):
        super().__init__(); s.m = Model(); s.resize(1300, 780)
        s.ed, s.pv = Editor(s.m), Preview(s.m)
        sp = QSplitter(); sp.addWidget(s.ed); sp.addWidget(s.pv); sp.setSizes([650, 650]); s.setCentralWidget(sp)
        s.busy = s.pushed = False; s.build_menus(); s.build_panel()
        s.m.selected.connect(s.on_select); s.m.changed.connect(s.refresh)
        if path: s.open_file(path)
        s.title()

    def act(s, menu, label, fn, key=None, checkable=False):
        a = QAction(label, s); a.triggered.connect(fn)
        if key: a.setShortcut(QKeySequence(key))
        a.setCheckable(checkable); menu.addAction(a); return a

    def build_menus(s):
        f = s.menuBar().addMenu('&File')
        s.act(f, 'New', s.new, 'Ctrl+N'); s.act(f, 'Open...', lambda: s.open_file(), 'Ctrl+O')
        s.act(f, 'Save', s.save, 'Ctrl+S'); s.act(f, 'Save As...', lambda: s.save(True), 'Ctrl+Shift+S')
        e = s.menuBar().addMenu('&Edit')
        s.act(e, 'Undo', s.m.pop, 'Ctrl+Z'); s.act(e, 'Duplicate', s.m.duplicate, 'Ctrl+D'); s.act(e, 'Delete', s.m.delete, 'Delete')
        s.act(e, 'Fit view', s.ed.fit, 'Ctrl+0')
        tb = s.addToolBar('Tools'); g = QActionGroup(s)
        for t in ('select', 'box', 'pyramid', 'base', 'teleporter'):
            a = QAction(t.capitalize(), s); a.setCheckable(True); a.setChecked(t == 'select')
            a.triggered.connect(lambda _, t=t: setattr(s.ed, 'tool', t)); g.addAction(a); tb.addAction(a)
        tb.addSeparator(); sn = QAction('Snap to 5', s); sn.setCheckable(True); sn.setChecked(True)
        sn.toggled.connect(lambda v: setattr(s.ed, 'snap', v)); tb.addAction(sn)
        tb.addSeparator(); sy = QComboBox(); sy.addItems(SYM_MODES); sy.setToolTip('New objects get a mirrored twin that follows every edit')
        sy.currentIndexChanged.connect(lambda i: setattr(s.m, 'sym', i)); tb.addWidget(sy)
        tb.addSeparator(); s.ws = QDoubleSpinBox(); s.ws.setRange(50, 5000); s.ws.setSingleStep(50); s.ws.setValue(400)
        s.ws.setPrefix('World size (half-width) '); s.ws.valueChanged.connect(s.set_world); tb.addWidget(s.ws)

    def build_panel(s):
        w = QWidget(); fl = QFormLayout(w); s.sp = {}
        for k, l in s.FIELDS:
            b = QDoubleSpinBox(); b.setRange(-5000, 5000); b.setDecimals(3); b.setSingleStep(1)
            b.valueChanged.connect(lambda v, k=k: s.edit(k, v)); fl.addRow(l, b); s.sp[k] = b
        s.team = QComboBox(); s.team.addItems(TEAM_NAMES[1:]); s.team.currentIndexChanged.connect(lambda i: s.edit('team', i + 1))
        s.link = QComboBox(); s.link.currentTextChanged.connect(lambda t: s.edit('link', '' if t == 'Nothing' else t))
        s.blink = QComboBox(); s.blink.currentTextChanged.connect(lambda t: s.edit('blink', '' if t == 'Nothing' else t))
        fl.addRow('Team', s.team); fl.addRow('Front links to', s.link); fl.addRow('Back links to', s.blink)
        d = QDockWidget('Selected object'); d.setWidget(w); s.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d)
        s.dock = d; s.on_select()

    def edit(s, k, v):
        o = s.m.sel
        if s.busy or not o: return
        if not s.pushed: s.m.push(); s.pushed = True
        setattr(o, k, v)
        if k not in ('link', 'blink'): s.m.sync_twin(o)
        s.busy = True; s.m.changed.emit(); s.busy = False

    def on_select(s):
        s.pushed = False; s.refresh()

    def refresh(s):
        if s.busy: return
        o = s.m.sel; s.busy = True
        s.dock.widget().setEnabled(o is not None)
        s.dock.setWindowTitle('Selected object' + (': ' + o.t + (' ' + o.name if o.name else '') if o else ''))
        if o:
            for k, b in s.sp.items(): b.setValue(getattr(o, k))
            s.team.setEnabled(o.t == 'base'); s.team.setCurrentIndex(o.team - 1)
            for cb, cur in ((s.link, o.link), (s.blink, o.blink)):
                cb.setEnabled(o.t == 'teleporter'); cb.clear(); cb.addItem('Nothing')
                cb.addItems([q.name for q in s.m.objs if q.t == 'teleporter' and q is not o])
                cb.setCurrentText(cur or 'Nothing')
        s.ws.setValue(s.m.W); s.busy = False

    def set_world(s, v):
        if not s.busy and v != s.m.W: s.m.push(); s.m.W = v; s.ed.fit(); s.m.changed.emit()

    def title(s): s.setWindowTitle('BZ Map Maker - ' + (s.m.path or 'untitled'))

    def new(s):
        s.m.push(); s.m.starter(); s.m.path = None; s.m.reset.emit(); s.m.selected.emit(); s.m.changed.emit(); s.title()

    def open_file(s, path=None):
        path = path or QFileDialog.getOpenFileName(s, 'Open world', '', 'BZFlag worlds (*.bzw);;All files (*)')[0]
        if not path: return
        try:
            with open(path, encoding='utf-8', errors='replace') as f: s.m.load(f.read())
        except OSError as e:
            QMessageBox.warning(s, 'Open failed', str(e)); return
        s.m.path = path; s.title()

    def save(s, as_new=False):
        path = s.m.path
        if as_new or not path:
            path = QFileDialog.getSaveFileName(s, 'Save world', 'map.bzw', 'BZFlag worlds (*.bzw)')[0]
        if not path: return
        try:
            with open(path, 'w', encoding='utf-8') as f: f.write(s.m.text())
        except OSError as e:
            QMessageBox.warning(s, 'Save failed', str(e)); return
        s.m.path = path; s.title()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = Win(sys.argv[1] if len(sys.argv) > 1 else None); w.show()
    sys.exit(app.exec())
