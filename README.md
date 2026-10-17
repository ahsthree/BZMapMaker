<p align="center">
  <img src="assets/bzmapmaker-logo.png" alt="BZ Map Maker logo" width="180">
</p>

# BZ Map Maker

A cross-platform map editor for [BZFlag](https://www.bzflag.org/), inspired by the classic BZEdit. It edits `.bzw` world files, lets you build and edit directly in a live 3D view, and can build symmetrical (balanced) capture-the-flag maps for you automatically.

> Unofficial fan project, not affiliated with the BZFlag project.

**Current release: 1.4.4** · [Download](../../releases) a ready-to-run package for Debian/Ubuntu, Windows or macOS, or run the single `bzmapmaker.py` script on anything with Python.

<img width="1920" height="1051" alt="image" src="https://github.com/user-attachments/assets/1f409e95-d424-43ab-9e68-90c8259c4338" />


<img width="1254" height="1254" alt="BZMM Arena Blueprint" src="https://github.com/user-attachments/assets/d27219f6-82ff-48df-8fba-e7eccabb5e88" />


## What's new in 1.4.4

- **Objects → World Settings...** edits the whole `world` block: name, size, flag height, **No walls** and **Free CTF spawns**. Any other world parameter in a file is kept as written. Previously only `size` survived an open-and-save.
- **Box select in the 2D view:** **Shift+drag** a rectangle to select every object whose center is inside it (**Ctrl+Shift+drag** adds to the current selection), then press **Ctrl+G** to group them.
- **Teleporters can link to themselves**, and each link now has an **exit face** setting ("its back" or "its front"). Exit faces from existing maps are kept instead of being rewritten to the default, and a self-link is drawn as a ring around the teleporter.
- **Teleporter links inside groups are kept.** Put a teleporter in a group and its links, and other teleporters' links to it, are saved using BZFlag's `<group instance>:<teleporter>` names and read back when you reopen the map. Links the editor can't represent exactly (wildcards such as `from *`, or links between two instances of a group) are carried through untouched instead of being dropped.
- **3D fixes:** hollow arcs no longer lose their top face when viewed from above, and the end side of partial arcs and the underside of solid arcs and cones now draw correctly.
- **Group instance names** (the `name` line inside a `group` block) are now preserved when you open and save a map.
- **New logo and app icon** on every platform.
- **Ready-made packages** for Debian/Ubuntu (`.deb`), Windows and macOS, built automatically by GitHub Actions (see [Installation](#installation)).

### Earlier highlights

- **Edit directly in 3D**, with the 2D view available either side by side or as a small radar overlay (**View** menu).
- **A real per-pixel depth buffer** for the 3D view (built with NumPy), replacing the old approach of sorting whole shapes, so overlapping objects, especially long thin ones, always render in the correct order.
- **Map Text panel**, tabbed with the Selected Object panel: view or hand-edit the raw `.bzw` text directly, with **Apply to Map** that can automatically build symmetry for anything new you typed, using whatever mode is active in the toolbar.
- **Textures show a real color** instead of plain white in the 3D view. Loading a local PNG or fetching one from a URL fills in its average color automatically (the actual image still isn't drawn, just its color).
- **Ctrl+click multi-select works in the 3D view too**, and 3D picking uses true ray depth, so an object hidden under another in the flat 2D view can be reached by orbiting to an angle where it's exposed and clicking it directly.
- **3- and 4-team rotational symmetry**, generalized from the original 2-team mode, plus an axis compass in the 2D view and a live-turning gizmo in the 3D view.
- **Arcs, cones, zones, world weapons, groups (reusable prefabs) and mesh objects** are readable and writable. All but meshes are editable, and group members can be edited one by one.
- **Physics drivers and map options** have their own dialogs under the **Objects** menu.
- Older maps' teleporter names and numeric-style links are read correctly and upgraded to the modern format on save.
- Recent Files list, persisted between sessions.

## Features

- **Edit directly in 3D.** Click to select, drag to move, Shift+drag to rotate, drag to place and size new objects, all in the same 3D view you use to look at your map. Orbit with the right or middle mouse button (or Space+drag), and zoom with the scroll wheel.
- **2D layout view**, either side by side with the 3D view or as a small fixed radar overlay in a corner of your choice.
- **Objects:** boxes, pyramids, arcs, cones, team bases (red, green, blue, purple), teleporters, zones, world weapons, and prefab groups.
- **Meshes:** opens `mesh` blocks from other maps (or a Modeltool-converted model) and keeps every normal, texture coordinate, and `drawinfo` byte for byte, even though the editor itself can't reshape one.
- **Symmetry modes** that build a balanced map for you: 2, 3, or 4-team rotation, or a left-right/top-bottom mirror. Move one copy and the others follow.
- **Materials**, including separate materials per face on boxes and arcs, a texture field that accepts a pasted image URL, and automatic color-from-texture so a textured object doesn't just render white.
- **Teleporter links** with a choice of exit face per link, self-links, and links that follow a teleporter into a group.
- **Physics drivers, map options and world settings** edited from the **Objects** menu.
- **Map Text panel** for viewing or hand-editing the raw `.bzw` text, with optional automatic symmetry for anything new you type.
- **Real files:** open, edit, and save `.bzw` worlds, including ones from other tools or from years-old maps. Undo, duplicate, snap-to-grid, recent-files list.
- **Import Mesh Object** to pull a mesh out of another `.bzw` file and drop it into the map you're working on.

## Installation

### Ready-made packages

Download the file for your system from the [Releases](../../releases) page.

| System | File | How to install |
| --- | --- | --- |
| Debian, Ubuntu and derivatives (including Chromebook Linux) | `bzmapmaker_1.4.4_all.deb` | `sudo apt install ./bzmapmaker_1.4.4_all.deb`, then start **BZ Map Maker** from your menu or run `bzmapmaker` (optionally `bzmapmaker mymap.bzw`). Installing this way pulls in Python, PyQt6 and NumPy for you. |
| Windows | `BZMapMaker-1.4.4-windows.zip` | Unzip and run `BZMapMaker.exe`. It isn't code-signed, so Windows SmartScreen may warn: choose **More info → Run anyway**. |
| macOS | `BZMapMaker-1.4.4-macos.dmg` | Open the disk image and drag **BZMapMaker** to Applications. It isn't signed or notarized, so the first time, right-click the app and choose **Open**. The build is for the type of Mac that built it (Apple Silicon); on an Intel Mac, run from source below. |

To upgrade the `.deb`, install the newer file the same way.

### Run from source

You need **Python 3.9 or newer**, **PyQt6**, and **NumPy** (NumPy powers the 3D view's depth buffer. It's a very common, lightweight math library, not a graphics driver; nothing here needs a GPU or OpenGL). Then download `bzmapmaker.py` (or clone this repository) and run it. Keep `bzmapmaker.png` next to the script if you want the logo as the window icon.

```
python bzmapmaker.py                # start with a blank map
python bzmapmaker.py mymap.bzw      # open an existing map
python bzmapmaker.py --version      # print the version
```

#### Chromebook (Linux / Crostini)

1. Turn on Linux: **Settings → Advanced → Developers → Linux development environment → Turn on**.
2. Open the **Terminal** app and either install the `.deb` above, or run from source:

```
sudo apt update
sudo apt install python3-pyqt6 python3-numpy
```

3. Go to the folder with the script and run `python3 bzmapmaker.py`.

Use the `apt` packages rather than `pip` here. On Chromebooks (which may be ARM), pip can leave a half-installed PyQt6 that fails with `No module named 'PyQt6.QtCore'`. If you already tried pip, remove it first:

```
pip uninstall PyQt6 PyQt6-Qt6 PyQt6-sip numpy
```

#### Windows

1. Install Python from [python.org](https://www.python.org/downloads/). On the first installer screen, tick **Add python.exe to PATH**.
2. Open **Command Prompt** or **PowerShell** and run:

```
py -m pip install PyQt6 numpy
py bzmapmaker.py
```

#### macOS

1. Install Python 3 from [python.org](https://www.python.org/downloads/) or with Homebrew (`brew install python`).
2. In Terminal:

```
python3 -m pip install PyQt6 numpy
python3 bzmapmaker.py
```

#### Linux (Debian, Ubuntu and derivatives)

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
| --- | --- |
| `No module named 'PyQt6.QtCore'` | Broken pip install. Uninstall it (see Chromebook above) and use your system package. |
| `No module named 'numpy'` | Install it the same way you installed PyQt6 (`pip install numpy` or `apt install python3-numpy`). |
| `externally-managed-environment` | Use the `apt`/`dnf`/`pacman` package, or a virtual environment. |
| `Could not load the Qt platform plugin "xcb"` | `sudo apt install libxcb-cursor0` |
| `python` not found on Windows | Use `py` instead, or reinstall Python with "Add to PATH" ticked. |
| Menu icon didn't change after upgrading the `.deb` | Log out and back in, or run `sudo gtk-update-icon-cache /usr/share/icons/hicolor`. |

### Building the packages yourself

The release packages are built from this repository by `.github/workflows/release.yml`. Push a tag such as `v1.4.4`, or run the workflow from the **Actions** tab, and download the three packages from the run's artifacts. Each package has to be built on its own operating system, so the workflow uses a Linux, a Windows and a macOS runner. You can also build one locally:

| Package | Command (run on that OS) | Output |
| --- | --- | --- |
| Debian/Ubuntu | `./build_deb.sh` | `bzmapmaker_1.4.4_all.deb` |
| Windows | `build_windows.bat` | `dist\BZMapMaker.exe` and a `.zip` |
| macOS | `./build_macos.sh` | `dist/BZMapMaker.app` and a `.dmg` |

The Windows and macOS builds use [PyInstaller](https://pyinstaller.org/); the icons and Debian packaging files are in `packaging/`.

## Using the editor

### Building a map

The 3D view is where you build. Pick a tool in the toolbar: **Select, Box, Pyramid, Base, Teleporter, Arc, Cone, Zone,** or **Weapon**.

| Action | In the 3D view | In the 2D view |
| --- | --- | --- |
| Select an object | Click it | Click it |
| Add to the selection (for grouping) | Ctrl+click | Ctrl+click |
| Select everything in an area | — | Shift+drag a box (Ctrl+Shift+drag adds to the selection) |
| Move the selected object | Drag it | Drag it |
| Rotate the selected object | Shift+drag | Edit "Rotation" in the panel |
| Place a Box/Pyramid/Arc/Cone/Zone | Drag corner to corner, or click for a default size | Drag corner to corner, or click for a default size |
| Place a Base/Teleporter/Weapon | Click | Click |
| Orbit the camera | Right or middle mouse button, or Space+drag | — |
| Pan the view | — | Drag empty space, or right/middle mouse button, or Space+drag |
| Zoom | Scroll wheel | Scroll wheel |

Once an object is selected, use the **Selected object** panel on the right for exact position, size, height, rotation, team, material, and (for teleporters) links. Resizing an *existing* object is done there, not by dragging a corner in 3D.

**3D picking uses real depth**, not just what's drawn on top. If something is hidden underneath another object in the flat 2D view, orbit the 3D camera to an angle where its side or top is exposed and click it directly.

Keyboard shortcuts: **Ctrl+N** new, **Ctrl+O** open, **Ctrl+S** save, **Ctrl+Shift+S** save as, **Ctrl+Z** undo, **Ctrl+D** duplicate, **Delete** delete, **Ctrl+0** fit the 2D view, **Ctrl+G** group.

When you're done: **File → Save**, then run the map with BZFlag's server: `bzfs -world mymap.bzw`.

### View menu: side by side, or 3D with a 2D radar

- **Side by Side** (the default) shows the 2D and 3D views next to each other.
- **3D View with 2D Radar** fills the window with the 3D view and shows a small, fixed, non-interactive 2D minimap in one corner, for orientation while you edit entirely in 3D.
- **Radar Position** sets which corner (top-left, top-right, bottom-left, bottom-right).
- **Show Zones** turns the zone overlay on or off.

Your choices are remembered the next time you open the app.

### The axis compass and gizmo

Both views show which way is which. The 2D view has a small fixed compass (+X/−X/+Y/−Y) in the top-left corner, since that view never rotates. The 3D view has a small circular gizmo, also top-left, that turns live as you orbit. A solid colored dot with its letter marks the positive end of each axis (X, Y, Z), and a hollow dot marks the negative end.

### World size and world settings

The **World size** box in the toolbar follows the BZFlag convention: it's the distance from the center to each edge. `size 400` gives a world that runs from −400 to 400 on both axes (800 wide). The center is always 0, 0.

**Objects → World Settings...** opens the full `world` block:

- **Name** of the map.
- **Size (half-width)**, the same value as the toolbar box.
- **Flag height**, how much clearance a flag needs to spawn. Leave "Set flag height" unticked to use BZFlag's default (nothing is written).
- **No walls** removes the outer walls.
- **Free CTF spawns** makes players spawn at random, instead of at their base, after a team flag is captured.

Any other parameter found in a map's `world` block is kept exactly as written.

### Object sizes

`Half-width X` and `Half-width Y` are half the object's extent, as in `.bzw` files. A box with `size 10 10 9.4` is 20 by 20 on the ground. `Height` is the full height. Rotation is in degrees.

### Teleporters

Each teleporter has a unique name and two link settings, **Front links to** and **Back links to**, written as `link` blocks. Next to each is a second drop-down for **which face of the target you come out of** ("its back" or "its front"). The defaults (a front link comes out of the target's back, a back link out of its front) match what older versions always wrote.

- A teleporter can link to **itself**: pick its own name. Linking its front to its own back gives a pass-through, and in the 2D view a self-link is drawn as a ring around the teleporter.
- Opening an older map that names a teleporter on its own line (`teleporter home`) or links by plain number (`from 0` / `to 1`) instead of by name still works. Those names and links are read correctly, and saving rewrites them in the modern, named style automatically.
- Each face holds **one** link here. If a file gives a face several links (BZFlag then picks one at random), only the last is shown, but see below for links the editor keeps untouched.
- **Teleporters inside groups** keep their links. BZFlag names them `<group instance>:<teleporter>`, so a group that holds teleporters is written with a `name` line, and links to and from them use those names. When the same group is used more than once, a link to one of its teleporters is written as `*:<teleporter>`, which BZFlag treats as a random pick among the copies.
- Links the editor can't represent exactly, such as `from * to *` wildcards or links from one group instance to another, are kept word for word and written back at the end of the file.

### Arcs and cones

Arcs have a **Sweep angle** (360° is a full circle) and a **Hollow ratio** (0 is solid, closer to 1 is a thin ring) plus **Divisions** for how many segments make up the curve. Cones just have **Divisions**. Both can have a single all-faces material, or (arcs only) separate materials per face. See Materials below.

### Zones and world weapons

**Zone** (drag to place) defines an area for spawning and flags. Set which teams spawn there, which teams' flags fly there when dropped, and any flag types in the panel. Zones are invisible in the real game, so the editor shows them as a checkered aid colored by which teams spawn there; **View → Show Zones** toggles that overlay. Symmetry swaps or cycles a zone's teams the same way it does for bases.

**Weapon** (click to place) is a world weapon. Rotation sets its aim direction and Tilt its vertical aim; set what it fires and how often in the panel. It has no appearance in the real game, so it's drawn here as an arrow.

### Groups

Select two or more objects (Ctrl+click each one, or Shift+drag a box in the 2D view), then **Edit → Group Selected** (Ctrl+G). This saves their layout as a reusable prefab (a `define` block, positioned relative to their combined center) and replaces them with a single **group** instance you can move and rotate as a whole.

- To add more objects to an existing group, select the group together with the objects and press Ctrl+G again.
- **Edit member** in the panel picks one object inside the selected group so you can change its size, position, links and so on.
- A group can optionally override the team color of every base inside it. Set this in the panel's **Group team override** field.
- Teleporters inside a group keep their links (see Teleporters above).
- There is no ungroup command, and groups can't contain other groups.

### Symmetry modes

Choose a mode from the toolbar drop-down **before** placing objects (or before dragging an object that doesn't have one yet; dragging it while a mode is active gives it a fresh, correctly centered copy). Every new object gets one or more **siblings** that stay linked: moving, resizing, rotating, or deleting one does the same to the others. The currently selected object's siblings are outlined with a dotted line.

| Mode | Copies | How they're placed |
| --- | --- | --- |
| Off | none | — |
| 2-team rotation (180°) | 2 | Opposite the center; bases swap red↔green or blue↔purple |
| 3-team rotation (120°) | 3 | Spaced evenly around the center; bases cycle through the four team colors |
| 4-team rotation (90°) | 4 | Spaced evenly around the center; bases cycle through the four team colors |
| Mirror left-right | 2 | Flipped across the vertical center line |
| Mirror top-bottom | 2 | Flipped across the horizontal center line |

A few things to know:

- **2-team and 4-team rotation always stay inside a square world**, no matter where you place the object. **3-team (120°) rotation can't make that same guarantee**: a 120° turn genuinely mixes the X and Y axes, so a copy can land outside the world if the object sits farther from the center than the world's half-width (roughly, out near a corner). The editor automatically pulls the whole set toward the center to keep everyone inside the world when this would happen, and the status bar tells you when it does. Keep new 3-team objects reasonably close to the center to avoid this.
- **Teleporters** in a symmetric set are automatically linked to each other in a ring.
- **Bases** swap or cycle team colors as described above; zones do the same with their team lists. Other object types are untouched, since "team" has no meaning for them.
- **Duplicate** (Ctrl+D) clones only the one selected object and builds it a fresh, correctly centered family if a symmetry mode is on. It doesn't try to translate a whole existing family, since that would break the symmetry.
- Symmetry linkage is **not saved in the `.bzw` file**. After you save and reopen a map, the copies are ordinary, independent objects (see Map Text below for a way around this when hand-editing).

### Materials

**Objects → Materials...** opens a list of named materials (color, and optionally a texture reference). Assign a material to a box, pyramid, arc, or cone in the **Material (all faces)** field, or, for boxes and arcs, override individual faces:

- **Box:** Top, Sides, Bottom
- **Arc:** Top, Bottom, Inside, Outside, Start side, End side (matching BZFlag's own face names)
- **Cone:** one material only, no per-face override

The 3D view colors each face by its material; the actual texture image isn't drawn (BZFlag texture images live in your game client or on the web, not in this editor). To avoid every textured object just showing up white, though:

- **Load PNG...** picks a local image file and automatically sets the material's color to that image's average color (unless you've already set a custom color yourself).
- **Fetch Color from URL** does the same for a texture given as a web address. This is the *only* thing in the whole app that makes a network request, and only when you click this button.
- Opening a map that already has a texture file sitting next to it does this automatically too.

**Texture field / image URLs:** paste a full image URL directly into the Texture field, for example:

```
http://images.bzflag.org/astevens/concrete.png
```

- A pasted `https://` link is automatically rewritten to `http://`, since that's the only scheme BZFlag actually loads.
- If the URL isn't under `http://images.bzflag.org/`, a warning appears. It still saves; the warning just tells you BZFlag clients may not download it by default.
- A URL texture needs its `.png` extension; a local texture name (no `://`) doesn't.

Every other material setting from an imported file (`addtexture`, `texmat`, `ambient`, and so on) is preserved exactly even though this editor doesn't expose controls for it.

### Physics drivers

**Objects → Physics Drivers...** manages the map's named physics drivers (the things that make a surface move, spin, slide or kill). Create or edit a driver there, then assign it to a box, pyramid, arc or cone with the **Physics driver** field in the Selected Object panel. Symmetry copies get their own driver, transformed to match.

### Map options

**Objects → Map Options...** edits the map's `options` block: the same flags you would otherwise pass to `bzfs` on the command line, including `-set` for any server variable. It's a plain text box, and what you type is written out exactly as typed.

### Map Text (advanced)

Tabbed alongside the Selected Object panel (click the tab to switch to it). This shows the raw `.bzw` text of your current map, live.

- **Refresh from Map** pulls in the latest state; it also updates on its own a moment after you make a change, but only while this tab is visible.
- **Apply to Map** parses whatever's in the box and replaces the map with it, the same as opening a file, so undo still works normally afterward. This is how you hand-add an object by typing or pasting a block directly.
- With **"Apply the toolbar's symmetry mode to new objects"** checked (on by default) and a symmetry mode active, any object that's genuinely new in the text you applied gets its symmetric copies built automatically, the same as if you'd placed it with the mouse. Editing an existing object's numbers, rather than adding a new block, is treated as a plain edit and won't trigger this, so refreshing, glancing at the text, and applying it again unchanged never duplicates anything.

### Import Mesh Object

**Objects → Import Mesh Object...** opens a file picker for any `.bzw`. It pulls out just the `mesh ... end` blocks from that file (other object types in it are skipped) and adds them to your current map at the origin, along with any materials those meshes refer to. Handy for keeping a "parts bin" file of your favorite mesh props.

### Recent files

**File → Open Recent** keeps your last 12 opened or saved files, most recent first, and remembers them the next time you start the app. Files that have been moved or deleted are quietly dropped from the list.

## Supported `.bzw` content

Reads and writes `world`, `options`, `physics`, `material`, `box`, `pyramid`, `base`, `teleporter`, `arc`, `cone`, `mesh`, `zone`, `weapon`, `define`/`group`, and `link` blocks, plus per-face `matref` on boxes and arcs. Any block type this editor doesn't specifically handle (dynamic colors, texture matrices, water level, transforms...) is preserved exactly as written and saved back unchanged. It just isn't drawn or editable here.

**Known limits:**

- Meshes can be moved but not reshaped, rotated, or scaled here.
- There is no ungroup command, and groups can't contain other groups.
- Each teleporter face holds one link in the editor (extra links in a file are kept as written, but not shown).
- Comments in the file are lost on save.
- The 3D preview shows flat material colors (real or approximated from a texture), not actual texture images.

**Keep a backup of any map you didn't create in this editor** before saving over it, especially an unusually structured one.

## Notes on the 3D view

The 3D view uses a real per-pixel depth buffer (built with NumPy), so overlapping objects always render in the correct front-to-back order regardless of draw order. This fixed an earlier issue where a long, thin object could visually glitch through a nearby one. Building that buffer costs some CPU every time the view redraws, so a very large map or very high-division arcs/cones may feel less smooth while dragging or orbiting than a simple map does.

## Ideas for the future

- Real texture rendering in the 3D view (the depth buffer already computes exact per-pixel positions, so this is a natural next step)
- Editing mesh geometry (not just moving it)
- An ungroup command
- Several links per teleporter face
- Saving symmetry pairs so they survive a save/reload outside of Map Text

## License

LGPL-2.1. See [LICENSE](LICENSE).
