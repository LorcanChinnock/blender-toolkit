# Blender Toolkit

A Blender add-on that collapses the repetitive click-paths of game-asset work
into single buttons: retopology setup, shapekey surgery, vertex-group falloffs,
rigging helpers, and an FBX export preset.

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
| **Toggles** | Preferences ▸ Add-ons ▸ Blender Toolkit — turn off any of the five modules and its panel and pie slot disappear |

### Updating

Install the new zip over the old one, then restart Blender.

---

## Features

Five independent modules. Each section below is one panel in the sidebar.

<!-- TOC -->
- [Retopology](#retopology) — one-click shrinkwrap + snapping setup
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

---

### Shapekeys

![Shapekeys panel](docs/images/shapekeys.png)
<!-- screenshot placeholder: shapekey list + Split dialog -->

#### Apply Modifiers (Keep Shapekeys)

Blender refuses to apply a modifier to a mesh that has shapekeys. This bakes
each key on a duplicate, applies the modifiers there, and joins everything back
with the original names, values and slider ranges intact.

> **Note** — modifiers that change vertex count (Subsurf, Mirror, Solidify, …)
> are rejected up front with an error. Join as Shapes needs matching vertex
> counts, and going ahead anyway would silently corrupt the keys. Apply or
> remove those first.

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
<!-- screenshot placeholder: handles + gradient ramp mid-session -->

**Weight Gradient** builds vertex-group weights from a spatial falloff between
two points: **start** reads 0, **end** reads 1. It writes a plain vertex group,
so it feeds modifier masks, cloth pin groups, influence limits, corrective
blends — anything that takes a group.

The points are arbitrary positions, so the gradient runs in any direction; a
tilted plane is just a start and end that aren't axis-aligned.

#### Interactive session

One button, **Weight Gradient**, opens a live session. It remembers your current
mode, drops you into Weight Paint, and draws the path in the viewport as
draggable handles. Every change in the panel rewrites the weights — drag a
handle, edit the ramp, switch shape — with no re-running and no undo spam.

Writes are batched rather than done per event: a drag fires an update on every
mouse-move, so the session collects them and writes at most once every 150 ms.
On a 65k-vertex mesh, moving a handle rebuilds the falloff in 150–500 ms
depending on how many handles there are; anything else — the ramp, the profile,
the mask, the group name — reuses the cached field and costs about 110 ms
regardless.

| Button | What it does |
| --- | --- |
| **Add** | Keeps the current group and moves straight on to the next one: the gradient resets to defaults and the name advances to one nothing is using. The path stays where you dragged it — its two ends, at least, since the ramp goes back to two stops. |
| **Finish** | Keeps the current group and ends the session, back to your original mode. |
| **Cancel** | Ends the session and throws away the gradient in progress, including deleting a group the session created. Anything you already hit Add or Finish on is kept. |

**Every session opens clean** — defaults, and a group name nothing is using yet.
Last round's shape, mask and ramp are not what you want on the next group, so
none of it carries over. **Add** opens clean the same way, minus the handles:
the next group almost always runs over the same span, and re-placing the path is
the part that took the work. Sessions live on the **object**, so two meshes in a
file each keep their own.

While a session is running you'll see a `tk.backup.<group>` vertex group for any
group it borrowed. That's how Cancel puts the original weights back — it's real
data rather than something held in memory, so it survives undo and a file save.
Add and Finish both clear it.

Nothing is committed until **Add** or **Finish**. Renaming **Group** mid-session *moves* the
gradient rather than leaving a copy behind: a group the session created is
removed under the old name, and one it only borrowed gets its original weights
back. Only the group named right now carries the gradient.

#### Coming back to a group

**Add** saves the gradient — handles, ramp, shape, mask, everything that decides
the result — onto the object, keyed by the group's name. It's stored as a custom
property on the object, so it survives saving the file.

**Group** is a vertex-group search field, so picking an existing group is how you
go back to one: pick it and its saved gradient loads straight back, ready to
adjust instead of rebuilt by eye. The panel says **Editing its saved gradient**
when the group you've picked has one. Typing a name that doesn't exist yet still
makes a new group, same as before.

The field lists *all* vertex groups rather than only the ones this tool built,
because pointing a gradient at a group that already exists is useful in itself —
it overwrites that group and, with **Mask**, blends into it. Picking a group with
no saved gradient just means there's nothing to load.

The catch: the key is the group's **name**, so renaming the group in Blender's
own vertex-group list orphans its saved gradient. (`VertexGroup` can't hold
custom properties at all, so there's nowhere closer to attach it.) Renaming in
the panel's **Group** field after an Add doesn't rename the committed group
either — it starts the next one, which is the point of Add.

#### Shapes

| Shape | Falloff |
| --- | --- |
| **Path** | Ramp along the path running through the handles. |
| **Spherical** | Distance from the first handle, reaching 1 at the last. Inverted: a local mask around a point. |
| **Band** | 0 on the plane through the middle, 1 at both ends. Inverted: a plateau between the two points. |

#### Handles

A gradient is a **path**, not just two points. `t` is each vertex's position
along the *arc length*, so past a bend the weight follows the bend rather than
the straight line.

Where two stretches of the path are about equally close, they **share** a
vertex rather than the nearer one taking it outright. Taking the nearest
outright is what produces hard bands on the concave side of a bend: a vertex
there is equidistant from two stretches whose positions along the path are far
apart, and the weight jumps across that tie line. Sharing removes the jump
entirely; away from a bend one stretch is clearly nearest and nothing changes.
Handles are unaffected either way — the two segments meeting at a bend both
report that corner, so they already agree.

Smoothing is not an alternative here: it diffuses a step as 1/√passes, so on a
0.50 jump twenty passes only reached 0.07 while blurring everything else. Two handles behaves exactly like a plain linear ramp.
**Curved** smooths the path through the handles instead of running straight
between them.

**The gradient decides how many handles exist** — one per stop. Hit `+` on the
gradient and a handle appears at that spot along the path; remove the stop and
it goes. The ceiling is **32**, which is Blender's own hard limit on ramp stops
rather than one this add-on imposes. Handles are positioned by dragging in the viewport, never by typing
coordinates. Adding a stop in the middle inserts a handle in the middle, and
handles you've already dragged stay put.

**A stop's position on the bar is its handle's weight.** Slide it right and that
handle weighs more; the handle recolours to match and moves nowhere in the
viewport. Where a handle falls *along the path* is the handle's own business —
you dragged it there in 3D — so the two controls never fight over it.

One consequence: the widget keeps stops in order and handles are matched to them
in path order, so weights run **monotonically** along the path. Dragging one stop
past another swaps which handle owns which weight. For a peak in the middle, use
the **Band** shape or a second gradient with a mask.

Spherical and Band only read the first and last handle, so the panel stops
offering the ones in between.

**Snap** decides what a dragged handle lands on: **Free** moves it in the view
plane; **Vertex** / **Edge** / **Face** put it on the mesh. Snapping happens as
the handle moves, not on release, and it aims **down the view ray** — the handle
lands on whatever is under the cursor, on the side of the mesh you're looking
at. (Snapping to the nearest surface in 3D would drop it on the far side as soon
as you dragged past the surface.)

**Handles are drawn in weight paint's own colour for the weight they sit at** —
blue at 0, through cyan, green and yellow, to red at 1. A handle and the surface
under it agree, so inverting or dragging a stop recolours the handles to match
without needing a legend. They're solid discs rather than rings.

#### Values

The **gradient** maps position along the path to weight. Its `+` / `−` buttons
add and remove stops, and therefore handles — that native add/remove is why it's
a colour ramp widget rather than a curve one, and why there is no second handle
list to keep in step with it.

The bar is a **fixed scale you read a weight off** — weight paint's own, blue →
cyan → green → yellow → red — not a colour picker. A stop's colour is simply the
scale's colour where it sits, so there is nothing to choose: Blender's widget
still opens a picker, but whatever you pick is overwritten on the next poll.

That works because weight paint's ramp is a sweep of hue at full saturation —
one number. The ramp is held in **HSV / Counter-Clockwise**, where the bar
renders as the true scale. In the ordinary RGB mode blue → red runs through
purple instead, which is no weight at all; the mode dropdowns are part of the
widget, so switching them is undone too.

**Profile** decides how the weight travels *between* one handle and the next —
Linear, Smooth, Sphere, Root, Sharp, Inverse Square, Constant. It shapes the
segments, never the handles: a handle always reads back exactly the weight its
stop shows. Past the outermost handle the weight holds flat — a handle is a
control point, not a boundary.

**Distribute Evenly** spaces the stops out from one end of the scale to the
other, which is the plain even run you'd get from a gradient you never touched.
It's the way back after hand-editing the stops.

Blender lets a ramp drop to a single stop; a gradient needs two ends, so the
floor here is **two** — remove past it and the missing stop reappears at the far
end. The ramp has no update callback and its `+` / `−` are not even operators, so
edits to it are noticed by polling while a session is open.

| Option | What it does |
| --- | --- |
| **Smooth** | Relaxation passes over the finished weights. |
| **Mask** | Give it a vertex group and weights are only written where that group has weight. Outside it, existing weights are untouched; a soft mask edge blends old into new. The protected region is **tinted red in the viewport**, so a low weight there reads as masked rather than as the gradient reaching zero. |
| **Invert** | Negates every weight, and moves the stops to where those weights now read on the bar. A gradient and its inverse add up to **exactly 1** at every vertex, so the pair is genuinely complementary — Add, tick Invert, Finish. |

#### Where the handles start

**Auto** by default: the Edit-mode selection if there's a usable one, otherwise a
left-to-right gradient across the whole mesh. The button always does something
sensible — you never have to go select two vertices first. The report line says
which it used.

The alternatives are **Selection** (strict — errors instead of falling back),
**Object Bounds** along a chosen axis, and **Keep Current**. Bounds are measured
from the mesh's own vertices, not the object bounding box, since an active shape
key or a modifier moves the latter away from the coordinates the weights are
computed from. Asking for an axis the mesh is flat along falls back to its
longest one.

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
