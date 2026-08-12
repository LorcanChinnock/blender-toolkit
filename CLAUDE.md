# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

`blender-toolkit` is a Blender add-on bundling repetitive game-asset workflows
into single operators: retopology setup, mesh checks, shapekey surgery, vertex
group falloffs, rigging helpers, FBX export. Six independent tool modules under
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

## Feel Blender-native

**When a design decision is open, the option that matches how Blender itself
behaves wins.** It is the one the user already knows, so it costs no learning
and needs no explaining. This outranks tidiness, cleverness, and brevity when
they conflict, and it applies to refining requirements as much as to writing
code — if a proposed design has no counterpart anywhere in Blender, that is
evidence against the design, not a gap to fill with invention.

Novel mechanisms are allowed exactly where the feature has no precedent —
draggable 3D handles and a colour ramp used as a weight scale are the genuinely
new part of the gradient tool. Everything *around* the novel part must be
ordinary Blender: its lists, its operators, its panel idioms, its warnings, its
undo.

Look for the precedent before inventing. `scripts/templates_py/` and Blender's
own `scripts/startup/bl_ui/` are on disk and are the reference; read them rather
than reasoning about what Blender "probably" does. Worked examples already used
here: a modifier's **Apply**, `mesh.select_all`'s action enum for one operator
with several modes, the F9 redo panel, `template_list` over real data,
`object.vertex_group_add` rather than a bespoke add button.

Two patterns Blender does not have, so neither do we:

- **A modal "this changed — choose one" dialog.** Blender states the situation
  in a static panel line ("Applying modifier will delete shape keys") and lets
  regeneration win; F9 redo and hair-particle edit both discard hand work with no
  prompt at all. Ctrl+Z is the undo, and an explicit Apply is how the user claims
  generated data.
- **A second list shadowing one Blender already keeps.** If the add-on's data is
  per-vertex-group, per-bone or per-modifier, it is an *attribute* of that thing,
  looked up from Blender's own list — never a parallel list the user has to map
  across. That mistake is what the gradient tool's own history is a record of.

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
  `tools/weights/__init__.py` assigns and deletes the two
  `bpy.types.Object.tk_gradient*` properties and calls `overlay.enable()` /
  `overlay.disable()`, because a CollectionProperty cannot exist before its
  PropertyGroup and a draw handler must neither predate nor outlive the add-on.
