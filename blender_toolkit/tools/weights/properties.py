import bpy
from mathutils import Vector

from . import gradient, snapping

RAMP_NAME = "TK Weight Gradient"

# What the session has touched and how to put it back, recorded as real data on
# the object rather than in a module dict. A dict does not survive undo, a file
# save, a Reload Scripts or the object being renamed - and every one of those
# leaves Cancel restoring weights that no longer correspond to the mesh.
#
# Two halves, because "the group had no members" and "there was no group" have
# to be told apart: SESSION_KEY names what was touched and whether the session
# created it, and a borrowed group's weights are copied into a backup group,
# whose membership mirrors the original exactly.
SESSION_KEY = "tk_gradient_session"
BACKUP_PREFIX = "tk.backup."

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
    """
    if not settings.use_ramp or len(settings.handles) < MIN_STOPS:
        return None
    return gradient.weight_curve(
        zip(handle_arcs(settings, points), (h.weight for h in settings.handles))
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
    """Move the stops to where the handles' weights now read on the bar.

    Nothing else writes stop positions - the bar is an input, not an output -
    except when what a weight *means* changes underneath it, which is exactly
    what Invert does.
    """
    ramp = ramp_of(settings)
    if ramp is None or not settings.handles:
        return
    shown = sorted(flip(settings, h.weight) for h in settings.handles)
    elements = sorted(ramp.elements, key=lambda e: e.position)
    for element, weight in zip(elements, shown):
        element.position = weight
        element.color = gradient.weight_colour(weight)


def handle_values(settings):
    """The weight the gradient produces at each handle.

    With the gradient on, that is the handle's own weight as the bar shows it -
    the stop it owns sits at exactly that place on the scale. With it off, the
    named profile decides, read at where the handle sits along the path. Either
    way this is what the mesh reads there, which is what lets a handle be drawn
    in the colour the surface under it will be.
    """
    if settings.use_ramp:
        return [flip(settings, h.weight) for h in settings.handles]
    return [
        gradient.value(
            arc,
            profile=settings.profile,
            midpoint=settings.midpoint,
            invert=settings.invert,
        )
        for arc in handle_arcs(settings)
    ]


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
        # Where the user's drag landed. Nothing else writes these.
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
    settings.active_handle = min(settings.active_handle, len(settings.handles) - 1)
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


def write_weights(context, settings, points=None, curve=None):
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

    weights = {
        index: gradient.value(
            t,
            profile=settings.profile,
            midpoint=settings.midpoint,
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
    if not name or name.startswith(BACKUP_PREFIX):
        return
    snapshot(obj, name)  # before the first write to this group, not before all
    group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
    # Weight paint draws the active group, so the one being written is the one
    # that should be on screen - including straight after a rename.
    obj.vertex_groups.active = group

    mask = obj.vertex_groups.get(settings.mask_group) if settings.mask_group else None
    # What the mask blends towards is the group as the session found it, never
    # as it stands: the session rewrites on every property change, and blending
    # against its own last result walks a half-masked vertex towards the full
    # gradient one tweak at a time until the soft edge has eroded away.
    baseline = baseline_of(obj, name) if mask is not None else None
    per_vertex = _memberships(obj) if mask is not None else None

    global _writing
    _writing = True
    try:
        for index, weight in weights.items():
            value = weight
            if mask is not None:
                # Outside the mask the pre-session weight stands, so a region can
                # be protected; a soft mask edge blends old and new.
                influence = per_vertex[index].get(mask.index, 0.0)
                existing = 0.0 if baseline is None else (baseline[index] or 0.0)
                value = existing + (value - existing) * influence
            # ponytail: per-vertex add, measured 28ms/65k verts - about half of
            # what a cached rewrite now costs. Worth revisiting with
            # foreach_set only if that half starts to matter.
            group.add([index], value, 'REPLACE')
    finally:
        _writing = False


# Saved gradients live on the object, keyed by group name. A VertexGroup cannot
# hold custom properties at all - "id properties not supported for this type" -
# so there is nowhere closer to put them. The cost of that is a group renamed in
# Blender's own list leaves its record behind under the old name.
RECORD_KEY = "tk_gradient"

# Everything that decides what the gradient produces. Snap is left out: it is
# how a handle is placed, not part of the result.
RECORDED = (
    "shape", "profile", "midpoint", "invert", "smooth_repeat", "curved",
    "use_ramp", "mask_group",
)


# Not reset with the rest: the ramp is a datablock the settings only point at,
# and the two session-state flags are what the caller is in the middle of
# setting. reset_settings clears the ramp's stops instead of dropping it.
_KEEP_ON_RESET = {"rna_type", "name", "ramp", "active", "previous_mode"}


def reset_settings(settings):
    """Back to defaults. A session never inherits the last one's settings."""
    for prop in settings.bl_rna.properties:
        if prop.identifier not in _KEEP_ON_RESET:
            settings.property_unset(prop.identifier)
    reset_ramp(settings)


