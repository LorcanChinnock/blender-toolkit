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
  `tools/weights/__init__.py` assigns and deletes `bpy.types.Object.tk_gradient`
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
- **Per-vertex maths in Python does not scale, and numpy is bundled.** The
  falloff cost is per vertex *per segment*: 65k verts on a curved eight-handle
  path was 1.4 s scalar, 163 ms vectorised. A KDTree narrowing the candidate
  segments needed a careful correctness bound (nearest *sample* is not nearest
  *segment* — wrong by 0.2 on a path that folds back) and still lost; scanning
  every segment in numpy is faster *and* exactly right. It was deleted.
- **Vectorised numeric code must repeat the scalar version's arithmetic in the
  same order, not merely the same algebra.** `(co - a) - t*d` and
  `co - (a + t*d)` round differently, and on a path that folds back a vertex can
  sit exactly equidistant from three segments — so the last bit decides which
  wins and the two implementations disagree by a whole segment. Keep the scalar
  version as the tested reference and assert they match on a dense sweep.
- **Never hand a numpy array to a per-element Python loop.** Iterating one
  yields numpy scalars, whose arithmetic is an order of magnitude slower than a
  float's; `.tolist()` on a 65k array was worth most of a second per write.
- **Drawing is a read-only context.** A `draw_handler`, a panel's `draw()` and
  `GizmoGroup.refresh()` all raise `AttributeError: Writing to ID classes in
  this context is not allowed` on any ID write — including a CollectionProperty
  on the Scene. Anything that has to *watch* something with no update callback
  (a ColorRamp's `+`/`−` buttons are not even operators) belongs in a
  `bpy.app.timers` poll, which runs in a writable context. Note a timer has no
  screen, so `bpy.context.active_object` is `None` there — fall back to
  `context.view_layer.objects.active`.
- **`VertexGroup` cannot hold custom properties** — `bpy_struct[key] = val: id
  properties not supported for this type`. Per-group metadata has to live in a
  dict on the object keyed by group name, which orphans when the group is
  renamed outside the add-on.
- **A live mask must blend against a snapshot, not the current weights.** The
  session rewrites on every property change; `existing + (target - existing) *
  influence` read off the mesh feeds its own last result back in, so a
  half-masked vertex walks towards the full gradient one tweak at a time and the
  soft edge erodes. Blend against what the session found.
- Gizmo groups are **not** exposed as `bpy.types.<name>` the way panels are, and
  `bl_rna` survives `unregister_class`. To test registration use
  `bpy.types.GizmoGroup.bl_rna_get_subclass_py("TK_GGT_...")`, which is `None`
  both before registering and after unregistering.
- **A gizmo's target handler *is* its location — do not put the position in a
  matrix as well.** `matrix_basis`, `matrix_offset` and the `"offset"` target
  compose, so writing the world position into both puts the handle at twice its
  distance from the origin. Blender's own template says it outright:
  `scripts/templates_py/Gizmo/operator.py` has `matrix.col[3].xyz = co`
  commented out under "The location callback handles the location". That
  templates directory is the reference for anything gizmo-shaped — it is on disk
  and it is right, which beats reasoning about the C.
- **`move_3d` leaves a finished drag in `matrix_basis` *and* `matrix_offset`,
  both of which are added to the target's location.** So a setter that adjusts
  what it stores — snapping, clamping — leaves the disc where the cursor let go,
  sliding in the view plane while the data underneath it is the snapped point.
  Reset both to identity in `draw_prepare`, guarded on `Gizmo.is_modal` or you
  clear the drag in progress. **Not in `refresh`, which does not run per
  redraw** — that is why the stale handle only corrected itself when something
  else forced a refresh, such as dragging a different one.
- **`Gizmo.use_draw_modal` ("Show while dragging") defaults to False**, so a
  gizmo is not drawn at all during its own drag. Anything that has to stay
  visible mid-drag must be drawn by the add-on's own handler —
  `HANDLE_POINT_SIZE` exists for exactly that.
- **Snap in the gizmo setter, never in the timer.** `bpy.context.region_data` is
  only there in the setter's context; without it the snap falls back to
  nearest-in-3D, which yanks a handle to the far side of the mesh.
- **`obj.ray_cast` and `obj.closest_point_on_mesh` query *evaluated* geometry** —
  their docstrings say so — while the polygon index they return is only useful
  against `obj.data`. With a shape key or a deform modifier the two disagree, and
  a topology-changing modifier puts the index out of range entirely. Build a
  `BVHTree.FromPolygons` over the base coordinates instead; it has both
  `find_nearest` and `ray_cast`, and its indices are base-valid by construction.
- Writing weights costs ~28 ms per 65k verts with per-vertex `group.add()`.
  Bucketing by quantised weight was measured at only 2.8× — not worth it.
- **A property `update=` callback must not do the work.** A gizmo drag and a
  slider drag both fire one per mouse-move event. Set a flag and let the timer
  coalesce them — and give the commit path an explicit flush, or renaming a
  group and hitting Add inside one poll commits a group never written.
- **Arc position along a path must come from how the polyline was built, not
  from `path_factor`.** That searches for the nearest point on the *whole* path,
  so a path folding back reports an earlier stretch's position for a later
  handle — exactly wrong, and invisible on a path that does not fold.
  `handle_arc_positions` indexes the samples instead: stride 1 straight,
  `samples_per_gap` curved.
- **Nearest-point-on-a-path parameterisation is discontinuous, and smoothing
  cannot hide it.** On the concave side of a bend a point is equidistant from
  two stretches whose arc positions are far apart, so the weight jumps across
  that tie line - measured at 0.44 across a bracket of 2.2e-16, i.e. a real
  jump, not a steep gradient. `smooth()` diffuses it as 1/sqrt(passes): the
  maximum 20 passes took 0.50 down to only 0.07. Curved does not help either
  (0.52 -> 0.46, and worse on gentle bends). The fix is to let near-tied
  segments share the answer, with the share falling to zero at `BLEND_FRACTION`
  of the path length - continuous by construction, costs one extra projection
  pass, and streams so it never holds a segments-by-vertices array.
- **Invert must negate the weight, not mirror the position.** `profile(1 - t)`
  and `1 - profile(t)` agree only for symmetric profiles; Root and Sphere are
  not, so mirroring left a gradient and its inverse summing to something other
  than 1. Negating also means the stored handle weight and what the bar shows
  differ by a flip whenever Invert is on - see `flip()`, and note that negating
  reverses the stops' order along the bar.
- **Weight paint's ramp is a hue sweep, so it is one number.** Blue to red at
  full saturation passes through cyan, green and yellow — exactly Blender's
  weight colours — which makes `weight_colour`/`weight_of` an exact inverse pair
  through the hue. A `ColorRamp` can therefore *be* the weight scale, but only
  in `color_mode='HSV'` with `hue_interpolation='CCW'`; the default RGB mode
  runs blue to red through purple, which is no weight at all. Both are
  user-facing dropdowns on the widget, so both have to be re-enforced on poll.
- **A `ColorRamp` is capped at 32 elements** — `Unable to add element to
  colorband (limit 32)`. One handle per stop makes that the handle ceiling too,
  so the gizmo pool matches it rather than imposing a second limit.
- `edit_bones` references die on mode switch. Store bone *names* across a
  mode change, not the bones.
- Bone-name aliases in `PART_ALIASES` are order-sensitive: first match wins, so
  `forearm` precedes the bare `arm` fallback and Mixamo's `UpLeg` (thigh)
  precedes `Leg` (calf).