- **Class naming:** `TK_OT_*` operators, `TK_PT_*` panels, `TK_MT_*` menus,
  `TK_PG_*` property groups, `TK_GGT_*` gizmo groups, `TK_GT_*` gizmos,
  `TK_UL_*` UI lists. Operator ids are `tk.<verb_noun>`.
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
  properties not supported for this type`. That is why a gradient stores the
  group *name* and lives in `Object.tk_gradients` rather than on the group.
- **There is no vertex-group rename hook, so a rename is *inferred*.**
  `purge_orphans()` follows one when the name is gone, the group count has not
  moved since the last look, and the group at the remembered `group_index` has no
  gradient of its own; anything else - deleted, reordered - drops the gradient
  rather than adopting the wrong group. The snapshot is refreshed by
  `remember_groups()` after every purge, which is why the overlay timer runs
  whether or not the handles are showing. It is a write, so it never runs from
  `draw`.
- **The draw handler and the write timer run for the add-on's lifetime.**
  `overlay.enable()` is called from `weights/__init__.py`'s `register()` and
  `disable()` from `unregister()`; nothing switches them mid-session. They cost
  nothing when `showing()` is False, and a file opened with gradients already in
  it has nobody to switch them on. There was a Show Handles toggle gating this
  once — it meant nothing, because nobody opens a gradient's panel in order not
  to see it, and while it was off a changed setting marked the gradient dirty and
  then never got written.
- **`template_color_ramp`'s `expand` does not hide anything.** It changes the
  widget's layout, not which controls appear - the colour-mode and interpolation
  dropdowns, the swatch and `Pos` are all drawn either way. A panel section of
  per-handle weight sliders was once added on the belief that `expand=False`
  removed `Pos`; it was a second editor for numbers the bar already edits, and it
  was deleted. The ramp is the only weight editor. `normalise_ramp` overwriting
  the two dropdowns every poll is the price, and it is cheap.
- **`lock_weight` stops Blender's paint tools, not the API.** `group.add()`
  writes straight through a locked group - probed. Anything writing on a timer
  has to check the flag itself, or the lock in the vertex group list means
  nothing. The live rewrite skips a locked group; an explicit operator the user
  aimed at that group (Remove's restore) does not.
- **A live mask must blend against a baseline, not the current weights.** The
  gradient rewrites on every property change; `existing + (target - existing) *
  influence` read off the mesh feeds its own last result back in, so a
  half-masked vertex walks towards the full gradient one tweak at a time and the
  soft edge erodes. It has to be captured when the gradient *adopts* the group —
  capture it lazily on first write and the gradient has already overwritten what
  it was meant to record.
- **The baseline is an ID property array on the gradient, not a backup vertex
  group.** A `tk.backup.<group>` group worked and was visible: a junk row in the
  user's own vertex group list, in every modifier's group dropdown and in the
  export, none of which an add-on can filter. `settings["baseline"]` is a real
  datablock write, so it survives undo, a save and a Reload Scripts exactly as
  the group did — the original objection was to a *module-level dict*, which none
  of those survive. It also dies with the gradient and needs no name tracked
  through a rename. `NOT_A_MEMBER = -1.0` encodes "was not in the group" because
  weights are 0..1; without it every vertex comes back a member weighing zero.
  `baseline_of` refuses an array whose length no longer matches the mesh.
- **A `bpy.types.Operator` cannot hold ID properties** — `this type doesn't
  support IDProperties`. The gradient PropertyGroup can, which is why
  `write_weights` takes an explicit `baseline=` for the one-shot operator and
  only consults the stored one for a real gradient.
- **A gradient is an attribute of a vertex group, not an item in a list of its
  own.** Two dead ends are recorded here. First a *session* — Start, Add, Finish,
  Cancel, with backup groups and a saved-record dict to make a live destructive
  write reversible: three peer buttons for two operations, and a committed
  gradient could only be reached again by typing its group name into a search
  field. Then a `CollectionProperty` with its own `UIList`, which deleted the
  transaction but shadowed Blender's vertex group list — two lists, one
  selection, and nothing saying which was the master. What works is neither: the
  panel draws `obj.vertex_groups` and `active_gradient()` looks the gradient up
  by the active group's name. Do not reintroduce a commit step, and do not
  reintroduce a second list.
- **Painting on the group is the only exit, and it is also Apply.** There were
  Reset, Remove and Apply buttons; all three went. Reset is Ctrl+Z with extra
  steps, and the other two collapse into one act: a brush stroke on a
  gradient-driven group detaches the gradient and leaves the weights exactly as
  they stand. The precedent is Blender's redo panel, which closes the moment you
  do anything else. Nothing in the module deletes a vertex group — the weights
  are the work.
- **Detaching cannot be inferred from the depsgraph alone.** A posed armature, a
  shape key slider and a modifier tweak all update the mesh, and detaching on any
  of those would be a disaster on a rigged character. `_on_depsgraph` raises a
  *suspicion*; the timer confirms it with `hand_painted()`, which compares the
  group against `settings["written"]` — the values the gradient actually wrote.
  The confirm is skipped while `pending()`, so a drag (which rewrites every poll)
  never pays for the full read.
- **`blend` composes against the baseline, not the current weights**, exactly as
  the mask does and for the same reason. That is what makes the mode reversible
  with no Remove button: the originals are still in the baseline, so switching
  back to Replace and out again recomputes rather than compounding. The mode
  names and set are Blender's Vertex Weight Mix ones.
- **Do not subclass a registered Blender UI class.** A `TK_UL_vgroups(MESH_UL_vgroups)`
  looked obvious and registers fine — but *unregistering* it makes Blender
  regenerate the parent's RNA subtype and raise `metaclass conflict: the
  metaclass of a derived class must be a (non-strict) subclass of the
  metaclasses of all its bases`, which aborts the loop and leaves the add-on
  half unregistered. Copy the handful of lines from `scripts/startup/bl_ui/`
  instead. (That list is gone now — see the next entry — but the trap stands.)
- **The panel draws no vertex group list at all.** Object Data Properties
  already has one; a copy in the N-panel is a second thing to keep in step, and
  the whole point of dropping the gradient list was to stop shadowing lists
  Blender already keeps. The panel names the active group and works on it. The
  cost, accepted: nothing marks which groups are gradient-driven, because
  Blender's own list is not ours to draw into.
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
- **A built-in gizmo that owns its drag cannot be made to snap.** `move_3d`
  accumulates the mouse delta into `matrix_basis`/`matrix_offset`, both of which
  are *added* to whatever the target handler reports, and it never re-reads the
  target while modal. A setter that adjusts what it stores therefore moves the
  data and not the disc: the handle slides in the view plane while the path
  underneath it snaps, and it only corrects itself when something else forces a
  refresh — such as dragging a *different* handle. No amount of resetting the
  matrices in `draw_prepare` fixes that; `TK_GT_gradient_handle` is a custom
  `Gizmo` with its own `invoke`/`modal`/`exit`, no target handler, and
  `matrix_basis` rebuilt from the stored point every redraw.
- **`Gizmo.use_draw_modal` ("Show while dragging") defaults to False**, so a
  gizmo is not drawn at all during its own drag. Set it, rather than drawing a
  stand-in marker from the add-on's own handler.
- **Position a custom gizmo's modal from `event.mouse_region_x/y`, not from a
  delta** — that is the whole point of owning the modal, and it is the only way
  to get a true cursor ray (`view3d_utils.region_2d_to_origin_3d` /
  `region_2d_to_vector_3d`). It also rules out `use_grab_cursor`, which wraps
  the pointer at the region edge and would teleport an absolute position.
- **Never snap by nearest-point-in-3D.** A handle dragged past the surface is
  still under the cursor on the near side; `closest_point_on_mesh` answers with
  the far side. Ray only, and a ray that misses returns the caller's fallback
  rather than moving the handle at all.
- **The cursor points at *evaluated* geometry, but the gradient measures base
  coordinates.** `snapping` builds its BVH over
  `obj.evaluated_get(depsgraph).to_mesh()` and carries the hit back with
  `mathutils.geometry.barycentric_transform` over the hit triangle's three
  vertices — exact for any stack that preserves vertex order (shape keys,
  armature, lattice). When the counts disagree (subsurf, mirror) there is no
  mapping, and the evaluated point stands in.
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
- **`BMEdge.is_contiguous` is the built-in winding test** — "manifold, between
  two faces with the same winding". Comparing the two loops' start vertices by
  hand computes exactly the same thing; `tools/mesh/checks.py` uses the
  property. It is False on a boundary edge too, so it needs an `is_manifold`
  guard or every open edge reads as flipped.
- **`is_manifold` is not the non-manifold check a retopo tool wants.** Blender
  counts a one-face boundary edge as non-manifold, and a retopo shell in
  progress is nearly all boundary — the report drowns. `checks.non_manifold`
  looks for `len(edge.link_faces) > 2` instead and leaves wire edges to the
  loose-geometry check.
- `edit_bones` references die on mode switch. Store bone *names* across a
  mode change, not the bones.
- Bone-name aliases in `PART_ALIASES` are order-sensitive: first match wins, so
  `forearm` precedes the bare `arm` fallback and Mixamo's `UpLeg` (thigh)
  precedes `Leg` (calf).