def unused_group_name(obj, base="Group"):
    """A group name nothing is using yet, numbered Blender's way."""
    if base not in obj.vertex_groups:
        return base
    index = 1
    while f"{base}.{index:03d}" in obj.vertex_groups:
        index += 1
    return f"{base}.{index:03d}"


def has_record(obj, name):
    return name in (obj.get(RECORD_KEY) or {})


def save_record(obj, settings, name):
    """Remember how this group was built, so it can be picked up again later."""
    ramp = ramp_of(settings)
    records = {
        key: dict(value) for key, value in (obj.get(RECORD_KEY) or {}).items()
    }
    records[name] = {
        **{key: getattr(settings, key) for key in RECORDED},
        # Weight first, then the object-space position. The stops are not stored
        # separately any more - a stop is just a handle's weight.
        "handles": [[h.weight, *h.position] for h in settings.handles],
    }
    obj[RECORD_KEY] = records


def load_record(obj, settings, name):
    """Restore a saved gradient onto the settings. True when there was one."""
    record = (obj.get(RECORD_KEY) or {}).get(name)
    if record is None:
        return False

    # Deactivated across the load: every one of these properties rewrites the
    # weights on change, and a restore would otherwise cost a dozen full passes
    # to arrive where one at the end gets it.
    was_active = settings.active
    settings.active = False
    try:
        for key in RECORDED:
            setattr(settings, key, record[key])

        # Three shapes have been written. A leading float is a weight now, but
        # in the version before this it was the stop position along the path,
        # and the weights lived in a separate "stops" list.
        entries = [list(e) for e in record["handles"]]
        stops = [list(s) for s in record.get("stops", [])]
        if len(entries[0]) == 4 and stops:
            weights = [value for _position, value in stops]
        elif len(entries[0]) == 4:
            weights = [entry[0] for entry in entries]
        else:  # no weight was stored at all; spread them evenly
            last = max(len(entries) - 1, 1)
            weights = [index / last for index in range(len(entries))]

        settings.handles.clear()
        for entry, weight in zip(entries, weights):
            handle = settings.handles.add()
            handle.weight = weight
            handle.position = entry[-3:]
        settings.active_handle = 0

        ensure_ramp(settings)
        ramp = ramp_of(settings)
        # One at a time, re-fetched: removing invalidates the others.
        while len(ramp.elements) > MIN_STOPS:
            ramp.elements.remove(ramp.elements[1])
        shown = sorted(flip(settings, weight) for weight in weights)
        for index, weight in enumerate(shown):
            element = (
                ramp.elements[index] if index < len(ramp.elements)
                else ramp.elements.new(weight)
            )
            element.position = weight
            element.color = gradient.weight_colour(weight)
    finally:
        settings.active = was_active
    return True


def _memberships(obj):
    """Group index to weight, per vertex. VertexGroup.weight() raises outside."""
    return [{g.group: g.weight for g in v.groups} for v in obj.data.vertices]


def snapshot(obj, name):
    """Remember a group before this session first overwrites it.

    Recorded per group and lazily, because a session writes several in a row:
    rename, adjust, Add, repeat.

    A borrowed group's weights go into a backup group whose membership mirrors
    the original, so "not a member" round-trips as itself rather than as zero.
    A group the session had to create is only named, with nothing to back up.
    """
    session = dict(obj.get(SESSION_KEY) or {})
    if name in session:
        return

    group = obj.vertex_groups.get(name)
    session[name] = "borrowed" if group else "created"
    obj[SESSION_KEY] = session
    if group is None:
        return

    backup = obj.vertex_groups.new(name=BACKUP_PREFIX + name)
    for index, weights in enumerate(_memberships(obj)):
        if group.index in weights:
            backup.add([index], weights[group.index], 'REPLACE')


