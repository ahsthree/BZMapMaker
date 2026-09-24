# BZMapMaker
BZFlag .bzw map editor for Windows, macOS, Linux and Chromebook. 2D layout, live 3D preview, teleporter links and symmetry tools for balanced CTF maps
#####

# BZ Map Maker

A cross-platform map editor for [BZFlag](https://www.bzflag.org/), inspired by the classic BZEdit. It edits `.bzw` world files with a top-down layout view and a live 3D preview, and it can build symmetrical (balanced) capture-the-flag maps for you.

> Unofficial fan project, not affiliated with the BZFlag project.

<!-- Add a screenshot here: ![BZ Map Maker](docs/screenshot.png) -->

## Features

- **Two views side by side.** Left: a top-down layout editor. Right: a 3D preview you can orbit and zoom, updated as you edit.
- **Objects:** boxes, pyramids, team bases (red, green, blue, purple) and teleporters.
- **Teleporter links:** front and back faces can each link to another teleporter. Names are kept unique automatically.
- **Symmetry modes:** place an object once and its mirrored twin is created and kept in sync (see [Symmetry modes](#symmetry-modes)).
- **Exact editing:** a properties panel for position, size, height and rotation.
- **Real files:** open, edit and save `.bzw` worlds. Undo, duplicate, snap to grid.
- **Light on dependencies:** only Python and PyQt6. The 3D preview is drawn in software, so no OpenGL is required.

## Installation

You need **Python 3.9 or newer** and **PyQt6**. Then download `bzmapmaker.py` (or clone this repository) and run it.

```
python bzmapmaker.py                # start with a blank map
python bzmapmaker.py mymap.bzw      # open an existing map
```

### Chromebook (Linux / Crostini)

1. Turn on Linux: **Settings → Advanced → Developers → Linux development environment → Turn on**.
2. Open the **Terminal** app and run:
   ```
   sudo apt update
   sudo apt install python3-pyqt6
   ```
3. Go to the folder with the script and run `python3 bzmapmaker.py`.

Use the `apt` package rather than `pip` here. On Chromebooks (which may be ARM), pip can leave a half-installed PyQt6 that fails with `No module named 'PyQt6.QtCore'`. If you already tried pip, remove it first:
```
pip uninstall PyQt6 PyQt6-Qt6 PyQt6-sip
```

### Windows

1. Install Python from [python.org](https://www.python.org/downloads/). On the first installer screen, tick **Add python.exe to PATH**.
2. Open **Command Prompt** or **PowerShell** and run:
   ```
   py -m pip install PyQt6
   py bzmapmaker.py
   ```

### macOS

1. Install Python 3 from [python.org](https://www.python.org/downloads/) or with Homebrew (`brew install python`).
2. In Terminal:
   ```
   python3 -m pip install PyQt6
   python3 bzmapmaker.py
   ```

### Linux (Debian, Ubuntu and derivatives)

```
sudo apt install python3-pyqt6
python3 bzmapmaker.py
```

Other distributions: `sudo dnf install python3-pyqt6` (Fedora) or `sudo pacman -S python-pyqt6` (Arch).

If you prefer pip, use a virtual environment (newer distributions refuse system-wide pip installs):
```
python3 -m venv venv && source venv/bin/activate
pip install PyQt6
python bzmapmaker.py
```
If Qt complains about a missing `xcb` plugin, run `sudo apt install libxcb-cursor0 libgl1`.

### Troubleshooting

| Message | Fix |
|---|---|
| `No module named 'PyQt6.QtCore'` | Broken pip install. Uninstall it (see Chromebook above) and use your system package. |
| `externally-managed-environment` | Use the `apt` package or a virtual environment. |
| `Could not load the Qt platform plugin "xcb"` | `sudo apt install libxcb-cursor0` |
| `python` not found on Windows | Use `py` instead, or reinstall Python with "Add to PATH" ticked. |

## Using the editor

### Building a map

1. Pick a tool in the toolbar: **Select, Box, Pyramid, Base** or **Teleporter**.
2. **Box / Pyramid:** drag from one corner to the opposite corner, or just click for a default size.
3. **Base / Teleporter:** click to place one, then adjust it in the properties panel.
4. Switch to **Select**, click an object, and drag it to move it or edit exact values in the **Selected object** panel.
5. **File → Save** and run the map with BZFlag's server: `bzfs -world mymap.bzw`.

### Controls

| Action | How |
|---|---|
| Pan the layout view | Drag empty space with Select, or right/middle mouse drag, or hold Space and drag |
| Zoom | Mouse wheel |
| Orbit the 3D preview | Drag |
| Zoom the 3D preview | Mouse wheel |
| New / Open / Save / Save As | Ctrl+N / Ctrl+O / Ctrl+S / Ctrl+Shift+S |
| Undo | Ctrl+Z |
| Duplicate | Ctrl+D |
| Delete | Delete |
| Fit the map to the window | Ctrl+0 |

### World size

The **World size** box follows the BZFlag convention: it is the distance from the center to each edge. `size 400` gives a world that runs from -400 to 400 on both axes (800 wide). The center is always 0, 0.

### Object sizes

`Half-width X` and `Half-width Y` are half the object's extent, as in `.bzw` files. A box with `size 10 10 9.4` is 20 by 20 on the ground. `Height` is the full height. Rotation is in degrees, counter-clockwise.

### Teleporters

Each teleporter has a unique name (`t1`, `t2`, ...) and two link settings:

- **Front links to:** where you come out after entering this teleporter's front.
- **Back links to:** where you come out after entering its back.

They are written as `link` blocks (`from t1:f to t2:b` and so on). Choose "Nothing" to leave a face unlinked.

## Symmetry modes

Choose a mode from the toolbar drop-down **before** placing objects. With a mode on, every new object gets a **twin** at the mirrored position. The twin stays linked to the original: moving, resizing, rotating, changing the height or deleting one does the same to the other. Select an object and its twin is outlined with a dotted line.

Mirroring is around the center of the world (0, 0). For an object at position `(200, 200)`:

| Mode | Twin position | Twin rotation | Twin at (200, 200) |
|---|---|---|---|
| **Off** | none | none | none |
| **Rotate 180°** | `(-x, -y)` | rotation + 180° | (-200, -200) |
| **Mirror left-right** | `(-x, y)` | 180° − rotation | (-200, 200) |
| **Mirror top-bottom** | `(x, -y)` | −rotation | (200, -200) |

- **Rotate 180°** (point symmetry) suits maps where both teams play the same layout from opposite corners. Turn the whole map around the center and it looks identical.
- **Mirror left-right** flips the map across the vertical center line, so the east half mirrors the west half.
- **Mirror top-bottom** flips the map across the horizontal center line, so the north half mirrors the south half.

Extra rules that keep the map fair:

- **Bases swap teams.** A base's twin belongs to the opposing team: red ↔ green and blue ↔ purple. Changing a base's team also changes its twin's.
- **Teleporters** get a twin turned 180° from the original, so front and back line up, and the pair is linked to each other on both faces.
- **Objects on the axis** have no twin. In Rotate 180° that means an object exactly at 0, 0. In left-right mode it means x = 0, and in top-bottom mode y = 0. Their twin would sit on top of themselves.
- **Duplicate** (Ctrl+D) also creates a twin when a mode is on.
- **Each pair remembers its mode.** Changing the drop-down affects new objects only.
- Turn the mode to **Off** to edit one object on its own. Twins created earlier still follow it, so delete the pair or undo if you want them separate.

Twin pairing is not stored in the `.bzw` file. After you save and reopen a map, both halves are ordinary objects and no longer move together.

## Supported `.bzw` content

Reads and writes `world` (size), `box`, `pyramid`, `base` (with `color`) and `teleporter` (with `name`) blocks, plus `link` blocks. Positions, sizes and rotations are preserved.

**Not supported yet.** These are skipped when opening a file, so they will be missing from anything you save:
- Walls, meshes, materials, textures, physics zones and other block types
- Groups: boxes inside a `group` load without the group's transform
- Link targets always use the opposite face (`t1:f` → `t2:b`), so front-to-front links become front-to-back

**Keep a backup of any map you did not create in this editor** before saving over it.

## Notes

- The 3D preview is a simple software renderer without textures or lighting like the game. Overlapping objects can occasionally be drawn in the wrong order.
- Tested on Linux on a Chromebook. The Windows and macOS steps use the standard PyQt6 install and are less tested.

## Ideas for the future

- Wall blocks and other `.bzw` features
- Saving symmetry pairs
- Textures and materials in the preview
- A first-person walk-through view
