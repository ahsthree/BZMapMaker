# BZ Map Maker

A cross-platform map editor for [BZFlag](https://www.bzflag.org/), inspired by the classic BZEdit. It edits `.bzw` world files, lets you build and edit directly in a live 3D view, and can build symmetrical (balanced) capture-the-flag maps for you automatically.

> Unofficial fan project, not affiliated with the BZFlag project.

<img width="1920" height="1051" alt="Screenshot From 2026-09-30 19-43-21" src="https://github.com/user-attachments/assets/25c6992b-4673-46d5-942a-45d08dc6e006" />


<img width="1920" height="1051" alt="Screenshot From 2026-09-30 19-44-01" src="https://github.com/user-attachments/assets/0d1c4358-5a21-4fcc-ab69-987267525b4a" />


## What's new

- **Edit directly in 3D**, with the 2D view available either side by side or as a small radar overlay (**View** menu).
- **A real per-pixel depth buffer** for the 3D view (built with NumPy), replacing the old approach of sorting whole shapes — overlapping objects, especially long thin ones, now always render in the correct order.
- **Map Text panel**, tabbed with the Selected Object panel: view or hand-edit the raw `.bzw` text directly, with **Apply to Map** that can automatically build symmetry for anything new you typed, using whatever mode is active in the toolbar.
- **Textures now show a real color** instead of plain white in the 3D view — loading a local PNG or fetching one from a URL fills in its average color automatically (the actual image still isn't drawn, just its color).
- **Ctrl+click multi-select works in the 3D view too**, and 3D picking uses true ray depth — so an object hidden under another in the flat 2D view can be reached by orbiting to an angle where it's exposed and clicking it directly.
- **3-, and 4-team rotational symmetry**, generalized from the original 2-team mode, plus an axis compass in the 2D view and a live-turning gizmo in the 3D view.
- **Arcs, cones, groups (reusable prefabs), and mesh objects** are now readable, writable, and (except meshes and prefabs) fully editable.
- Older maps' teleporter names and numeric-style links are now read correctly and upgraded to the modern format on save.
- Recent Files list, persisted between sessions.

## Features

- **Edit directly in 3D.** Click to select, drag to move, Shift+drag to rotate, drag to place and size new objects — all in the same 3D view you use to look at your map. Orbit with the right or middle mouse button (or Space+drag), and zoom with the scroll wheel.
- **2D layout view**, either side by side with the 3D view or as a small fixed radar overlay in a corner of your choice.
- **Objects:** boxes, pyramids, arcs, cones, team bases (red, green, blue, purple), teleporters, and prefab groups.
- **Meshes:** opens `mesh` blocks from other maps (or a Modeltool-converted model) and keeps every normal, texture coordinate, and `drawinfo` byte-for-byte, even though the editor itself can't reshape one.
- **Symmetry modes** that build a balanced map for you: 2, 3, or 4-team rotation, or a left-right/top-bottom mirror. Move one copy and the others follow.
- **Materials**, including separate materials per face on boxes and arcs, a texture field that accepts a pasted image URL, and automatic color-from-texture so a textured object doesn't just render white.
- **A real depth buffer** for the 3D view, so overlapping objects always render in the correct order — no more long, thin objects glitching through nearby ones.
- **Map Text panel** for viewing or hand-editing the raw `.bzw` text, with optional automatic symmetry for anything new you type.
- **Real files:** open, edit, and save `.bzw` worlds, including ones from other tools or from years-old maps. Undo, duplicate, snap-to-grid, recent-files list.
- **Import Mesh Object** to pull a mesh out of another `.bzw` file and drop it into the map you're working on.

## Installation

You need **Python 3.9 or newer**, **PyQt6**, and **NumPy** (NumPy powers the 3D view's depth buffer — it's a very common, lightweight math library, not a graphics driver; nothing here needs a GPU or OpenGL). Then download `bzmapmaker.py` (or clone this repository) and run it.

```
python bzmapmaker.py                # start with a blank map
python bzmapmaker.py mymap.bzw      # open an existing map
```

### Chromebook (Linux / Crostini)

1. Turn on Linux: **Settings → Advanced → Developers → Linux development environment → Turn on**.
2. Open the **Terminal** app and run:
   ```
   sudo apt update
   sudo apt install python3-pyqt6 python3-numpy
   ```
3. Go to the folder with the script and run `python3 bzmapmaker.py`.

Use the `apt` packages rather than `pip` here. On Chromebooks (which may be ARM), pip can leave a half-installed PyQt6 that fails with `No module named 'PyQt6.QtCore'`. If you already tried pip, remove it first:
```
pip uninstall PyQt6 PyQt6-Qt6 PyQt6-sip numpy
```

### Windows

1. Install Python from [python.org](https://www.python.org/downloads/). On the first installer screen, tick **Add python.exe to PATH**.
2. Open **Command Prompt** or **PowerShell** and run:
   ```
   py -m pip install PyQt6 numpy
   py bzmapmaker.py
   ```

### macOS

1. Install Python 3 from [python.org](https://www.python.org/downloads/) or with Homebrew (`brew install python`).
2. In Terminal:
   ```
   python3 -m pip install PyQt6 numpy
   python3 bzmapmaker.py
   ```

### Linux (Debian, Ubuntu and derivatives)

```
sudo apt install python3-pyqt6 python3-numpy
python3 bzmapmaker.py
```

Other distributions: `sudo dnf install python3-pyqt6 python3-numpy` (Fedora) or `sudo pacman -S python-pyqt6 python-numpy` (Arch).

If you prefer pip, use a virtual environment (newer distributions refuse system-wide pip installs):
```
python3 -m venv venv && source venv/bin/activate
pip install PyQt6 numpy
python bzmapmaker.py
```
If Qt complains about a missing `xcb` plugin, run `sudo apt install libxcb-cursor0 libgl1`.

### Troubleshooting

| Message | Fix |
|---|---|
| `No module named 'PyQt6.QtCore'` | Broken pip install. Uninstall it (see Chromebook above) and use your system package. |
| `No module named 'numpy'` | Install it the same way you installed PyQt6 (`pip install numpy` or `apt install python3-numpy`). |
| `externally-managed-environment` | Use the `apt`/`dnf`/`pacman` package, or a virtual environment. |
| `Could not load the Qt platform plugin "xcb"` | `sudo apt install libxcb-cursor0` |
| `python` not found on Windows | Use `py` instead, or reinstall Python with "Add to PATH" ticked. |

## Using the editor

### Building a map

The 3D view is where you build. Pick a tool in the toolbar: **Select, Box, Pyramid, Base, Teleporter, Arc,** or **Cone**.

| Action | In the 3D view | In the 2D view |
|---|---|---|
| Select an object | Click it | Click it |
| Add to the selection (for grouping) | Ctrl+click | Ctrl+click |
| Move the selected object | Drag it | Drag it |
| Rotate the selected object | Shift+drag | Edit "Rotation" in the panel |
| Place a Box/Pyramid/Arc/Cone | Drag corner to corner, or click for a default size | Drag corner to corner, or click for a default size |
| Place a Base/Teleporter | Click | Click |
| Orbit the camera | Right or middle mouse button, or Space+drag | — |
| Pan the view | — | Drag empty space, or right/middle mouse button, or Space+drag |
| Zoom | Scroll wheel | Scroll wheel |

Once an object is selected, use the **Selected object** panel on the right for exact position, size, height, rotation, team, material, and (for teleporters) links. Resizing an *existing* object is done there, not by dragging a corner in 3D.

**3D picking uses real depth**, not just what's drawn on top — so if something is hidden underneath another object in the flat 2D view, orbit the 3D camera to an angle where its side or top is exposed and click it directly.

When you're done: **File → Save**, then run the map with BZFlag's server: `bzfs -world mymap.bzw`.

### View menu: side by side, or 3D with a 2D radar

- **Side by Side** (the default) shows the 2D and 3D views next to each other.
- **3D View with 2D Radar** fills the window with the 3D view and shows a small, fixed, non-interactive 2D minimap in one corner, for orientation while you edit entirely in 3D.
- **Radar Position** sets which corner (top-left, top-right, bottom-left, bottom-right).

Your choice is remembered the next time you open the app.

### The axis compass and gizmo

Both views show which way is which. The 2D view has a small fixed compass (+X/−X/+Y/−Y) in the top-left corner, since that view never rotates. The 3D view has a small circular gizmo, also top-left, that turns live as you orbit — a solid colored dot with its letter marks the positive end of each axis (X, Y, Z), a hollow dot marks the negative end.

### World size

The **World size** box follows the BZFlag convention: it's the distance from the center to each edge. `size 400` gives a world that runs from −400 to 400 on both axes (800 wide). The center is always 0, 0.

### Object sizes

`Half-width X` and `Half-width Y` are half the object's extent, as in `.bzw` files. A box with `size 10 10 9.4` is 20 by 20 on the ground. `Height` is the full height. Rotation is in degrees.

### Teleporters

Each teleporter has a unique name and two link settings, **Front links to** and **Back links to**, written as `link` blocks. Opening an older map that names a teleporter on its own line (`teleporter home`) or links by plain number (`from 0` / `to 1`) instead of by name still works — those names and links are read correctly, and saving rewrites them in the modern, named style automatically.

### Arcs and cones

Arcs have a **Sweep angle** (360° is a full circle) and a **Hollow ratio** (0 is solid, closer to 1 is a thin ring) plus **Divisions** for how many segments make up the curve. Cones just have **Divisions**. Both can have a single all-faces material, or (arcs only) separate materials per face — see Materials below.

### Groups

Select two or more objects (Ctrl+click each one), then **Edit → Group Selected** (Ctrl+G). This saves their layout as a reusable prefab (a `define` block, positioned relative to their combined center) and replaces them with a single **group** instance you can move and rotate as a whole. A group can optionally override the team color of every base inside it — set this in the panel's **Group team override** field. Groups currently can't be edited back into their individual pieces or reshaped after creation; delete the instances and edit the file by hand if you need to change a prefab.

### Symmetry modes

Choose a mode from the toolbar drop-down **before** placing objects (or before dragging an object that doesn't have one yet — dragging it while a mode is active gives it a fresh, correctly centered copy). Every new object gets one or more **siblings** that stay linked: moving, resizing, rotating, or deleting one does the same to the others. The currently selected object's siblings are outlined with a dotted line.

| Mode | Copies | How they're placed |
|---|---|---|
| Off | none | — |
| 2-team rotation (180°) | 2 | Opposite the center; bases swap red↔green or blue↔purple |
| 3-team rotation (120°) | 3 | Spaced evenly around the center; bases cycle through the four team colors |
| 4-team rotation (90°) | 4 | Spaced evenly around the center; bases cycle through the four team colors |
| Mirror left-right | 2 | Flipped across the vertical center line |
| Mirror top-bottom | 2 | Flipped across the horizontal center line |

A few things to know:
- **2-team and 4-team rotation always stay inside a square world**, no matter where you place the object. **3-team (120°) rotation can't make that same guarantee** — a 120° turn genuinely mixes the X and Y axes, so a copy can land outside the world if the object sits farther from the center than the world's half-width (roughly, out near a corner). The editor automatically pulls the whole set toward the center to keep everyone inside the world when this would happen, and the status bar tells you when it does. Keep new 3-team objects reasonably close to the center to avoid this.
- **Teleporters** in a symmetric set are automatically linked to each other in a ring.
- **Bases** swap or cycle team colors as described above; other object types are untouched, since "team" has no meaning for them.
- **Duplicate** (Ctrl+D) clones only the one selected object and builds it a fresh, correctly centered family if a symmetry mode is on — it doesn't try to translate a whole existing family, since that would break the symmetry.
- Symmetry linkage is **not saved in the `.bzw` file**. After you save and reopen a map, the copies are ordinary, independent objects — though see Map Text below for a way around this when hand-editing.

### Materials

**Objects → Materials...** opens a list of named materials (color, and optionally a texture reference). Assign a material to a box, pyramid, arc, or cone in the **Material (all faces)** field, or, for boxes and arcs, override individual faces:

- **Box:** Top, Sides, Bottom
- **Arc:** Top, Bottom, Inside, Outside, Start side, End side (matching BZFlag's own face names)
- **Cone:** one material only, no per-face override

The 3D view colors each face by its material; the actual texture image isn't drawn (BZFlag texture images live in your game client or on the web, not in this editor). To avoid every textured object just showing up white, though:

- **Load PNG...** picks a local image file and automatically sets the material's color to that image's average color (unless you've already set a custom color yourself).
- **Fetch Color from URL** does the same for a texture given as a web address — this is the *only* thing in the whole app that makes a network request, and only when you click this button.
- Opening a map that already has a texture file sitting next to it does this automatically too.

**Texture field / image URLs:** paste a full image URL directly into the Texture field, for example:
```
http://images.bzflag.org/astevens/concrete.png
```
- A pasted `https://` link is automatically rewritten to `http://`, since that's the only scheme BZFlag actually loads.
- If the URL isn't under `http://images.bzflag.org/`, a warning appears (it still saves — the warning just tells you it likely won't be approved for league play).
- A URL texture needs its `.png` extension; a local texture name (no `://`) doesn't.

Every other material setting from an imported file — `addtexture`, `texmat`, `ambient`, and so on — is preserved exactly even though this editor doesn't expose controls for it.

### Map Text (advanced)

Tabbed alongside the Selected Object panel — click the tab to switch to it. This shows the raw `.bzw` text of your current map, live.

- **Refresh from Map** pulls in the latest state; it also updates on its own a moment after you make a change, but only while this tab is visible.
- **Apply to Map** parses whatever's in the box and replaces the map with it — the same as opening a file, so undo still works normally afterward. This is how you hand-add an object by typing or pasting a block directly.
- With **"Apply the toolbar's symmetry mode to new objects"** checked (on by default) and a symmetry mode active, any object that's genuinely new in the text you applied gets its symmetric copies built automatically, the same as if you'd placed it with the mouse. Editing an existing object's numbers, rather than adding a new block, is treated as a plain edit and won't trigger this — so refreshing, glancing at the text, and applying it again unchanged never duplicates anything.

### Import Mesh Object

**Objects → Import Mesh Object...** opens a file picker for any `.bzw`. It pulls out just the `mesh ... end` blocks from that file (other object types in it are skipped) and adds them to your current map at the origin, along with any materials those meshes refer to. Handy for keeping a "parts bin" file of your favorite mesh props.

### Recent files

**File → Open Recent** keeps your last 12 opened or saved files, most recent first, and remembers them the next time you start the app. Files that have been moved or deleted are quietly dropped from the list.

## Supported `.bzw` content

Reads and writes `world`, `box`, `pyramid`, `base`, `teleporter`, `arc`, `cone`, `mesh`, `material`, `define`/`group`, and `link` blocks, plus per-face `matref` on boxes and arcs. Any block type this editor doesn't specifically handle (walls, physics drivers, dynamic colors, zones, texture matrices...) is preserved exactly as written and saved back unchanged — it just isn't drawn or editable here.

**Known limits:**
- Meshes can be moved but not reshaped, rotated, or scaled here.
- Group prefabs can't be edited or reshaped after creation.
- Groups can't contain other groups.
- Comments in the file are lost on save.
- The 3D preview shows flat material colors (real or approximated from a texture), not actual texture images.

**Keep a backup of any map you didn't create in this editor** before saving over it, especially an unusually structured one.

## Notes on the 3D view

The 3D view uses a real per-pixel depth buffer (built with NumPy), so overlapping objects always render in the correct front-to-back order regardless of draw order — this fixed an earlier issue where a long, thin object could visually glitch through a nearby one. Building that buffer costs some CPU every time the view redraws, so a very large map or very high-division arcs/cones may feel less smooth while dragging or orbiting than a simple map does.

## Ideas for the future

- Real texture rendering in the 3D view (the depth buffer already computes exact per-pixel positions, so this is a natural next step)
- Editing mesh geometry (not just moving it)
- Reshaping or ungrouping a saved prefab
- Saving symmetry pairs so they survive a save/reload outside of Map Text
