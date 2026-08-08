# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

`blender-toolkit` is a Blender add-on bundling repetitive game-asset workflows
into single operators: retopology setup, shapekey surgery, rigging helpers, FBX
export. Four independent tool modules under `blender_toolkit/tools/`, each
toggleable from the add-on preferences.

See [README.md](README.md) for what each tool does and how to install it.

## Target version

**Blender 5.2 LTS only.** Installed at `/Applications/Blender.app`.

The add-on already uses 5.x-only API and will not run on 3.6/4.x. Do not add
compatibility shims for older versions unless asked.

## Verify API against the docs and the local build — do not work from memory

The bpy API changes across releases and training data lags it. Two rules:

1. **Reference the official docs, pinned to the target version:**
   <https://docs.blender.org/api/5.2/> — e.g.
   `https://docs.blender.org/api/5.2/bpy.types.ToolSettings.html`.
   Use the `5.2` path, not `current`, which drifts to newer releases.

2. **Confirm against the installed build before writing code that depends on
   it.** A headless probe costs seconds and beats guessing:

   ```bash
   /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python-expr '
   import bpy
   print(hasattr(bpy.context.scene.tool_settings, "use_snap_project"))
   print([i.identifier for i in bpy.types.ToolSettings.bl_rna.properties["snap_elements_individual"].enum_items])
   '
   ```

   `bl_rna.properties` enumerates real property names and enum values;
   `op.get_rna_type().properties` does the same for operator arguments.

This is not hypothetical. `tool_settings.use_snap_project` does not exist in
5.2, and setting `snap_elements_individual` *clears* `snap_elements_base` —
neither is guessable, both are one probe away.

## Testing

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python tests/smoke.py
```

Runs every operator end to end, prints `PASS`/`FAIL` per tool, exits non-zero on
failure. Run it after any change to operator logic.

Notes:
- `bpy.ops.*` raises `RuntimeError` when an operator reports `{'ERROR'}` — it
  does not return `{'CANCELLED'}`. Use the `raises()` helper in the test file.
- The pie menu cannot be tested headlessly: background Blender has no addon
  keyconfig, so `keyconfigs.addon` is `None` and no keymap is registered.
- Pure helpers (`_part`, `_side`, `missing_humanoid_bones`) are tested directly
  rather than through operators. Prefer that where logic can be factored out.

## Architecture rules

These are deliberate. Keep them.

- **No logic in any `__init__.py`.** They hold `bl_info`, imports, and
  `register()`/`unregister()` loops over a local `classes` tuple. Nothing else.
- **Class naming:** `TK_OT_*` operators, `TK_PT_*` panels, `TK_MT_*` menus.
  Operator ids are `tk.<verb_noun>`.
- **Every operator needs `poll()`.** It must fail closed in the wrong mode or
  with the wrong object type rather than throwing.
- **Panels:** `bl_space_type = 'VIEW_3D'`, `bl_region_type = 'UI'`,
  `bl_category = "Toolkit"`.
- **Reload order matters.** `ui_pie_menu` imports operator classes from `tools`,
  so `tools` must be reloaded first in the master `__init__.py` — otherwise a
  renamed operator raises `ImportError` on Reload Scripts. Anything importing
  across modules has to reload after its dependencies.
- **Panels follow the preferences.** `tools.refresh_panels()` registers and
  unregisters panel classes to match the toggles; it is called from each
  preference's `update=`. Do not gate panels with `poll()` returning False for a
  disabled module — the panel should be gone, not greyed.
- **Pie slots fill W, E, S, N, …** so each mode branch in the pie emits exactly
  two entries (padding with `pie.separator()`) to keep the exporter on the
  bottom slot.

## Adding a tool module

Create `tools/<name>/` with `__init__.py`, `operators.py`, `ui.py` mirroring an
existing module, then add it to `MODULES` in `tools/__init__.py` and a matching
`use_<name>` boolean in `preferences.py`. Both `operators.py` and `ui.py` must
expose a `classes` tuple.

## Gotchas already paid for

- `bpy.ops.object.join_shapes` requires matching vertex counts. Applying a
  topology-changing modifier to a mesh with shapekeys silently corrupts them,
  which is why `TK_OT_apply_modifiers_shapekeys` refuses upfront via a depsgraph
  vertex-count check.
- `ShapeKey.vertex_group` masks a key — L/R splitting needs no per-vertex math.
- `edit_bones` references die on mode switch. Store bone *names* across a
  mode change, not the bones.
- Bone-name aliases in `PART_ALIASES` are order-sensitive: first match wins, so
  `forearm` precedes the bare `arm` fallback and Mixamo's `UpLeg` (thigh)
  precedes `Leg` (calf).
