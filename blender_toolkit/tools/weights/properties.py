import bpy
from mathutils import Vector

from . import gradient

RAMP_NAME = "TK Weight Gradient"

# What a group held before a gradient adopted it, kept as an ID property array
# on the gradient itself - a real datablock write, so it survives undo, a save
# and a Reload Scripts the way a module-level dict never could.
#
# It used to be a `tk.backup.<group>` vertex group, which worked but put a junk
# row in the user's own vertex group list, in every modifier's group dropdown
# and in the export - none of which the add-on can filter. On the gradient it is
# invisible, it dies with the gradient, and it needs no name to be tracked
# through a rename.
BASELINE_KEY = "baseline"

# The weights the gradient last wrote, so a hand-painted stroke can be told from
# any other reason the mesh updated.
WRITTEN_KEY = "written"

# Weights are 0..1, so a negative one is free to mean "this vertex was not in
# the group at all" - which has to round-trip as itself rather than as zero, or
# restoring makes every vertex a member weighing nothing.
NOT_A_MEMBER = -1.0

# Blender's own floor is one stop; a gradient needs two ends to run between.
MIN_STOPS = 2

# Where each vertex sits in the 0..1 field, and what it was computed for. This is
# the expensive half, and everything the panel changes except the handles and the
# shape reuses it. Measured on 65k verts: rebuilding is 150-500ms depending on
# how many segments the path has, reusing is ~110ms whatever it has.
_factor_cache = (None, None)

# Set by anything that changes the result, cleared by the session's timer when it
# writes. Dragging a gizmo or a slider fires an update per mouse-move event, and
# a full write per event is a slideshow; this collapses a drag into at most one
# write per poll.
_dirty = False

# True while write_weights is running, so the depsgraph updates it causes are not
# mistaken for the user editing the mesh.
_writing = False

# True while a gradient is being pointed at a group it has just been renamed
# into, so the rename callback - which exists to *move* a gradient - stays out
# of the way.
_healing = False


def ensure_ramp(settings):
    """The texture datablock hosting the value ramp, created on first use.

    A ColorRamp cannot exist on its own, so it rides on a BLEND texture. It is
    the gradient control because its widget has real add and remove buttons -
    that is how you add a stop, and therefore a handle. The PointerProperty
    holds a user, so it saves with the file.
    """
    if settings.ramp is None:
        texture = bpy.data.textures.new(RAMP_NAME, type='BLEND')
        texture.use_color_ramp = True
        settings.ramp = texture
        reset_ramp(settings)
    return settings.ramp


def ramp_of(settings):
    return settings.ramp.color_ramp if settings.ramp is not None else None


def normalise_ramp(settings):
    """Hold the ramp to what it actually represents. True when it changed.

    Three things it enforces, on every poll so an edit made through Blender's own
    widget is picked up without a callback the datablock does not offer:

    - **At least two stops.** Blender is happy with one; a gradient is not.
    - **HSV, counter-clockwise.** Weight paint's ramp is a hue sweep, and that is
      not a straight line in RGB - interpolating blue to red the ordinary way
      goes through purple. In HSV/CCW the same two stops pass through cyan, green
      and yellow, matching weight paint exactly, so the widget *is* the legend.
    - **Colour follows position.** A stop's position *is* its handle's weight, so
      its colour is simply the scale's colour there. That is what makes the
      picker inert: Blender's widget still opens one, but whatever is chosen is
      overwritten on the next poll.
    """
    ramp = ramp_of(settings)
    if ramp is None:
        return False

    changed = False
    if ramp.color_mode != 'HSV':
        ramp.color_mode = 'HSV'
        changed = True
    if ramp.hue_interpolation != 'CCW':
        ramp.hue_interpolation = 'CCW'
        changed = True

    while len(ramp.elements) < MIN_STOPS:
        # At the far end from the survivor, so the two are never stacked.
        survivor = ramp.elements[0].position
        ramp.elements.new(0.0 if survivor > 0.5 else 1.0)
        changed = True

    for element in ramp.elements:
        scale = gradient.weight_colour(element.position)
        if max(abs(a - b) for a, b in zip(element.color, scale)) > 1e-6:
            element.color = scale
            changed = True
    return changed