def restore(obj):
    """Put the mesh back exactly as snapshot() found it."""
    session = obj.get(SESSION_KEY)
    if session is None:
        return

    for name, origin in dict(session).items():
        group = obj.vertex_groups.get(name)
        backup = obj.vertex_groups.get(BACKUP_PREFIX + name)
        if group is not None and origin == "created":
            obj.vertex_groups.remove(group)
        elif group is not None and backup is not None:
            saved = [v.get(backup.index) for v in _memberships(obj)]
            for index, weight in enumerate(saved):
                if weight is None:  # vertex was not a member before
                    group.remove([index])
                else:
                    group.add([index], weight, 'REPLACE')
        if backup is not None:
            obj.vertex_groups.remove(backup)
    del obj[SESSION_KEY]


def forget(obj):
    """Commit: the session's writes stand, so the way back is dropped."""
    session = obj.get(SESSION_KEY)
    if session is None:
        return
    for name in dict(session):
        backup = obj.vertex_groups.get(BACKUP_PREFIX + name)
        if backup is not None:
            obj.vertex_groups.remove(backup)
    del obj[SESSION_KEY]


def baseline_of(obj, name):
    """Pre-session weights of `name`, or None when the session created it."""
    backup = obj.vertex_groups.get(BACKUP_PREFIX + name)
    if backup is None:
        return None
    return [v.get(backup.index) for v in _memberships(obj)]


def _rewrite(self, context):
    """Every live control routes here, and none of them writes directly.

    A slider drag and a gizmo drag both fire this once per mouse-move event, and
    a full write per event is a slideshow on any real mesh. Flagging instead lets
    the session's timer collapse a whole drag into one write per poll.
    """
    if self.active:
        mark_dirty()


def _invert_changed(self, context):
    """Negating the weights moves where each one reads on the bar."""
    mirror_weights_to_ramp(self)
    _rewrite(self, context)


def _rename_group(self, context):
    """A rename moves the gradient rather than leaving a copy behind.

    Restoring first undoes everything the session wrote under the old name -
    removing the group if the session created it, putting the weights back if it
    did not - so only the group named right now carries the gradient. Add is what
    makes a name permanent; before that, the name is still being chosen.
    """
    obj = context.active_object
    if self.active and obj is not None:
        restore(obj)
        # Naming a group that was built with this tool picks its gradient back
        # up, so an earlier one can be adjusted rather than rebuilt by eye.
        load_record(obj, self, self.group_name)
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
    """Settings and session state for the Weight Gradient tool."""

    shape: bpy.props.EnumProperty(
        name="Shape", items=gradient.SHAPES, default='LINEAR', update=_rewrite
    )
    profile: bpy.props.EnumProperty(
        name="Profile", items=gradient.PROFILES, default='LINEAR', update=_rewrite
    )
    handles: bpy.props.CollectionProperty(type=TK_PG_gradient_handle)
    active_handle: bpy.props.IntProperty(name="Handle", default=0, min=0)
    curved: bpy.props.BoolProperty(
        name="Curved",
        description="Bend the path smoothly through the handles instead of "
        "running straight between them",
        default=False,
        update=_rewrite,
    )
    snap: bpy.props.EnumProperty(
        name="Snap",
        description="What a dragged handle lands on",
        items=snapping.MODES,
        default='FREE',
    )
    ramp: bpy.props.PointerProperty(type=bpy.types.Texture)
    use_ramp: bpy.props.BoolProperty(
        name="Use Gradient",
        description="Map position along the path to weight with the gradient. "
        "Its stops are held greyscale - this picks a value, not a colour",
        default=True,
        update=_rewrite,
    )
    midpoint: bpy.props.FloatProperty(
        name="Midpoint",
        description="Where along the gradient the weight passes 0.5",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        update=_rewrite,
    )
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
        description="Vertex group the gradient is written into. Rename it and "
        "Add again to build up several",
        default="Group",
        update=_rename_group,
    )
    mask_group: bpy.props.StringProperty(
        name="Mask",
        description="Only write where this group has weight; elsewhere the "
        "existing weights are left alone",
        update=_rewrite,
    )

    active: bpy.props.BoolProperty(default=False)
    previous_mode: bpy.props.StringProperty(default='OBJECT')


classes = (TK_PG_gradient_handle, TK_PG_weight_gradient)
