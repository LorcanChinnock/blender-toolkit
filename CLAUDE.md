# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

`blender-toolkit` is a Blender add-on bundling repetitive game-asset workflows
into single operators: retopology setup, shapekey surgery, vertex group falloffs,
rigging helpers, FBX export. Five independent tool modules under
`blender_toolkit/tools/`, each toggleable from the add-on preferences.

See [README.md](README.md) for what each tool does and how to install it.

## Keep the add-on generic

The add-on is a general mesh/rig toolkit. It is not an avatar add-on, not a
character add-on, not a face add-on. Feature requests arrive phrased in whatever
workflow prompted them — "split the smile into left and right", "separate the
upper and lower lip" — and that framing is context for *you*, not vocabulary for
the code. Ship the general mechanism the example is an instance of.

Nothing domain-specific may reach operator ids, class names, labels, property
names, enum items, defaults, docstrings, comments, reports, the README, or the
tests. If a lip, a face, an eyelid, a garment, or an avatar appears in any of
those, the abstraction is wrong — go back a step and find the operation
underneath.

The falloff-groups operator is the worked example: the request was a smooth
lip split, the result is *axis + centre + band width writes two complementary
vertex groups*. No lip anywhere in it. Left/Right survive only as string
property **defaults**, overridable per invocation.

Signs the generic version is the right one: a second, unrelated use case fits
without a new branch; the docstring reads without naming a body part; the
properties describe geometry, not anatomy. If generalising would actually cost
real complexity, say so and let the user decide — do not silently hardcode the
specific case.

## Discuss a new feature before building it

New features start as a conversation, not an implementation. Before writing
code, settle two things with the user:

1. **Use cases — plural.** Ask what else the feature should cover beyond the
   example that prompted it. Two concrete cases are what reveal which parts are
   the mechanism and which are one workflow's specifics. One case cannot be
   generalised from; it can only be hardcoded.
2. **Placement and organisation.** Which existing tool module it belongs in, or
   whether it warrants a new one. Whether it extends an existing operator via
   properties or needs its own. Where it goes in the panel, whether it earns a
   pie slot (see the two-entry budget below), and what it does to the module
   toggles.

Use `AskUserQuestion` for the decisions that would change the shape of the code.
Do not skip this because the request seems obvious — the obvious reading is
usually the specific one.

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
  `register()`/`unregister()` loops over a local `classes` tuple. The single
  allowed exception is registration that has to *bracket* the class loop —
  `tools/weights/__init__.py` assigns and deletes `bpy.types.Scene.tk_gradient`
  and calls `overlay.disable()`, because a PointerProperty cannot exist before
  its PropertyGroup and a draw handler must not outlive the add-on.
- **Class naming:** `TK_OT_*` operators, `TK_PT_*` panels, `TK_MT_*` menus,
  `TK_PG_*` property groups, `TK_GGT_*` gizmo groups. Operator ids are
  `tk.<verb_noun>`.
- **Keep the maths out of the operator.** `tools/weights/gradient.py` imports no
  `bpy` at all, so the falloff shapes and profiles are tested directly. Anything
  with real logic in it should be factored the same way.
- **Every operator needs `poll()`.** It must fail closed in the wrong mode or
  with the wrong object type rather than throwing.
- **Panels:** `bl_space_type = 'VIEW_3D'`, `bl_region_type = 'UI'`,
  `bl_category = "Toolkit"`.
- **Every `__init__.py` loads its submodules through `utils.load_submodules`,**
  never a hand-written `if "bpy" in locals(): importlib.reload(x)` block. That
  block raises `NameError` for any submodule added since the session started,
  aborting the reload and leaving the add-on half updated — old classes still
  registered, new panel missing. The helper imports those fresh instead.
- **`utils` is the exception, and the master `__init__.py` reloads it by hand**
  before importing any name out of it. Reload Scripts re-executes that file, so
  `from .utils import ...` resolves against the session's cached copy — adding a
  helper to `utils` would raise `ImportError` on that line, before the loader
  that lives in `utils` ever runs. Do not "tidy" that `importlib.reload(utils)`
  away.
- **Reload order matters.** `ui_pie_menu` imports operator classes from `tools`,
  so `tools` comes first in the master `__init__.py`'s name list — otherwise a
  renamed operator raises `ImportError` on Reload Scripts. `load_submodules`
  preserves the order it is given. Anything importing across modules has to
  reload after its dependencies.