def handle_arcs(settings, points=None):
    """Where each handle sits along the path, 0..1."""
    return gradient.handle_arc_positions(
        points if points is not None else path_of(settings),
        len(settings.handles),
        settings.curved,
    )


def ramp_curve(settings, points=None):
    """Value mapping from the handles, or None to fall back to the profile.

    Not read off the ramp any more. A stop's position is its handle's weight, so
    the curve is a knot per handle: where it sits along the path, and what it
    weighs. The ramp is where the weight is *edited*, not where it is stored.

    The profile rides along as the easing between one knot and the next, which
    is the only place left for it to mean anything: the handles decide the
    weights, so all that is left to choose is how the weight travels between
    them.
    """
    if len(settings.handles) < MIN_STOPS:
        return None
    # None for Linear rather than the identity lambda: weight_curve skips the
    # call entirely, and it is one per vertex on the default profile.
    profile = settings.profile
    return gradient.weight_curve(
        zip(handle_arcs(settings, points), (h.weight for h in settings.handles)),
        ease=None if profile == 'LINEAR' else gradient.PROFILE_CURVES[profile],
    )


def reset_ramp(settings):
    """Back to a straight weight-zero to weight-one ramp with two stops."""
    ramp = ramp_of(settings)
    if ramp is None:
        return
    ramp.color_mode = 'HSV'
    ramp.hue_interpolation = 'CCW'
    # One at a time, re-fetched: removing an element invalidates references to
    # the others, so a cached list of them goes stale mid-loop.
    while len(ramp.elements) > MIN_STOPS:
        ramp.elements.remove(ramp.elements[1])
    # Position is the weight now, so the ends of the bar are weights 0 and 1.
    ramp.elements[0].position = 0.0
    ramp.elements[0].color = gradient.weight_colour(0.0)
    ramp.elements[-1].position = 1.0
    ramp.elements[-1].color = gradient.weight_colour(1.0)


def ramp_signature(settings):
    """What the ramp currently says, cheap enough to compare every poll.

    The ColorRamp has no update callback and its add/remove buttons are not even
    operators, so there is nothing to subscribe to - a change is noticed by
    comparing this against the last one.
    """
    ramp = ramp_of(settings)
    if ramp is None:
        return ()
    return tuple(sorted(e.position for e in ramp.elements))


def flip(settings, weight):
    """A weight as it reads on the bar, or back again. Its own inverse.

    A handle stores the weight the *un-inverted* gradient reaches there, and
    Invert negates the result rather than mirroring the path. So what the bar
    shows, what the mesh gets and what the handle is coloured are all the flip
    of what is stored, and flipping twice is the identity.
    """
    return 1.0 - weight if settings.invert else weight


def stops_in_handle_order(settings, stops):
    """Sorted stop positions lined up with the handles they belong to.

    Inverting negates every weight, which reverses their order along the bar -
    so the first handle then owns the *last* stop.
    """
    return list(reversed(stops)) if settings.invert else list(stops)


def mirror_weights_to_ramp(settings):
    """Move the stops to where the handles' weights now read on the bar. True
    when any of them had to move.

    The bar is usually the input and the handles the output. Two things push the
    other way: Invert, which changes what a stored weight *means* without
    touching it, and a weight typed straight into the handle list.
    """
    ramp = ramp_of(settings)
    if ramp is None or not settings.handles:
        return False
    shown = sorted(flip(settings, h.weight) for h in settings.handles)
    elements = sorted(ramp.elements, key=lambda e: e.position)
    if len(elements) != len(shown):
        return False

    changed = False
    for element, weight in zip(elements, shown):
        if abs(element.position - weight) > 1e-6:
            element.position = weight
            element.color = gradient.weight_colour(weight)
            changed = True
    return changed


