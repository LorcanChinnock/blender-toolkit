# Blender Toolkit

A Blender add-on that collapses the repetitive click-paths of game-asset work
into single buttons: retopology setup, mesh checks, shapekey surgery,
vertex-group falloffs, rigging helpers, and an FBX export preset.

**Requires Blender 5.2 LTS.** It uses 5.x-only API, so it will not run on
3.6 or 4.x.

![Toolkit sidebar in the 3D View](docs/images/hero.png)
<!-- screenshot placeholder: N-panel open on the Toolkit tab -->

---

## Install

1. Download `blender_toolkit.zip` from the
   [latest release](https://github.com/lorcanchinnock/blender-toolkit/releases/latest).
2. In Blender: **Edit ▸ Preferences ▸ Add-ons ▸ ⌄ ▸ Install from Disk…**
3. Pick the zip, then tick **Blender Toolkit** in the list.

That's it — no dependencies, nothing to build.

![Installing from disk](docs/images/install.png)
<!-- screenshot placeholder: Preferences > Add-ons > Install from Disk -->

### Where the tools live

| | |
| --- | --- |
| **Sidebar** | 3D View ▸ press `N` ▸ **Toolkit** tab |
| **Pie menu** | `Shift + Alt + Q` |
| **Toggles** | Preferences ▸ Add-ons ▸ Blender Toolkit — turn off any of the six modules and its panel and pie slot disappear |

### Updating

Install the new zip over the old one, then restart Blender.

---

## Features

Six independent modules. Each section below is one panel in the sidebar.

<!-- TOC -->
- [Retopology](#retopology) — one-click shrinkwrap + snapping setup
- [Mesh](#mesh) — find the geometry that will cause trouble later
- [Shapekeys](#shapekeys) — apply modifiers without losing keys, split keys by weight
- [Weights](#weights) — interactive vertex-group gradients
- [Rigging](#rigging) — humanoid validation, pose toggle, twist bones
- [Export](#export) — FBX with game-engine defaults

---

### Retopology

![Retopology panel](docs/images/retopo.png)
<!-- screenshot placeholder: before/after of Setup Retopo -->

**Setup Retopo** — select your high-poly sculpt, press the button, start drawing.

It creates an empty mesh in the same collection, draws it in front of the
sculpt, adds a Shrinkwrap modifier onto the sculpt with Display on Cage, turns
on Face Project snapping, and leaves you in Edit mode.

#### Pressing it again

The second press takes you back to the retopo mesh you already have — from the
sculpt or from the retopo mesh itself. It doesn't make a second one.

What marks a mesh as a retopo mesh is simply **its Shrinkwrap modifier**. There
is no hidden bookkeeping: delete the modifier and the mesh is an ordinary mesh
again, which is also how you ask for a second retopo mesh on the same sculpt.
A shrinkwrap you added by hand counts too.

#### Options

All five are in the redo panel (`F9`), so you can change your mind after the
fact rather than undoing and starting over.

| Option | What it does |
| --- | --- |
| **Offset** | Lifts the shell off the surface. The fix for z-fighting on a dense sculpt. |
| **Mirror** | Adds a Mirror modifier **above** the shrinkwrap — so the mirrored half projects too — with Clipping on, and turns on the mesh's X symmetry. Turning it back off removes it again. |
| **Seed From Selection** | Starts the mesh from a copy of the sculpt's selected faces instead of empty. Nothing selected just means you start empty. Skipped when it's returning you to a mesh you already have, since that would drop a duplicate on top of your work. |
| **Snapping** | On by default. Turn it off to leave your snap settings alone. |
| **Auto Merge** | Welds vertices dropped on top of each other. Off unless you ask. |

Both are scene settings, and the operator doesn't put them back — same as
changing them yourself. Ctrl+Z, or the header, if you want them back.

---

### Mesh

**Validate Mesh** — reports the geometry that will cause trouble later, and
**selects it** so you can go straight to it. Aimed at retopo output, but it
checks any mesh.

| Checked | Why |
| --- | --- |
| **Ngons** | Faces over four sides. They triangulate unpredictably and subdivide badly. |
| **Poles** | Vertices joining more than five edges. Pinches under subdivision. |
| **Non-manifold edges** | Edges shared by three or more faces. |
| **Loose** | Vertices on no edge, edges on no face. Invisible, and they still export. |
| **Flipped** | Neighbouring faces that disagree about which way is out — what Recalculate Normals fixes. |

A clean mesh reports *Mesh is clean*.

**Boundary edges are deliberately not reported.** Blender counts an edge with
one face as non-manifold, but a retopo shell in progress is nearly all boundary,
so including them would bury everything else.

Selection follows your select mode, so loose vertices and poles show up in
**vertex** mode — switch to it if a reported count seems to select nothing.

---

### Shapekeys

![Shapekeys panel](docs/images/shapekeys.png)
<!-- screenshot placeholder: shapekey list + Split dialog -->

#### Apply Modifiers (Keep Shapekeys)

Blender refuses to apply a modifier to a mesh that has shapekeys. This bakes
each key on a duplicate, applies the modifiers there, and joins everything back
with the original names, values and slider ranges intact.

> **Note** — modifiers that add geometry are fine: Subsurf, Mirror, Solidify
> and Array build the same topology whatever a key does, so the keys come back
> subdivided or mirrored with them. What cannot work is a modifier that rebuilds
> geometry *from its shape* — Weld, Decimate, Remesh, Boolean — because each key
> then ends up with a different vertex count and there is nothing to join to.
> Those are reported with the offending keys named, before anything is changed.

#### Split Shapekey

Splits one key into two, each weighted by one of a pair of vertex groups —
`Name_L` and `Name_R` from `Left` and `Right` by default. The group names and
suffixes are properties, so the same operator handles any pair
(`Upper`/`Lower`, `_Up`/`_Lo`). Build the groups with **Weight Gradient** below.

Weights are **baked into the coordinates**, not left as a live
`ShapeKey.vertex_group` mask. A key only holds one mask group, so a live mask
can't be chained: splitting an already-split key would replace the first mask
instead of intersecting it, and the half you thought you'd excluded would deform
at full strength. Baking means results can be split again, and the keys carry no
vertex-group dependency into the exported FBX.

*Trade-off:* repainting a group no longer updates the key — re-run the split.

**Four quadrants** = two runs. Split by `Left`/`Right`, then split each result by
`Upper`/`Lower`. The four keys sum back to the original. Turn off **Keep Source**
on the second pass to drop the intermediates.

---

### Weights

![Weight gradient handles in the viewport](docs/images/weights.png)
<!-- screenshot placeholder: handles + ramp in the viewport -->

**Weight Gradient** fills a vertex group with a falloff you place in the
viewport. It writes a plain vertex group, so it feeds modifier masks, cloth pin
groups, influence limits — anything that takes a group.

#### Making one

Pick a vertex group in **Properties ▸ Object Data ▸ Vertex Groups**, open the
**Toolkit** tab in the sidebar and hit **Add Gradient**. No group selected? It
makes one. You land in Weight Paint with the path drawn as draggable discs.

A gradient belongs to its vertex group, so selecting the group is how you get
back to it later. There is no separate list to keep track of, and the panel
always works on whichever group is active.

Everything updates live — drag a handle, drag a stop, switch shape. Nothing to
re-run and nothing to confirm.

A second gradient starts as a copy of the last one you made, so the usual
left/right pair is: make one, make another on a new group, tick **Invert**. The
two add up to exactly 1 everywhere.

For scripting there's `tk.write_gradient`, which writes the weights once and
keeps no gradient behind.

#### Painting ends it

While a gradient is live it rewrites its group on every change, so your brush
strokes wouldn't survive. So **painting on the group detaches the gradient** —
the weights stay exactly as they are, stroke and all, and the group becomes
ordinary weights. That is the Apply button, and the Remove button, and you never
have to find either.

Ctrl+Z is the way back. To protect a region *while* the gradient is live, use
**Mask** instead.

Posing an armature or moving a shape key slider won't end a gradient — only an
actual change to its weights does.

#### Shape

| Shape | What it does |
| --- | --- |
| **Path** | Ramps along the path through the handles. |
| **Spherical** | Ramps outwards from the first handle to the last. Invert it for a soft mask around a point. |
| **Band** | Ramps out to both ends from the middle. Invert it for a plateau in the middle. |

**Curved** bends the path smoothly through the handles instead of running
straight between them. Spherical and Band only use the first and last handle.

**Smooth** blurs the finished weights across neighbouring vertices.

#### Handles

Handles are placed by dragging them in the viewport — there are no coordinate
fields. Each one is drawn in weight paint's colour for the weight it sits at, so
a handle and the surface under it always match.

**Snap** decides what a dragged handle lands on: **Free** drags in the view
plane, **Vertex** / **Edge** / **Face** put it on the mesh. It snaps as you drag,
and it aims down the view ray — the handle lands on whatever is under the
cursor, on the side facing you.

**Where the handles start** is up to **Points From**. **Auto** uses your
Edit-mode selection if there is one, otherwise spans the object left to right.
There is also **Selection** (errors instead of falling back), **Object Bounds**
along an axis, and **Keep Current**.

#### The ramp

The bar is the weight scale, not a colour picker — blue at 0 through cyan,
green and yellow to red at 1, exactly weight paint's own colours. Where a stop
sits on the bar *is* its handle's weight, and the colour follows automatically,
so the picker Blender opens has nothing to choose.

**The ramp decides how many handles there are** — one per stop. Hit `+` and a
handle appears; remove the stop and it goes. The floor is two and the ceiling is
32, which is Blender's own limit on ramp stops.

Stops stay in order, and handles are matched to them along the path, so weights
run **monotonically** from one end to the other. Dragging one stop past another
swaps which handle owns which weight. For a peak in the middle, use **Band** or
a second gradient with a mask.

**Profile** shapes how the weight travels *between* handles — Linear, Smooth,
Sphere, Root, Sharp, Inverse Square, Constant. A handle always reads back
exactly the weight its stop shows, and past the outermost handle the weight
holds flat.

Three buttons even things out:

| Button | What it does |
| --- | --- |
| **Weights** | Spreads the stops evenly from one end of the scale to the other. Moves no handle. |
| **Space** | Spreads the handles evenly along the path, ends pinned. Changes no weight. |
| **Relax** | Smooths kinks out of the path. **Factor** and **Repeat** in the redo panel (F9) control how much; press again for more. |

Space and Relax aren't the same thing — on a kinked path, Space keeps the kink
and evens the spacing, Relax flattens the kink.

#### Blend and Mask

**Blend** is what the gradient does to weights the group already had. Same set
and same names as the **Vertex Weight Mix** modifier:

| Mode | Result |
| --- | --- |
| **Replace** | The gradient's weight. The default. |
| **Add** | The two summed, clamped to 1. |
| **Multiply** | What was there, scaled by the gradient. |
| **Minimum** / **Maximum** | The lower or higher of the two. |

Blending is always against the weights the group had when the gradient took it
over, never against the gradient's own last result — so changing mode recomputes
from the originals instead of stacking, and switching back to **Replace** gets
you exactly where you started.

**Mask** takes a vertex group and only writes where that group has weight.
Elsewhere the existing weights are left alone, and a soft mask edge blends old
into new. The protected region is tinted red in the viewport, so a low weight
there reads as masked rather than as the gradient bottoming out.

#### Good to know

**Locking the group pauses the gradient.** The panel says so, and unlocking lets
the next change through. You can't add a gradient to a locked group.

**Renaming the group is fine** — the gradient follows it. Deleting the group, or
reordering your groups, drops the gradient instead; Ctrl+Z brings it back.

Gradients live on the **object** and save with the file, so two meshes each keep
their own.

---

### Rigging

![Rigging panel](docs/images/rigging.png)
<!-- screenshot placeholder: Validate Humanoid report + twist bones -->

| Tool | What it does |
| --- | --- |
| **Validate Humanoid** | Reports which humanoid bones the active armature is missing, checked per side. Name matching is case- and separator-insensitive and understands Rigify, Unity and Mixamo conventions — `UpperArm_L`, `upper_arm.L`, `mixamorig:LeftForeArm` all resolve. |
| **Pose Mode** | Toggles the active armature between Object and Pose mode, clearing every bone transform on the way in and out. The small side button is the same thing with **Reset Pose** off, for when the current pose has to survive the round trip. |
| **Add Twist Bones** | In Edit mode, adds a half-length `twist_<name>` bone parented to each selected bone, then constrains it in Pose mode with Copy Rotation: Y axis only, local/local, influence 0.5. |

---

### Export

![Export dialog](docs/images/export.png)
<!-- screenshot placeholder: FBX export sidebar with Game Ready preset -->

**Export FBX (Game Ready)** — a file-browser export of the current selection with
engine-friendly defaults: `-Z` forward, `Y` up, face smoothing, `FBX_SCALE_ALL`,
no leaf bones, no animation baking.

Everything is shown and editable in the export dialog's sidebar. The **Preset**
dropdown at the top is the reset — pick *Game Ready* to restore every value.
Editing a setting doesn't flip the dropdown to *Custom* on its own.

---

## Development

### Set up a live checkout

Symlink the package into Blender's addons directory so edits take effect without
reinstalling.

<details open>
<summary><b>macOS</b></summary>

```bash
git clone https://github.com/lorcanchinnock/blender-toolkit.git
cd blender-toolkit
mkdir -p "$HOME/Library/Application Support/Blender/5.2/scripts/addons"
ln -sfn "$PWD/blender_toolkit" "$HOME/Library/Application Support/Blender/5.2/scripts/addons/blender_toolkit"
```
</details>

<details>
<summary><b>Linux</b></summary>

```bash
git clone https://github.com/lorcanchinnock/blender-toolkit.git
cd blender-toolkit
mkdir -p "$HOME/.config/blender/5.2/scripts/addons"
ln -sfn "$PWD/blender_toolkit" "$HOME/.config/blender/5.2/scripts/addons/blender_toolkit"
```
</details>

<details>
<summary><b>Windows</b> (PowerShell, as Administrator or with Developer Mode on)</summary>

```powershell
git clone https://github.com/lorcanchinnock/blender-toolkit.git
cd blender-toolkit
$addons = "$env:APPDATA\Blender Foundation\Blender\5.2\scripts\addons"
New-Item -ItemType Directory -Force -Path $addons
New-Item -ItemType SymbolicLink -Path "$addons\blender_toolkit" -Target "$PWD\blender_toolkit"
```
</details>

Restart Blender and enable **Blender Toolkit** in Preferences ▸ Add-ons.

### Tests

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python tests/smoke.py
```

Headless run of every operator end to end, `PASS`/`FAIL` per tool, non-zero exit
on failure. It can't cover the pie menu — background Blender has no addon
keyconfig.

### Reloading after an edit

| Changed | Needed |
| --- | --- |
| Function body | System ▸ Reload Scripts (`F3`) |
| Class name, `bl_idname`, class added/removed | Disable + re-enable the add-on |
| `bl_info` | Restart Blender |
| A whole new module | Disable + re-enable the add-on |

Reload Scripts re-runs `register()` without a clean `unregister()`, so renamed or
deleted classes stay registered under their old names until a full toggle.

### Layout

```
blender_toolkit/
├── __init__.py       bl_info, reload block, register/unregister
├── preferences.py    module toggles
├── ui_pie_menu.py    Shift+Alt+Q pie, keymap registration
├── utils/            prefs(), ensure_mode(), load_submodules()
└── tools/
    ├── __init__.py   registers submodules, panels follow the preferences
    └── <module>/     __init__.py + operators.py + ui.py
```

House rules, all deliberate:

- **No logic in any `__init__.py`** — imports and register/unregister loops only.
- **Naming:** `TK_OT_*` operators, `TK_PT_*` panels, `TK_MT_*` menus, `TK_PG_*`
  property groups, `TK_GGT_*` gizmo groups. Operator ids are `tk.<verb_noun>`.
- **Keep the maths out of the operator** — `tools/weights/gradient.py` imports no
  `bpy`, so falloff shapes are unit-testable directly.
- **Every operator needs a `poll()`** that fails closed in the wrong mode.
- **Panels:** `VIEW_3D` / `UI` / `bl_category = "Toolkit"`.
- **The pie fills slots W, E, S**, so each mode branch emits exactly two entries
  to keep the exporter pinned to the bottom.

See [CLAUDE.md](CLAUDE.md) for the longer version, including the gotchas already
paid for.

### Adding a tool module

Create `tools/<name>/` with `__init__.py`, `operators.py` and `ui.py` mirroring
an existing module, add the name to `MODULE_NAMES` in `tools/__init__.py`, and
add a matching `use_<name>` boolean in `preferences.py`. `test_modules_wired`
fails if those three get out of step.

---

## Contributing

Issues and pull requests welcome. Before opening a PR:

- Run the smoke test above.
- Keep it **generic**. This is a general mesh/rig toolkit, not a character or
  avatar add-on. If a body part shows up in an operator id, label, property name
  or docstring, the abstraction is one step too specific — ship the mechanism the
  example is an instance of.

## License

MIT — see [LICENSE](LICENSE).
