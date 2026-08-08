# blender-toolkit

Blender add-on bundling the repetitive click-paths of game-ready asset work into
single operators: retopology setup, shapekey surgery, rigging helpers, and an
FBX export preset.

Requires **Blender 5.2 LTS**. It uses 5.x-only API (`snap_elements_individual`),
so it will not run on 3.6/4.x without changes.

## Install

Symlink the package into Blender's addons directory so edits are live:

```bash
mkdir -p "$HOME/Library/Application Support/Blender/5.2/scripts/addons"
ln -sfn "$PWD/blender_toolkit" "$HOME/Library/Application Support/Blender/5.2/scripts/addons/blender_toolkit"
```

Restart Blender, then enable **Blender Toolkit** in Preferences ▸ Add-ons. The
tools appear in the 3D View sidebar (`N`) under the **Toolkit** tab, and in a
pie menu on `Shift + Alt + Q`.

For a normal (non-dev) install, zip the `blender_toolkit/` directory and use
Preferences ▸ Add-ons ▸ Install from Disk.

## Tools

### Retopology

**Setup Retopo** (`tk.retopo_setup`) — with the high-poly sculpt active, creates
an empty mesh in the same collection, draws it in front, adds a Shrinkwrap
modifier targeting the sculpt with Display on Cage, turns on Face Project
snapping, and drops you into Edit mode ready to draw geometry.

### Shapekeys

**Apply Modifiers (Keep Shapekeys)** (`tk.apply_modifiers_shapekeys`) — Blender
refuses to apply modifiers to a mesh with shapekeys. This bakes each key on a
duplicate, applies the modifiers there, and joins the results back as shapekeys
with their original names, values and slider ranges restored.

Modifiers that change the vertex count (Subsurf, Mirror, Solidify, …) are
rejected with an error rather than silently producing corrupt keys — Join as
Shapes requires matching vertex counts. Apply or remove those first.

**Split Shapekey L/R** (`tk.split_shapekey`) — splits the active shapekey into
`Name_L` and `Name_R`, masked by vertex groups named `Left` and `Right`. Both
groups must exist. The original key is left in place.

### Rigging

**Validate Humanoid** (`tk.validate_humanoid`) — reports which humanoid bones the
active armature is missing (limbs checked per side). Name matching is
case- and separator-insensitive and understands Rigify, Unity and Mixamo
conventions: `UpperArm_L`, `upper_arm.L`, `mixamorig:LeftForeArm` all resolve.

Aliases live in `PART_ALIASES` in `tools/rigging/operators.py`. Order matters —
the first match wins, so `forearm` is tested before the bare `arm` fallback, and
Mixamo's `UpLeg` (thigh) before `Leg` (calf).

**Add Twist Bones** (`tk.add_twist_bones`) — in Edit mode, adds a half-length
`twist_<name>` bone parented to each selected bone, then constrains it in Pose
mode with Copy Rotation: Y axis only, local/local, influence 0.5.

### Export

**Export FBX (Game Ready)** (`tk.export_game_fbx`) — file browser export of the
current selection with engine-friendly settings baked in: `-Z` forward, `Y` up,
face smoothing, `FBX_SCALE_ALL`, no leaf bones, no animation baking.

## Preferences

Each of the four modules can be toggled off in the add-on preferences. Disabling
one unregisters its sidebar panel and drops it from the pie menu.

## Development

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python tests/smoke.py
```

Headless test covering every operator end to end. Exits non-zero on failure. It
cannot cover the pie menu — background Blender has no addon keyconfig.

After editing, how much of a reload you need depends on what changed:

| Changed | Needed |
| --- | --- |
| Function body | System ▸ Reload Scripts (`F3`) |
| Class name, `bl_idname`, class added/removed | Disable + re-enable the add-on |
| `bl_info` | Restart Blender |

Reload Scripts re-runs `register()` without a clean `unregister()`, so renamed or
deleted classes stay registered under their old names until a full toggle.

## Layout

```
blender_toolkit/
├── __init__.py       bl_info, reload block, register/unregister
├── preferences.py    module toggles
├── ui_pie_menu.py    Shift+Alt+Q pie, keymap registration
├── utils/            prefs(), ensure_mode()
└── tools/
    ├── __init__.py   registers submodules, panels follow the preferences
    └── <module>/     __init__.py + operators.py + ui.py
```

Every `__init__.py` holds imports and register/unregister loops only — no logic.
Panels are `bl_category = "Toolkit"`, `VIEW_3D` / `UI`. The pie fills slots in
W, E, S order, so each mode branch emits exactly two entries to keep the
exporter pinned to the bottom.