def handle_values(settings):
    """The weight the gradient produces at each handle.

    The handle's own weight as the bar shows it - the stop it owns sits at
    exactly that place on the scale. This is what the mesh reads there, which is
    what lets a handle be drawn in the colour the surface under it will be.
    """
    return [flip(settings, h.weight) for h in settings.handles]


def handle_colours(settings):
    """Weight paint's colour for each handle's own weight."""
    return [gradient.weight_colour(v) for v in handle_values(settings)]


def _seed_between(handles, index):
    """Where a handle inserted at `index` should start, in object space.

    Midway between the neighbours it lands between, because the reason to add
    one is almost always to bend the path where it currently runs straight. A
    stop's position is a weight now, so there is no place along the path in it
    to seed from.
    """
    if not handles:
        return Vector((0.0, 0.0, 0.0))
    before = Vector(handles[min(max(index - 1, 0), len(handles) - 1)][1])
    after = Vector(handles[min(index, len(handles) - 1)][1])
    if before == after:  # past an end: carry on the way the path was going
        other = Vector(handles[max(len(handles) - 2, 0)][1])
        return after + (after - other) * 0.5
    return (before + after) * 0.5


def sync_handles_to_ramp(settings):
    """One handle per ramp stop. True when the handles changed.

    The gradient is the single control for how many control points there are:
    its `+` and `-` are how you add a stop, and a handle appears with it. One
    widget, not two - a separate handle list is a second place to manage the
    same thing.

    **A stop's position is its handle's weight.** Dragging it along the bar reads
    a weight off the scale; it moves nothing in the viewport. Where the handle
    falls along the path is the handle's own business, dragged there in 3D.

    Stops are kept in order by the widget and handles are matched to them in path
    order, so weights run monotonically along the path - dragging one stop past
    another swaps which handle owns which weight.
    """
    ramp = ramp_of(settings)
    if ramp is None or len(settings.handles) == 0:
        return False

    stops = stops_in_handle_order(settings, sorted(e.position for e in ramp.elements))
    if len(stops) == len(settings.handles):
        # Where the user's drag landed. Nothing else writes these - the bar is
        # the one weight editor, which is why there is no second list of the
        # same numbers to keep in step with it.
        for handle, stop in zip(settings.handles, stops):
            handle.weight = flip(settings, stop)
        return False

    # Matched in what the bar shows, so the comparison is like for like.
    existing = [
        (flip(settings, h.weight), tuple(h.position)) for h in settings.handles
    ]
    taken = set()
    rebuilt = []
    for stop in stops:
        match = next(
            (
                index
                for index, (weight, _position) in enumerate(existing)
                if index not in taken and abs(weight - stop) < 1e-6
            ),
            None,
        )
        if match is None:
            rebuilt.append((stop, tuple(_seed_between(existing, len(rebuilt)))))
        else:
            taken.add(match)
            rebuilt.append((stop, existing[match][1]))

    settings.handles.clear()
    for stop, position in rebuilt:
        handle = settings.handles.add()
        handle.weight = flip(settings, stop)
        handle.position = position
    return True


def path_of(settings):
    """The polyline the gradient runs along, in object space."""
    return gradient.path_points(
        [h.position for h in settings.handles], settings.curved
    )