- **Panels follow the preferences.** `tools.refresh_panels()` registers and
  unregisters panel classes to match the toggles; it is called from each
  preference's `update=`. Do not gate panels with `poll()` returning False for a
  disabled module — the panel should be gone, not greyed.
- **Pie slots fill W, E, S, N, …** so each mode branch in the pie emits exactly
  two entries (padding with `pie.separator()`) to keep the exporter on the
  bottom slot.

## Adding a tool module

Create `tools/<name>/` with `__init__.py`, `operators.py`, `ui.py` mirroring an
existing module, then add the name to `MODULE_NAMES` in `tools/__init__.py` and a
matching `use_<name>` boolean in `preferences.py`. `MODULES` derives itself from
those two. Both `operators.py` and `ui.py` must expose a `classes` tuple.
`test_modules_wired` fails if a directory, a `MODULE_NAMES` entry and a
preference get out of step.

**A newly added module does not survive Reload Scripts** — the running session
has no name bound for it, so the reload loop imports it fresh instead of calling
`importlib.reload` on nothing. Registration still needs a full disable +
re-enable, because the reloaded module objects hold new class objects while the
old ones are what bpy has registered.

## Gotchas already paid for

- `bpy.ops.object.join_shapes` requires matching vertex counts. Applying a
  topology-changing modifier to a mesh with shapekeys silently corrupts them,
  which is why `TK_OT_apply_modifiers_shapekeys` refuses upfront via a depsgraph
  vertex-count check.
- `ShapeKey.vertex_group` holds **one** group and masks cannot stack. A split
  that assigns it looks right but cannot be chained: splitting an already-split
  key replaces the first mask instead of intersecting it, and the discarded half
  silently deforms at full strength. `TK_OT_split_shapekey` therefore bakes the
  weight into the coordinates — `co = relative_key.co + (source.co - relative_key.co) * w`
  — and leaves `vertex_group` empty.
- `VertexGroup.weight(index)` raises for vertices outside the group. Read
  memberships off the mesh instead: `{g.group: g.weight for g in vert.groups}`.
- BMesh elements die on a mode switch, same as `edit_bones`. Copy out indices
  and coordinates before leaving Edit mode, not the `BMVert`.
- `gpu.shader.from_builtin()` raises `SystemError` until the GPU module is
  initialised, which never happens in a plain `-b` Blender. Build shaders lazily
  inside the draw callback, never at import or `register()`, or the add-on
  becomes unusable headlessly. Tests can call `gpu.init()` — it works in
  background on 5.2 — which makes the draw callback smoke-testable.
- **RNA methods never appear via `hasattr` on the type.** `closest_point_on_mesh`,
  `Region.tag_redraw` and `UILayout.template_color_ramp` all report `False` and
  all exist. Check `SomeType.bl_rna.functions` instead, or call it on an
  instance. Three separate probes in this repo have been fooled by this.
- **Nearest-segment lookups need a bound, not a guess.** Taking the KDTree's
  nearest sample and testing only its own two segments is wrong by up to 0.2 on
  a path that folds back, and looks perfectly right on a sparse test. The bound
  that holds: if the nearest point on segment `i` is `d` away, sample `i` is
  within `d + length(i)`. Sweep `best + longest segment`. Also sort candidates
  and break ties toward the lowest index, or an exact tie makes the accelerated
  and brute-force paths disagree.
- Gizmo groups are **not** exposed as `bpy.types.<name>` the way panels are, and
  `bl_rna` survives `unregister_class`. To test registration use
  `bpy.types.GizmoGroup.bl_rna_get_subclass_py("TK_GGT_...")`, which is `None`
  both before registering and after unregistering.
- Writing weights costs ~30 ms per 65k verts with per-vertex `group.add()`, and
  the falloff maths ~16 ms. That is fast enough to recompute on every property
  change. Bucketing by quantised weight was measured at only 2.8× — not worth it.
- `edit_bones` references die on mode switch. Store bone *names* across a
  mode change, not the bones.
- Bone-name aliases in `PART_ALIASES` are order-sensitive: first match wins, so
  `forearm` precedes the bare `arm` fallback and Mixamo's `UpLeg` (thigh)
  precedes `Leg` (calf).
