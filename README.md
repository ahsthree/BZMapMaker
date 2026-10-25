# BZ Map Maker

A cross-platform map editor for [BZFlag](https://www.bzflag.org/), inspired by the classic BZEdit. It edits `.bzw` world files, lets you build and edit directly in a live 3D view, and can build symmetrical (balanced) capture-the-flag maps for you automatically.

> Unofficial fan project, not affiliated with the BZFlag project.

**Current release: 1.5.0** · [Download](https://github.com/ahsthree/BZMapMaker/releases/tag/v1.5.0) a ready-to-run package for Debian/Ubuntu, Windows or macOS, or run the single `bzmapmaker.py` script on anything with Python.

Custom objects are extracted from the bzmapmaker-objects-1.4.9 archive 
(including subdirectories like 'nature' and 'other') into platform-specific 
shared directories: /usr/share/bzmapmaker/objects/ on Linux, 
C:\ProgramData\BZMapMaker\objects\ on Windows, and 
/Library/Application Support/BZMapMaker/objects/ on macOS.


## Features & Screenshots

![BZMapMaker v1.5.0 Features](https://raw.githubusercontent.com/ahsthree/BZMapMaker/main/assets/images/bzmapmaker-features1.5.0.jpeg)

![App Screenshot](https://raw.githubusercontent.com/ahsthree/BZMapMaker/main/assets/images/Screenshot%20From%202026-10-24%2021-08-48.png)

## What's new in 1.5.0

## What's New in v1.5.0

Version 1.5.0 introduces major workflow enhancements, robust cross-platform packaging, and advanced map-editing tools for the BZFlag community:

* **Cross-Platform Native Builds**: Fully automated CI/CD pipeline delivering ready-to-run binaries for Windows (`.exe`), macOS (`.app`), and Debian Linux (`.deb`).
* **Dual 2D & 3D Viewports**: Seamlessly switch between precision 2D top-down editing and interactive 3D wireframe previews.
* **Terrain Generator & Custom Objects Library**: Integrated procedural terrain generation tools paired with a robust custom objects library to supercharge map creation.
* **Procedural Symmetry & Tools**: Powerful symmetry utilities and automated obstacle generation tools to speed up complex layout design.
* **Advanced Mesh & Object Management**: Enhanced mesh grouping, custom asset integration, and precise control over ricochet properties.
* **Physics Trajectory Visualization**: Built-in visual aids to preview shot trajectories and map physics directly within the editor.


## What's new in 1.4.9

- **Internet textures load by themselves.** The `http://` textures a map uses (such as `http://images.bzflag.org/...`) are now downloaded in the background when you open the map, so the 3D view shows them without any button. The picture never waits for the network: faces draw without the texture until it arrives, then fill in. Downloads are remembered on your computer, so the next time (even offline) they appear at once. Turn it off with **View → Load Remote Textures Automatically**. **Fetch All URL Textures** is still there to retry any that failed. See [Materials](#materials).
- **Texture size.** Select a box, pyramid, arc or cone and use the new **Texture size** field to set BZFlag's `texsize` (4 numbers for a box, pyramid or arc; 2 for a cone). A negative number is the size of one texture tile in world units, a positive one is relative to the object's size, so `1 1 1 1` on a box or pyramid gives one texture per face. Leave it blank and nothing is written, so BZFlag uses its default. Existing `texsize` lines in a map are read and kept. The 3D preview does not apply it.
- **Arcs are drawn the way BZFlag draws them.** In bzfs an arc's `rotation` does not turn its shape: the ellipse (X size by Y size) always lines up with the world axes, and `rotation` only sets the angle where the first corner, or the start of a partial sweep, sits. The 2D view, 3D view, selection and clicking now do the same, so an elliptical arc looks in the editor the way it does in the game. Round arcs look exactly as before. Groups still turn the whole arc. To point an elliptical arc diagonally, swap its sizes or use a `spin` transform in a group. Symmetry copies of an elliptical arc keep the same axes instead of turning with the copy, as in the game.
- **`addtexture` is read like `texture`.** Older maps often give a material's picture with `addtexture`; it now fills the Texture field, shows in the 3D view and can be fetched, exactly like `texture`. Maps you don't edit are saved exactly as they were, and any extra `addtexture` layers are kept.

### Also new in 1.4.8

- **Custom object library.** **Objects → Custom Objects** lists ready-made mesh objects (a crate, a lamp post, planters, a street corner...) by category; pick one and click in the 2D or 3D view to place it as a group. You can add your own with **Import into My Objects...** (from any `.bzw`) or **Save Selection to My Objects...** (from your current map), and the materials come along. See [Custom objects](#custom-objects).
- **Textures in the 3D view, and a faster, smarter one.** Boxes, pyramids, arcs and cones show BZFlag's own default textures when they have no material, as in the game, and faces with a texture show a tiny version of it (shrunk so its longer side is at most 16 pixels, which keeps memory trivial however big the file is) repeated across the surface, plus an optional **edge shading** that darkens the far side of sharp depth steps for a contact-shadow look. Both are switches in the **View** menu. The renderer itself is about 1.6 times faster on busy maps with exactly the same picture, and dropping to half resolution while you orbit now only happens when it actually saves time. See [Notes on the 3D view](#notes-on-the-3d-view).
- **Remove Unused.** Placing a custom object adds its group definition (and materials) to the map, and deleting the placed copy leaves them behind. **Objects → Remove Unused...** lists the group definitions nothing places, plus the materials, texture matrices and physics drivers only they used, and deletes the ones you leave ticked. Saving can offer to do the same. See [Removing unused groups](#removing-unused-groups-and-materials).
- **Texture matrices.** **Objects → Texture Matrices...** creates and edits `textureMatrix` blocks (shift, spin and scale, fixed or changing over time), and the Materials dialog has a **Texture matrix** field to give a material one. Existing texture matrices in a map are now read, editable and saved properly instead of just carried through.
=======
- **Internet textures load by themselves.** The `http://` textures a map uses (such as `http://images.bzflag.org/...`) are now downloaded in the background when you open the map, so the 3D view shows them without any button. The picture never waits for the network: faces draw without the texture until it arrives, then fill in. Downloads are remembered on your computer, so the next time (even offline) they appear at once. Turn it off with **View → Load Remote Textures Automatically**. **Fetch All URL Textures** is still there to retry any that failed. See [Materials](#materials).
- **Bases have their textures back.** In the 3D view a base shows its team's `basetop` picture across its top and bottom (once, turning with the base) and its team's `basewall` picture on the sides, in red, green, blue or purple. BZFlag can't give a base a material, so a base always shows these textures.
- **Generate Terrain.** **Objects → Generate Terrain...** builds random hills and valleys over the whole world as one mesh, modelled on a Wings 3D terrain: a flat plain with the hills rising in tiers, and invisible sides and bottom. You choose how much of the world stays flat, the symmetry (off, 2-team 180°, 4-team 90°, mirror left-right or top-bottom), the share of valleys, the feature size, the tallest hill, whether hills are stepped with flat landings, the steepest slope, the height of the flat ground and the random seed. The same seed gives the same terrain, and a top view shows it before you add it. Ground around the objects already on the map stays flat. See [Generate Terrain](#generate-terrain).
- **More custom objects included.** The library now ships 15 nature objects (trees, rocks, weeds, ferns) and 4 others (a dome, a penny and two ramps).
- **Texture size.** Select a box, pyramid, arc or cone and use the new **Texture size** field to set BZFlag's `texsize` (4 numbers for a box, pyramid or arc; 2 for a cone). A negative number is the size of one texture tile in world units, a positive one is relative to the object's size, so `1 1 1 1` on a box or pyramid gives one texture per face. Leave it blank and nothing is written, so BZFlag uses its default. Existing `texsize` lines in a map are read and kept. The 3D preview does not apply it.
- **Arcs are drawn the way BZFlag draws them.** In bzfs an arc's `rotation` does not turn its shape: the ellipse (X size by Y size) always lines up with the world axes, and `rotation` only sets the angle where the first corner, or the start of a partial sweep, sits. The 2D view, 3D view, selection and clicking now do the same, so an elliptical arc looks in the editor the way it does in the game. Round arcs look exactly as before. Groups still turn the whole arc. To point an elliptical arc diagonally, swap its sizes or use a `spin` transform in a group. Symmetry copies of an elliptical arc keep the same axes instead of turning with the copy, as in the game.
- **`addtexture` is read like `texture`.** Older maps often give a material's picture with `addtexture`; it now fills the Texture field, shows in the 3D view and can be fetched, exactly like `texture`. Maps you don't edit are saved exactly as they were, and any extra `addtexture` layers are kept.

### Also new in 1.4.8

- **Custom object library.** **Objects → Custom Objects** lists ready-made mesh objects (a crate, a lamp post, planters, a street corner...) by category; pick one and click in the 2D or 3D view to place it as a group. You can add your own with **Import into My Objects...** (from any `.bzw`) or **Save Selection to My Objects...** (from your current map), and the materials come along. See [Custom objects](#custom-objects).
- **Textures in the 3D view, and a faster, smarter one.** Boxes, pyramids, arcs and cones show BZFlag's own default textures when they have no material, as in the game, and faces with a texture show a tiny version of it (shrunk so its longer side is at most 16 pixels, which keeps memory trivial however big the file is) repeated across the surface, plus an optional **edge shading** that darkens the far side of sharp depth steps for a contact-shadow look. Both are switches in the **View** menu. The renderer itself is about 1.6 times faster on busy maps with exactly the same picture, and dropping to half resolution while you orbit now only happens when it actually saves time. See [Notes on the 3D view](#notes-on-the-3d-view).
- **Remove Unused.** Placing a custom object adds its group definition (and materials) to the map, and deleting the placed copy leaves them behind. **Objects → Remove Unused...** lists the group definitions nothing places, plus the materials, texturematrix and physics drivers only they used, and deletes the ones you leave ticked. Saving can offer to do the same. See [Removing unused groups](#removing-unused-groups-and-materials).
- **Texturematrix.** **Objects → Texturematrix...** creates and edits `textureMatrix` blocks (shift, spin and scale, fixed or changing over time), and the Materials dialog has a **Texturematrix** field to give a material one. Existing texturematrix in a map are now read, editable and saved properly instead of just carried through.
>>>>>>> 9e85454 (Add GitHub Actions build workflow and update project files)
- **Groups can nest and scale.** A group can hold other groups, as in BZFlag itself (a `define` still can't hold another `define`), and a group now has **Scale X/Y/Z** in the panel. Transforms are written in the order BZFlag needs: scale, then rotation, then shift. Teleporters inside nested groups keep their links.
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
- **Textures show in the 3D view** as small approximations of the picture, from local PNG files next to the map or from internet textures. BZFlag's own textures that have no file beside the map (`std_ground`, `wall`, `roof`, `boxwall`, `pyrwall` and the base textures) show a built-in stand-in, so generated terrain and most maps look textured without any files.
- **Ctrl+click multi-select works in the 3D view too**, and 3D picking uses true ray depth, so an object hidden under another in the flat 2D view can be reached by orbiting to an angle where it's exposed and clicking it directly.
- **3- and 4-team rotational symmetry**, generalized from the original 2-team mode, plus an axis compass in the 2D view and a live-turning gizmo in the 3D view.
- **Arcs, cones, zones, world weapons, groups (reusable prefabs) and mesh objects** are readable and writable. All but meshes are editable, and group members can be edited one by one.
- **Physics drivers and map options** have their own dialogs under the **Objects** menu.
- Older maps' teleporter names and numeric-style links are read correctly and upgraded to the modern format on save.
- Recent Files list, persisted between sessions.

## Features

- **Edit directly in 3D.** Click to select, drag to move, Shift+drag to rotate, drag to place and size new objects, all in the same 3D view you use to look at your map. Orbit with the right or middle mouse button (or Space+drag), and zoom with the scroll wheel.
- **2D layout view**, either side by side with the 3D view or as a small fixed radar overlay in a corner of your choice.
- **Objects:** boxes, pyramids, arcs, cones, team bases (red, green, blue, purple), teleporters, zones, world weapons, and prefab groups (which can nest and be scaled).
- **Custom object library:** a menu of ready-made mesh objects you place like a box, plus your own personal collection, kept in a folder on your computer.
<<<<<<< HEAD
=======
- **Terrain generator:** random symmetric hills and valleys with a flat-ground percentage you choose.
>>>>>>> 9e85454 (Add GitHub Actions build workflow and update project files)
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
| Debian, Ubuntu and derivatives (including Chromebook Linux) | `bzmapmaker_1.4.9_all.deb` | `sudo apt install ./bzmapmaker_1.4.9_all.deb`, then start **BZ Map Maker** from your menu or run `bzmapmaker` (optionally `bzmapmaker mymap.bzw`). Installing this way pulls in Python, PyQt6 and NumPy for you, and puts the starter custom objects in `/usr/share/bzmapmaker/objects`. |
| Windows | `BZMapMaker-1.4.9-windows.zip` | Unzip and run `BZMapMaker.exe`. It isn't code-signed, so Windows SmartScreen may warn: choose **More info → Run anyway**. |
| macOS | `BZMapMaker-1.4.9-macos.dmg` | Open the disk image and drag **BZMapMaker** to Applications. It isn't signed or notarized, so the first time, right-click the app and choose **Open**. The build is for the type of Mac that built it (Apple Silicon); on an Intel Mac, run from source below. |

To upgrade the `.deb`, install the newer file the same way.

### Run from source

You need **Python 3.9 or newer**, **PyQt6**, and **NumPy** (NumPy powers the 3D view's depth buffer. It's a very common, lightweight math library, not a graphics driver; nothing here needs a GPU or OpenGL). Then download `bzmapmaker.py` (or clone this repository) and run it. Keep `bzmapmaker.png` next to the script if you want the logo as the window icon, and the `objects` folder next to it if you want the starter custom objects.

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

The release packages are built from this repository by `.github/workflows/release.yml`. Push a tag such as `v1.4.9`, or run the workflow from the **Actions** tab, and download the three packages from the run's artifacts. Each package has to be built on its own operating system, so the workflow uses a Linux, a Windows and a macOS runner. You can also build one locally:

| Package | Command (run on that OS) | Output |
| --- | --- | --- |
| Debian/Ubuntu | `./build_deb.sh` | `bzmapmaker_1.4.9_all.deb` |
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
- **Teleporters inside groups** keep their links. BZFlag names them `<group instance>:<teleporter>` (groups inside groups chain the names, such as `town:padA:in`), so a group that holds teleporters is written with a `name` line, and links to and from them use those names. When the same group is used more than once, a link to one of its teleporters is written as `*:<teleporter>`, which BZFlag treats as a random pick among the copies.
- Links the editor can't represent exactly, such as `from * to *` wildcards or links from one group instance to another, are kept word for word and written back at the end of the file.

### Arcs and cones

Arcs have a **Sweep angle** (360° is a full circle) and a **Hollow ratio** (0 is solid, closer to 1 is a thin ring) plus **Divisions** for how many segments make up the curve. Cones just have **Divisions**. Both can have a single all-faces material, or (arcs only) separate materials per face. See Materials below.

### Zones and world weapons

**Zone** (drag to place) defines an area for spawning and flags. Set which teams spawn there, which teams' flags fly there when dropped, and any flag types in the panel. Zones are invisible in the real game, so the editor shows them as a checkered aid colored by which teams spawn there; **View → Show Zones** toggles that overlay. Symmetry swaps or cycles a zone's teams the same way it does for bases.

**Weapon** (click to place) is a world weapon. Rotation sets its aim direction and Tilt its vertical aim; set what it fires and how often in the panel. It has no appearance in the real game, so it's drawn here as an arrow.

### Groups

Select two or more objects (Ctrl+click each one, or Shift+drag a box in the 2D view), then **Edit → Group Selected** (Ctrl+G). This saves their layout as a reusable prefab (a `define` block, positioned relative to their combined center) and replaces them with a single **group** instance you can move and rotate as a whole.

- To add more objects to an existing group, select the group together with the objects and press Ctrl+G again. Nothing moves, even if the group is rotated or scaled.
- **Groups nest.** Select two or more groups (or groups and other objects) and press Ctrl+G to wrap them in a new group. BZFlag allows a group to contain other groups, but a prefab can't contain itself, and the editor refuses to add a group to one of its own contents.
- A group has **Scale X / Y / Z** in the panel. BZFlag applies a group's transforms in the order they are written, so the editor writes `scale`, then `rotation`, then `shift`. A non-uniform scale on a rotated object inside a group is approximated in the preview.
- **Edit member** in the panel picks one object inside the selected group so you can change its size, position, links and so on.
- A group can optionally override the team color of every base inside it. Set this in the panel's **Group team override** field.
- Teleporters inside a group keep their links (see Teleporters above).
- There is no ungroup command. Prefabs are shared: if two group instances use the same prefab, changing what is inside one changes both.
- The contents of a group inside another group are edited through a top-level instance of that group.

### Custom objects

**Objects → Custom Objects** is a library of ready-made objects built from meshes. Pick one from its category (or **Browse Library...** to search), then click in the 2D or 3D view to place it; the toolbar's last button shows which one is active. It is added as a **group**, so ten copies share one definition, and you can move, rotate and scale it like any group. Its materials are added to the map, and if the map already has a different material with the same name, the object's copy is renamed (`bzmm_crate_2`) so yours is never overwritten.

**Adding your own**

- **Save Selection to My Objects...** saves what is selected in the map (with its materials and any groups inside it) under a name and category you choose.
- **Import into My Objects...** copies the meshes, boxes, groups and materials out of any `.bzw` file. If it holds several separate parts you can make one object from each.
- **Open My Objects Folder** opens your personal folder, so you can also drop `.bzw` files in or share them with friends. The menu rebuilds each time you open it, so new files appear without restarting.
- Teleporters, zones and weapons are left out, because they only make sense in one particular map.

| System | Your objects folder | Built-in objects |
| --- | --- | --- |
| Linux | `~/.local/share/bzmapmaker/objects` | `/usr/share/bzmapmaker/objects` |
| Windows | `%APPDATA%\BZMapMaker\objects` | bundled with the app |
| macOS | `~/Library/Application Support/BZMapMaker/objects` | bundled with the app |

A library object is an ordinary `.bzw` file, so you can make one in any tool. An optional first line gives it a menu entry, and without it the file name is the name and its folder is the category:

```
#bzmm-object name="Lamp post" category="Street" description="A 15 unit street lamp"
```

Groups inside `define` blocks are fine (`define street` can place `group lamppost` and `group planter`), but a `define` can't contain another `define`. Remember scale, rotation, shift order inside a group.

<<<<<<< HEAD
=======
### Generate Terrain

**Objects → Generate Terrain...** adds one mesh called `terrain` (a group you can move, delete or save as a custom object). It follows the example it was modelled on: the ground is a flat plain, hills rise from it in tiers, valleys sink below it, and the slab's sides and bottom use an invisible `clear` material. The materials `flat-ground` (texture `std_ground`) and `hill-mountain` (texture `wall`) are added to the map if they are missing; you can pick other materials in the dialog.

- **Flat ground (%)** is the share of the world left at ground level. Hills and valleys are placed with a flat gap between them until about that share is reached; the dialog shows what was actually reached. Very low values may not fit.
- **Symmetry** repeats every hill and valley exactly: 2-team (180°), 4-team (90°), or mirrored left-right or top-bottom. The toolbar's symmetry mode is used to start with. 3-team (120°) is not offered because a square grid cannot repeat it exactly.
- **Steepest slope** limits every triangle of the mesh (default 45°). A steeper limit lets taller hills fit in less room. **Stepped hills** give each tier a flat landing, like the example; turn it off for one straight slope.
- **Ground height (z)** is where the flat ground sits (the example uses 50). Put bases and other objects at that height, because the plain is a solid slab.
- **Keep ground flat around existing objects** protects the area around bases, boxes and so on that are already on the map. The map edge also stays flat.
- **Cell size** is the grid spacing. Smaller cells give finer terrain but a much bigger mesh, so keep the face count shown in the dialog reasonable (a few thousand is fine).
- **Same seed, same terrain.** Press **New seed** to try another layout.

In BZFlag, tanks drive only on flat ground unless the server is started with `-set _noClimb 0`, which lets them climb slopes of about 45°. If you use sloped terrain, tell players to use that setting.

>>>>>>> 9e85454 (Add GitHub Actions build workflow and update project files)
### Removing unused groups and materials

Every group instance in a map uses a prefab, a `define` block that is saved in the file even if nothing places it any more. This happens when you place a custom object, decide it doesn't work, and delete it: the definition and the materials it brought stay in the file as filler. Two ways to clear them out:

<<<<<<< HEAD
- **Objects → Remove Unused...** shows a checklist of what nothing in the map uses: group definitions that nothing places (including ones only placed by other unused groups), and the materials, texture matrices and physics drivers that only those groups used. Untick anything you want to keep, then click **Remove Checked**. **Undo** (Ctrl+Z) brings it all back. Tick **Also list ... that nothing uses at all** to include things that were never used, such as a material you made but haven't assigned yet; those are left alone by default.
=======
- **Objects → Remove Unused...** shows a checklist of what nothing in the map uses: group definitions that nothing places (including ones only placed by other unused groups), and the materials, texturematrix and physics drivers that only those groups used. Untick anything you want to keep, then click **Remove Checked**. **Undo** (Ctrl+Z) brings it all back. Tick **Also list ... that nothing uses at all** to include things that were never used, such as a material you made but haven't assigned yet; those are left alone by default.
>>>>>>> 9e85454 (Add GitHub Actions build workflow and update project files)
- **When you save**, if the map has unused group definitions you are asked whether to remove them first: **Remove and Save**, **Save As Is**, or **Cancel**. Tick **Don't ask again** to stop the question; **Objects → Ask to Remove Unused When Saving** turns it back on.

Anything a block this editor doesn't understand mentions by name is treated as in use, and removing a group definition never removes a material that something you are keeping still needs.

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

The 3D view colors each face by its material, and where a texture is available it draws a tiny version of it on the face (see Notes on the 3D view). A texture is available when its PNG sits beside the map (or in a `textures` folder there) with the texture's name, when you load a PNG here, or when you fetch an internet texture here. Textures are remembered by name, so it doesn't matter whether you load the PNG before or after naming the material. To avoid every textured object just showing up white when there isn't one:

<<<<<<< HEAD
- **Load PNG...** picks a local image file and automatically sets the material's color to that image's average color (unless you've already set a custom color yourself).
- **Fetch Color from URL** does the same for a texture given as a web address, right now (handy for the one you are typing).
=======
- **Load PNG...** picks a local image file so the 3D view can show it. A material's color is never taken from its image; it stays whatever you set. The picture stays loaded while the program is open, including after you save. When you open a map again, textures are found only if their PNGs sit beside the map or in a `textures` folder next to it.
>>>>>>> 9e85454 (Add GitHub Actions build workflow and update project files)
- Opening a map that already has a texture file sitting next to it does this automatically too, and also keeps the tiny copy for the 3D view. A PNG you add beside the map later is picked up the next time you click **Add / Update** on that material.
- **Internet textures load automatically.** When you open a map, every `http://` texture its materials use is downloaded in the background (and `https://` ones too, though BZFlag itself only loads `http://`), and the 3D view fills them in as they arrive. This is the only thing in the app that goes online, and it only ever downloads images the map asks for. Copies are kept in a cache folder (`~/.cache/bzmapmaker/textures` on Linux, `%LOCALAPPDATA%\BZMapMaker\cache\textures` on Windows, `~/Library/Caches/BZMapMaker/textures` on macOS) so the next open is instant and works offline; delete the folder to clear it. Untick **View → Load Remote Textures Automatically** to stop the app going online.
- **Fetch All URL Textures** retries every internet texture that hasn't loaded (for example because you were offline), or loads them all if automatic loading is off.
- **`addtexture`** is read as the material's texture, the same as `texture`. A map you don't edit is saved exactly as it was; if you edit that material here it is written back as `texture`, and any further `addtexture` layers are kept.

**Texture field / image URLs:** paste a full image URL directly into the Texture field, for example:

```
http://images.bzflag.org/astevens/concrete.png
```

- A pasted `https://` link is automatically rewritten to `http://`, since that's the only scheme BZFlag actually loads.
- If the URL isn't under `http://images.bzflag.org/`, a warning appears. It still saves; the warning just tells you BZFlag clients may not download it by default.
- A URL texture needs its `.png` extension; a local texture name (no `://`) doesn't.

<<<<<<< HEAD
Every other material setting from an imported file (`ambient`, `specular`, extra `addtexture` layers, and so on) is preserved exactly even though this editor doesn't expose controls for it. The **Texture matrix** field sets a material's `texmat`; see Texture matrices below.

### Texture matrices

**Objects → Texture Matrices...** manages the map's named `textureMatrix` blocks, which control how a material's texture moves. Pick one in the list or type a new name (no spaces), tick the settings you want, and click **Add / Update**. Only ticked settings are written, so a matrix that just scrolls doesn't also get unwanted zero lines.
=======
Every other material setting from an imported file (`ambient`, `specular`, extra `addtexture` layers, and so on) is preserved exactly even though this editor doesn't expose controls for it. The **Texturematrix** field sets a material's `texmat`; see Texturematrix below.

### Texturematrix

**Objects → Texturematrix...** manages the map's named `textureMatrix` blocks, which control how a material's texture moves. Pick one in the list or type a new name (no spaces), tick the settings you want, and click **Add / Update**. Only ticked settings are written, so a matrix that just scrolls doesn't also get unwanted zero lines.
>>>>>>> 9e85454 (Add GitHub Actions build workflow and update project files)

| Setting | What it does |
| --- | --- |
| Fixed shift, Fixed spin, Fixed scale | A time-invariant offset, rotation or scale: the texture doesn't move with time. |
| Shift over time | The texture moves linearly; the values are the u and v frequencies. |
| Spin over time | The texture spins around the center point; the value is the rotational frequency. |
| Scale over time | The texture dynamically changes size; u and v frequencies, then u and v scales. |
| Center | The central point that spin and scale work around. |

<<<<<<< HEAD
To use one, open **Objects → Materials...**, pick the material (it needs a texture) and choose the matrix in its **Texture matrix** field. The matrices are written before the materials that use them. The 3D preview shows textures as small approximations and doesn't animate them, so you will see the movement in BZFlag itself. Custom objects whose materials use a texture matrix bring it along when you save or place them.
=======
To use one, open **Objects → Materials...**, pick the material (it needs a texture) and choose the matrix in its **Texturematrix** field. The matrices are written before the materials that use them. The 3D preview shows textures as small approximations and doesn't animate them, so you will see the movement in BZFlag itself. Custom objects whose materials use a texturematrix bring it along when you save or place them.
>>>>>>> 9e85454 (Add GitHub Actions build workflow and update project files)

### Physics drivers

**Objects → Physics Drivers...** manages the map's named physics drivers (the things that make a surface move, spin, slide or kill). Create or edit a driver there, then assign it to a box, pyramid, arc or cone with the **Physics driver** field in the Selected Object panel. Symmetry copies get their own driver, transformed to match.

### Map options

**Objects → Map Options...** edits the map's `options` block: the same flags you would otherwise pass to `bzfs` on the command line, including `-set` for any server variable. It's a plain text box, and what you type is written out exactly as typed.

### Map Text (advanced)

Tabbed alongside the Selected Object panel (click the tab to switch to it). This shows the raw `.bzw` text of your current map, live.

- **Refresh from Map** pulls in the latest state; it also updates on its own a moment after you make a change, but only while this tab is visible. The cursor and scroll position are kept, and the automatic refresh leaves the box alone while it holds edits you haven't applied (the status line under the text says "edited, not applied yet"). The Refresh button asks before throwing those edits away.
- **Find, replace and go to line**, like a text editor. **Ctrl+F** (or the **Find / Replace** button) opens a find bar: matches are highlighted and counted as you type, **Enter** or **F3** goes to the next match and **Shift+F3** to the previous, and the search wraps around. **Ctrl+H** jumps to the replace field; **Replace** changes the current match and moves on, and **Replace All** changes every match as a single undo step (Ctrl+Z inside the box). **Match case** and **Whole words** are tick boxes, and **Esc** closes the bar. **Ctrl+L** (or the **Go to Line** button) asks for a line number, and the status line always shows the cursor's line and column. Line numbers run down the left side of the box, with the current line in bold. These shortcuts only work while the cursor is in the Map Text box. Changes made with replace are edits to the text, so press **Apply to Map** to use them.
- **Apply to Map** parses whatever's in the box and replaces the map with it, the same as opening a file, so undo still works normally afterward. This is how you hand-add an object by typing or pasting a block directly.
- With **"Apply the toolbar's symmetry mode to new objects"** checked (on by default) and a symmetry mode active, any object that's genuinely new in the text you applied gets its symmetric copies built automatically, the same as if you'd placed it with the mouse. Editing an existing object's numbers, rather than adding a new block, is treated as a plain edit and won't trigger this, so refreshing, glancing at the text, and applying it again unchanged never duplicates anything.

### Import Mesh Object

**Objects → Import Mesh Object...** opens a file picker for any `.bzw`. It pulls out just the `mesh ... end` blocks from that file (other object types in it are skipped) and adds them to your current map at the origin, along with any materials those meshes refer to. Handy for keeping a "parts bin" file of your favorite mesh props.

### Recent files

**File → Open Recent** keeps your last 12 opened or saved files, most recent first, and remembers them the next time you start the app. Files that have been moved or deleted are quietly dropped from the list.

## Supported `.bzw` content

Reads and writes `world`, `options`, `physics`, `textureMatrix`, `material`, `box`, `pyramid`, `base`, `teleporter`, `arc`, `cone`, `mesh`, `zone`, `weapon`, `define`/`group`, and `link` blocks, plus per-face `matref` on boxes and arcs. Any block type this editor doesn't specifically handle (dynamic colors, water level, transforms...) is preserved exactly as written and saved back unchanged. It just isn't drawn or editable here.

**Known limits:**

- Meshes can be moved but not reshaped here. Put a mesh in a group to rotate or scale it.
- There is no ungroup command.
- Each teleporter face holds one link in the editor (extra links in a file are kept as written, but not shown).
- The custom object library has no picture previews yet, only names, categories and descriptions.
- Comments in the file are lost on save.
<<<<<<< HEAD
- Textures in the 3D preview are 16-pixel approximations, repeated every 10 world units by a simple mapping that is not BZFlag's own, so they show the general look rather than exact placement or scale. Texture matrices (movement) are not shown.
=======
- Textures in the 3D preview are 16-pixel approximations, repeated every 10 world units by a simple mapping that is not BZFlag's own, so they show the general look rather than exact placement or scale. Texturematrix (movement) are not shown.
>>>>>>> 9e85454 (Add GitHub Actions build workflow and update project files)

**Keep a backup of any map you didn't create in this editor** before saving over it, especially an unusually structured one.

## Notes on the 3D view

The 3D view uses a real per-pixel depth buffer (built with NumPy), so overlapping objects always render in the correct front-to-back order regardless of draw order. This fixed an earlier issue where a long, thin object could visually glitch through a nearby one. Building that buffer costs some CPU every time the view redraws, so a very large map or very high-division arcs/cones may feel less smooth while dragging or orbiting than a simple map does.

It needs no graphics card or OpenGL, which keeps it dependable on school Chromebooks and other low-spec machines. What the picture is made of, from back to front:

- **Base color:** each face's material color, shaded by a simple light direction.
- **Default textures (View → Show Textures in 3D):** a face with no material gets what BZFlag gives it: boxes and arcs have `boxwall` walls with a `roof` top and bottom, pyramids are `pyrwall`, and cones are `boxwall`. These are 16-pixel versions built into the program, so nothing has to be installed, and nothing about them is written to your map. Give an object (or just one of its faces) a material and that replaces the default. Bases, teleporters, zones and meshes keep plain colors.
- **Texture (View → Show Textures in 3D):** if the material has a texture, a copy shrunk to at most 16 pixels on its longer side (a 1000 × 1600 picture becomes 10 × 16, under a kilobyte) is repeated across the face, with correct perspective. The picture's own colors are only lightly shaded (down to 75%, against 50% for plain colors) so they stay close to the original. Only that tiny copy is kept, never the full picture, so a huge image can't use up memory.
- **Edge shading (View → Edge Shading in 3D):** after the picture is drawn, the depth buffer is searched for sharp steps, and the farther side of each is darkened. It is a handful of array operations and costs next to nothing.

**Speed.** Each triangle has a fixed cost for the numbers that place it on screen, so on a map of many small shapes the time depends on how many triangles there are far more than on how many pixels. Each shared corner is now projected once and the per-triangle work is leaner, which made a 340-object test map render about 1.6 times faster with a pixel-for-pixel identical result. While you orbit or drag, the view can drop to half resolution, but that only helps when pixels are what a frame costs, so the program times both and does it only when half resolution is clearly quicker (and the full frame is slow enough to matter). Otherwise it stays sharp.

## Ideas for the future

<<<<<<< HEAD
- Texture mapping that follows BZFlag's own rules, and showing texture matrices moving
=======
- Texture mapping that follows BZFlag's own rules, and showing texturematrix moving
>>>>>>> 9e85454 (Add GitHub Actions build workflow and update project files)
- An optional OpenGL 3D view for machines with a working graphics card (the lightweight NumPy one would stay the default and the fallback)
- Editing mesh geometry (not just moving it)
- An ungroup command
- Several links per teleporter face
- Picture previews in the custom object library, and dropping a `.bzw` onto the window to import it
- Regular-expression find in the Map Text
- Saving symmetry pairs so they survive a save/reload outside of Map Text

## License

LGPL-2.1. See [LICENSE](LICENSE).