def _raw_factors(obj, settings, points):
    """Where every vertex sits in the 0..1 field, cached against what defines it.

    ponytail: the key is the path, the shape and the vertex count, so editing the
    mesh itself mid-session goes unnoticed. A session lives in weight paint where
    that cannot happen; leaving edit mode ends it anyway.
    """
    global _factor_cache
    mesh = obj.data
    key = (
        obj.name, len(mesh.vertices), settings.shape,
        tuple(tuple(p) for p in points),
    )
    if key == _factor_cache[0]:
        return _factor_cache[1]

    import numpy as np

    coords = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", coords)  # one C call, not 65k attribute reads
    # .tolist(), not the array: the value stage walks this one element at a time,
    # and iterating a numpy array yields numpy scalars whose arithmetic is an
    # order of magnitude slower than a plain float's. Measured at 65k verts, that
    # single conversion is most of a second.
    raw = gradient.raw_factors(
        coords, points, settings.shape, gradient.segment_lengths(points)
    ).tolist()
    _factor_cache = (key, raw)
    return raw


def mark_dirty():
    """The result changed and the session's timer should write it."""
    global _dirty
    _dirty = True


def _mark_dirty(self, context):
    """`update=` form. Blender wants exactly two arguments, so no *args."""
    mark_dirty()


def pending():
    """Is a write already queued? Then nothing else needs to look for changes."""
    return _dirty


def take_dirty():
    global _dirty
    was, _dirty = _dirty, False
    return was


def flush(context, settings):
    """Do the pending write now, if there is one.

    The session's timer is the normal path, and deliberately lazy - it is what
    stops a drag writing once per mouse-move event. But a commit cannot wait for
    it: renaming a group and hitting Add inside the same poll would otherwise
    save a record for a group that had not been written yet.
    """
    if take_dirty():
        write_weights(context, settings)


def writing():
    return _writing


def write_weights(context, settings, points=None, curve=None, baseline=None):
    """Recompute and write both groups. Called live from every setting's update.

    `points` and `curve` are passed in rather than read off `settings`: the
    one-shot operator has two plain vectors and no ramp, the session has a
    handle collection and one. Everything else is duck-typed across both.
    """
    # The view layer is the fallback because active_object is screen-derived and
    # a timer callback has no screen, so it reads None from overlay._sync.
    obj = context.active_object or context.view_layer.objects.active
    if obj is None or obj.type != 'MESH':
        return

    mesh = obj.data
    if points is None:
        points = path_of(settings)
        curve = ramp_curve(settings, points)
    if len(points) < 2:
        return

    try:
        raw = _raw_factors(obj, settings, points)
    except ValueError:  # handles coincident mid-drag; keep the last good weights
        return

    # A curve wins over both of these, and the session settings do not carry a
    # midpoint at all - it is the scripting operator, which cannot hold a
    # ColorRamp, that still needs one.
    midpoint = getattr(settings, "midpoint", 0.5)
    weights = {
        index: gradient.value(
            t,
            profile=settings.profile,
            midpoint=midpoint,
            invert=settings.invert,
            curve=curve,
        )
        for index, t in enumerate(raw)
    }

    if settings.smooth_repeat:
        weights = gradient.smooth(
            weights, [tuple(e.vertices) for e in mesh.edges], settings.smooth_repeat
        )

    name = settings.group_name
    if not name:
        return
    group = obj.vertex_groups.get(name)
    if group is None:
        group = obj.vertex_groups.new(name=name)
        # Remembered so removing the gradient can take the group with it. A
        # group that was already there is the user's, and stays.
        if hasattr(settings, "created_group"):
            settings.created_group = True
    elif group.lock_weight:
        # `lock_weight` stops Blender's paint tools, not the API - group.add()
        # writes straight through it - so a live gradient has to check it or the
        # lock means nothing against the one thing writing every 150 ms.
        return
    # Weight paint draws the active group, so the one being written is the one
    # that should be on screen - including straight after a rename.
    obj.vertex_groups.active = group

    mask = obj.vertex_groups.get(settings.mask_group) if settings.mask_group else None
    # What the blend composes with, and what the mask blends *towards*, is the
    # group as the gradient first found it - never as it stands. The gradient
    # rewrites on every property change, and reading its own last result back in
    # walks a half-masked vertex towards the full gradient one tweak at a time
    # until the soft edge has eroded away.
    #
    # The one-shot operator passes its own and carries no stored one - an
    # Operator raises "this type doesn't support IDProperties" if asked.
    if baseline is None and hasattr(settings, "created_group"):
        baseline = baseline_of(settings, len(mesh.vertices))
    mode = getattr(settings, "blend", 'REPLACE')
    per_vertex = _memberships(obj) if mask is not None else None

    written = [0.0] * len(mesh.vertices)
    global _writing
    _writing = True
    try:
        for index, weight in weights.items():
            existing = 0.0 if baseline is None else max(baseline[index], 0.0)
            value = weight if mode == 'REPLACE' else gradient.blend(
                mode, existing, weight
            )
            if mask is not None:
                # Outside the mask what the group already held stands, so a
                # region can be protected; a soft edge blends old into new.
                influence = per_vertex[index].get(mask.index, 0.0)
                value = existing + (value - existing) * influence
            # ponytail: per-vertex add, measured 28ms/65k verts - about half of
            # what a cached rewrite now costs. Worth revisiting with
            # foreach_set only if that half starts to matter.
            group.add([index], value, 'REPLACE')
            written[index] = value
    finally:
        _writing = False

    # What the group should read if nobody else has touched it. Painting on it
    # is what detaches the gradient, and this is how that is told apart from a
    # posed armature or a shape key slider, both of which also update the mesh.
    if hasattr(settings, "created_group"):
        settings[WRITTEN_KEY] = written


# Everything that decides what a gradient produces, which is exactly what a new
# gradient copies from the one it was made from. The target group is not in it -
# two gradients writing one group would fight - and neither is the path, which
# is copied separately because it is a collection rather than a value.
COPIED = (
    "shape", "profile", "invert", "smooth_repeat", "curved", "mask_group",
    "blend",
)


def copy_gradient(source, target):
    """Everything but the target group: a new gradient starts as a variation.

    The reason for a second gradient is nearly always the first one again with
    one thing changed - inverted, masked, aimed at a different group - so
    starting from a copy is fewer clicks than rebuilding it, and Reset is one
    button away.
    """
    for name in COPIED:
        setattr(target, name, getattr(source, name))
    set_handles(target, [tuple(h.position) for h in source.handles])
    for handle, original in zip(target.handles, source.handles):
        handle.weight = original.weight

    ensure_ramp(target)
    ramp, original = ramp_of(target), ramp_of(source)
    while len(ramp.elements) > MIN_STOPS:
        ramp.elements.remove(ramp.elements[1])
    for index, stop in enumerate(sorted(e.position for e in original.elements)):
        element = (
            ramp.elements[index] if index < len(ramp.elements)
            else ramp.elements.new(stop)
        )
        element.position = stop
        element.color = gradient.weight_colour(stop)


def spread_weights(settings):
    """Weights evenly from one end of the scale to the other, in path order.

    Stored raw, not flipped: Invert negates on the way out, so writing it this
    way is what keeps an inverted gradient running the other way.
    """
    last = max(len(settings.handles) - 1, 1)
    for index, handle in enumerate(settings.handles):
        handle.weight = index / last


def set_handles(settings, points):
    """Replace the path with the given points, weights spread evenly."""
    settings.handles.clear()
    for point in points:
        settings.handles.add().position = point
    spread_weights(settings)


def unused_group_name(obj, base="Group"):
    """A group name nothing is using yet, numbered Blender's way."""
    if base not in obj.vertex_groups:
        return base
    index = 1
    while f"{base}.{index:03d}" in obj.vertex_groups:
        index += 1
    return f"{base}.{index:03d}"


def gradients_of(obj):
    """Every gradient on `obj`, or an empty tuple for a non-mesh."""
    return getattr(obj, "tk_gradients", ())


def gradient_for(obj, name):
    """The gradient writing vertex group `name`, or None.

    Looked up by name because a gradient is an *attribute of a vertex group*,
    not a thing with its own list. There was a second list here once, shadowing
    Blender's own, and nothing said which one was the master.
    """
    return next(
        (entry for entry in gradients_of(obj) if entry.group_name == name), None
    )


def active_gradient(obj):
    """The gradient on the active vertex group, or None.

    Selecting a vertex group *is* selecting a gradient. The two were already
    coupled - write_weights makes the group it writes the active one - so this
    only stops pretending they were separate.
    """
    groups = getattr(obj, "vertex_groups", None)
    active = groups.active if groups is not None else None
    return gradient_for(obj, active.name) if active is not None else None


def remember_groups(obj):
    """Snapshot where each gradient's group sits, for `purge_orphans` to use.

    Taken after every purge rather than before, so what is stored is always the
    arrangement as it stood the last time everything still lined up.
    """
    count = len(obj.vertex_groups)
    for entry in obj.tk_gradients:
        group = obj.vertex_groups.get(entry.group_name)
        if group is not None:
            entry.group_index = group.index
            entry.group_count = count


def _heal_rename(obj, entry):
    """Follow a gradient's group through a rename in Blender's own list. True
    when it was followed.

    Blender gives add-ons no rename hook, so a rename can only be *inferred*.
    The evidence: the name is gone, the number of groups has not changed since
    the last look, and the group still sitting at the remembered index has no
    gradient of its own. Deleting a group changes the count, so that falls
    through to a purge instead of adopting whichever group shuffled into place.
    """
    global _healing
    if entry.group_count != len(obj.vertex_groups):
        return False
    if not 0 <= entry.group_index < len(obj.vertex_groups):
        return False
    group = obj.vertex_groups[entry.group_index]
    if gradient_for(obj, group.name) is not None:
        return False

    # Straight across, with the rename callback held off: that callback exists
    # to *move* a gradient to another group, which is the opposite of following
    # one that has moved by itself.
    _healing = True
    try:
        entry.group_name = group.name
        entry.name = group.name
        entry.previous_group = group.name
    finally:
        _healing = False
    return True


def purge_orphans(obj):
    """Reunite or drop gradients whose vertex group name is gone. True on either.

    A plain rename is followed. Anything else - the group deleted, groups
    reordered - drops the gradient rather than guessing, which keeps the tool
    self-cleaning instead of letting invisible state pile up in the file. Ctrl+Z
    brings it straight back.

    A write, so never from `draw`: the operators and the overlay timer call it.
    """
    names = {group.name for group in obj.vertex_groups}
    changed = False
    for index in reversed(range(len(obj.tk_gradients))):
        entry = obj.tk_gradients[index]
        if entry.group_name in names:
            continue
        changed = True
        if not _heal_rename(obj, entry):
            discard(obj, index)

    remember_groups(obj)
    return changed


def discard(obj, index):
    """Drop a gradient, leaving its vertex group and weights exactly as they are.

    The only exit there is. Nothing here deletes a vertex group: the weights are
    the work, and a gradient letting go of them is not a reason to lose them.
    """
    entry = obj.tk_gradients[index]
    name = entry.group_name

    # Cleared before the datablock goes, or the pointer is left dangling.
    ramp, entry.ramp = entry.ramp, None
    if ramp is not None and ramp.users == 0:
        bpy.data.textures.remove(ramp)

    obj.tk_gradients.remove(index)
    return name


def index_of(obj, settings):
    """Where `settings` sits in the object's gradients."""
    return next(
        index
        for index, entry in enumerate(obj.tk_gradients)
        if entry == settings
    )


def showing(obj):
    """Is there a gradient whose path should be drawn on this object?

    Selecting a vertex group that has a gradient is the whole of the intent -
    there was a Show Handles toggle here once and it meant nothing, because
    nobody opens a gradient's panel in order not to see it.
    """
    return bool(
        obj is not None
        and obj.type == 'MESH'
        and active_gradient(obj) is not None
    )


def _memberships(obj):
    """Group index to weight, per vertex. VertexGroup.weight() raises outside."""
    return [{g.group: g.weight for g in v.groups} for v in obj.data.vertices]


def read_weights(obj, name):
    """Every vertex's weight in `name`, `NOT_A_MEMBER` where it has none."""
    group = obj.vertex_groups.get(name)
    if group is None:
        return None
    return [
        weights.get(group.index, NOT_A_MEMBER) for weights in _memberships(obj)
    ]


def capture_baseline(settings, obj):
    """Remember what the group held before this gradient adopted it, once.

    Two readers, and neither is a commit log. A mask blends *towards* this:
    outside the mask the weights the user already had must stand, and they
    cannot be read off the group once the gradient has started overwriting it.
    Remove hands the same weights back, which is what makes Remove and Apply a
    symmetric pair - undo the adoption, or keep the result.
    """
    if BASELINE_KEY in settings:
        return
    saved = read_weights(obj, settings.group_name)
    if saved is not None:
        settings[BASELINE_KEY] = saved


def drop_baseline(settings):
    """The gradient let the group go, so there is no 'before' any more."""
    if BASELINE_KEY in settings:
        del settings[BASELINE_KEY]


def baseline_of(settings, count):
    """The stored baseline, or None when there is none that still fits.

    A mesh whose vertex count has changed since the capture invalidates it
    outright - the array is positional and there is nothing to map it through.
    """
    saved = settings.get(BASELINE_KEY)
    return saved if saved is not None and len(saved) == count else None


def hand_painted(obj, settings):
    """Has something other than the gradient changed its group's weights?

    Exact rather than inferred: the depsgraph says the mesh updated, but a posed
    armature, a shape key slider and a modifier tweak all say the same thing.
    Comparing against what was actually written is what tells a brush stroke
    from any of those.
    """
    saved = settings.get(WRITTEN_KEY)
    if saved is None or len(saved) != len(obj.data.vertices):
        return False
    current = read_weights(obj, settings.group_name)
    if current is None:
        return False
    return any(abs(a - b) > 1e-4 for a, b in zip(current, saved))


def detach(obj, settings):
    """Let the group go, leaving the weights exactly as they stand.

    Painting on a gradient's group is how you claim it - the same bargain as
    Blender's redo panel, which closes the moment you do anything else. There is
    no Apply button because this *is* Apply.
    """
    discard(obj, index_of(obj, settings))


def _rewrite(self, context):
    """Every live control routes here, and none of them writes directly.

    A slider drag and a gizmo drag both fire this once per mouse-move event, and
    a full write per event is a slideshow on any real mesh. Flagging instead lets
    the overlay's timer collapse a whole drag into one write per poll.
    """
    mark_dirty()


def _invert_changed(self, context):
    """Negating the weights moves where each one reads on the bar."""
    mirror_weights_to_ramp(self)
    _rewrite(self, context)


def _rename_group(self, context):
    """Re-aim the gradient at another group, taking its output with it.

    A group this gradient created has no other claim on it, so it goes rather
    than being left behind empty of meaning. One the user already had stays
    exactly as it is - it was theirs before the gradient adopted it, and the
    backup that says what it held is dropped with the claim.
    """
    if _healing:
        return
    obj = context.active_object
    if obj is not None and self.created_group and self.previous_group:
        stale = obj.vertex_groups.get(self.previous_group)
        if stale is not None:
            obj.vertex_groups.remove(stale)
    if self.previous_group:
        drop_baseline(self)

    self.created_group = False
    self.previous_group = self.group_name
    # The active vertex group is what selects a gradient, so re-aiming one has
    # to move the selection with it - otherwise the panel is left showing a
    # group whose gradient has just walked off, or nothing at all.
    if obj is not None and self.group_name:
        group = obj.vertex_groups.get(self.group_name)
        if group is None:
            group = obj.vertex_groups.new(name=self.group_name)
            self.created_group = True
        else:
            # Captured on adoption, not lazily on the first write: by then the
            # gradient has already overwritten the very weights the baseline is
            # supposed to be. A group it created has no history to keep.
            capture_baseline(self, obj)
        obj.vertex_groups.active = group
        # Where it sits now, so a later rename has something to be recognised
        # by. The overlay timer refreshes this every poll thereafter.
        self.group_index = group.index
        self.group_count = len(obj.vertex_groups)
    _rewrite(self, context)


class TK_PG_gradient_handle(bpy.types.PropertyGroup):
    """One point on the gradient path."""

    position: bpy.props.FloatVectorProperty(
        name="Position", subtype='TRANSLATION', unit='LENGTH', update=_mark_dirty
    )
    # The weight the gradient reaches at this handle, and the position of the
    # stop that edits it. The handle owns this; the ramp mirrors it.
    weight: bpy.props.FloatProperty(default=0.0, min=0.0, max=1.0)


class TK_PG_weight_gradient(bpy.types.PropertyGroup):
    """One gradient: a path, a weight profile along it, and the group it writes.

    An object holds a list of these, the way it holds modifiers. There is no
    session and no commit - a gradient exists, it owns its vertex group, and it
    stays editable. Deleting it is what cancelling used to be.
    """

    name: bpy.props.StringProperty(name="Name", default="Gradient")

    shape: bpy.props.EnumProperty(
        name="Shape", items=gradient.SHAPES, default='LINEAR', update=_rewrite
    )
    profile: bpy.props.EnumProperty(
        name="Profile",
        description="How the weight travels from one handle to the next. The "
        "handles themselves always read exactly what their stops say",
        items=gradient.PROFILES,
        default='LINEAR',
        update=_rewrite,
    )
    handles: bpy.props.CollectionProperty(type=TK_PG_gradient_handle)
    curved: bpy.props.BoolProperty(
        name="Curved",
        description="Bend the path smoothly through the handles instead of "
        "running straight between them",
        default=False,
        update=_rewrite,
    )
    ramp: bpy.props.PointerProperty(type=bpy.types.Texture)
    invert: bpy.props.BoolProperty(
        name="Invert",
        description="Negate every weight, so a gradient and its inverse add up "
        "to 1 everywhere. The stops move to where their weights now read",
        default=False,
        update=_invert_changed,
    )
    smooth_repeat: bpy.props.IntProperty(
        name="Smooth",
        description="Relaxation passes over the weights",
        default=0,
        min=0,
        max=20,
        update=_rewrite,
    )
    group_name: bpy.props.StringProperty(
        name="Group",
        description="Vertex group this gradient writes. It owns that group - "
        "point it at another and the weights move with it",
        default="Group",
        update=_rename_group,
    )
    blend: bpy.props.EnumProperty(
        name="Blend",
        description="What the gradient does to the weights the group already "
        "had when it was adopted. A group the gradient created had none, so "
        "everything but Replace leaves it empty",
        items=gradient.BLENDS,
        default='REPLACE',
        update=_rewrite,
    )
    mask_group: bpy.props.StringProperty(
        name="Mask",
        description="Only write where this group has weight; elsewhere the "
        "weights the group had before the gradient adopted it are left alone",
        update=_rewrite,
    )

    # Whether the group is the gradient's to delete, and what it was called last
    # time - the rename callback is told the new name, never the old one.
    created_group: bpy.props.BoolProperty(default=False)
    previous_group: bpy.props.StringProperty(default="")
    # Where the group sat, and how many there were, the last time the two still
    # lined up. That is the only evidence a rename in Blender's own list leaves.
    group_index: bpy.props.IntProperty(default=-1)
    group_count: bpy.props.IntProperty(default=-1)


classes = (TK_PG_gradient_handle, TK_PG_weight_gradient)
