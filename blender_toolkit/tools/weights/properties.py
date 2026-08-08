import bpy
from mathutils import Vector

from . import gradient, snapping

# Pre-session weights, keyed by object name, so Cancel can put them back. The
# session is transient by design, so this deliberately does not survive a reload.
_snapshots = {}

RAMP_NAME = "TK Weight Gradient"

# Blender's own floor is one stop; a gradient needs two ends to run between.
MIN_STOPS = 2

# Above this many segments a per-vertex scan over all of them dominates: the
# maths is ~16ms per 65k verts for one segment, and a curved 8-handle path is 84.
_KDTREE_THRESHOLD = 8


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

    Two things it enforces, both on every redraw so a native add or remove is
    picked up without needing a callback the datablock does not offer:

    - **At least two stops.** Blender is happy with one; a gradient is not.
    - **Greyscale, opaque.** This picks a weight, not a colour, so a stop is
      flattened to the mean of its RGB. The widget still opens a colour picker -
      that is Blender's, not ours - but nothing downstream can read a hue as a
      weight, and what you see is what is written.
    """
    ramp = ramp_of(settings)
    if ramp is None:
        return False

    changed = False
    while len(ramp.elements) < MIN_STOPS:
        # At the far end from the survivor, so the two are never stacked.
        survivor = ramp.elements[0].position
        ramp.elements.new(0.0 if survivor > 0.5 else 1.0)
        changed = True

    for element in ramp.elements:
        value = sum(element.color[:3]) / 3.0
        flattened = (value, value, value, 1.0)
        if tuple(element.color) != flattened:
            element.color = flattened
            changed = True
    return changed


def ramp_curve(settings):
    """Value mapping from the ramp, or None to fall back to the named profile."""
    ramp = ramp_of(settings) if settings.use_ramp else None
    if ramp is None:
        return None
    # Red alone: normalise_ramp keeps the channels equal, so this is the value.
    return lambda t: ramp.evaluate(t)[0]


def reset_ramp(settings):
    """Back to a straight black-to-white ramp with two stops."""
    ramp = ramp_of(settings)
    if ramp is None:
        return
    # One at a time, re-fetched: removing an element invalidates references to
    # the others, so a cached list of them goes stale mid-loop.
    while len(ramp.elements) > MIN_STOPS:
        ramp.elements.remove(ramp.elements[1])
    ramp.elements[0].position = 0.0
    ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
    ramp.elements[-1].position = 1.0
    ramp.elements[-1].color = (1.0, 1.0, 1.0, 1.0)


def point_at(settings, t):
    """The object-space point `t` of the way along the current path."""
    points = path_of(settings)
    lengths, offsets, total = gradient.segment_lengths(points)
    if not total:
        return Vector(points[0])

    target = t * total
    for index, length in enumerate(lengths):
        if offsets[index] + length >= target or index == len(lengths) - 1:
            local = (target - offsets[index]) / length if length else 0.0
            a, b = Vector(points[index]), Vector(points[index + 1])
            return a + (b - a) * min(max(local, 0.0), 1.0)
    return Vector(points[-1])


def sync_handles_to_ramp(settings):
    """One handle per ramp stop, kept in stop order.

    The ramp is the single control for how many control points there are: its
    add and remove buttons are how you add a stop, and a handle appears at that
    spot along the path. A new handle is seeded where its stop sits along the
    path as it stands; after that the handle shapes the path and the stop shapes
    the value.

    Each handle remembers the stop it belongs to, so inserting a stop in the
    middle inserts a handle in the middle and leaves the dragged positions of
    its neighbours alone.

    Returns True when the handles changed.
    """
    ramp = ramp_of(settings)
    if ramp is None or len(settings.handles) == 0:
        return False

    stops = sorted(e.position for e in ramp.elements)
    if len(stops) == len(settings.handles) and all(
        abs(h.t - s) < 1e-6 for h, s in zip(settings.handles, stops)
    ):
        return False

    # Seed against the path as it is now, before the collection is rebuilt.
    existing = [(h.t, tuple(h.position)) for h in settings.handles]
    taken = set()
    rebuilt = []
    for stop in stops:
        match = next(
            (
                index
                for index, (t, _position) in enumerate(existing)
                if index not in taken and abs(t - stop) < 1e-6
            ),
            None,
        )
        if match is None:
            rebuilt.append((stop, tuple(point_at(settings, stop))))
        else:
            taken.add(match)
            rebuilt.append((stop, existing[match][1]))

    settings.handles.clear()
    for stop, position in rebuilt:
        handle = settings.handles.add()
        handle.t = stop
        handle.position = position
    settings.active_handle = min(settings.active_handle, len(settings.handles) - 1)
    return True


def path_of(settings):
    """The polyline the gradient runs along, in object space."""
    return gradient.path_points(
        [h.position for h in settings.handles], settings.curved
    )


def _lookup_for(points):
    """Narrow which segments path_factor has to test, without ever excluding
    the true nearest one.

    The nearest *sample* is not always next to the nearest *segment* - on a path
    that folds back, picking only its neighbours is wrong by as much as 0.2.
    The bound that does hold: if the nearest point on segment i is `d` away,
    then sample i is within `d + length(i)`. So take the best distance from the
    obvious candidates, then sweep every sample within `best + longest segment`.
    That is guaranteed to contain the answer and still touches a handful of
    segments instead of all of them.
    """
    segments = len(points) - 1
    if segments <= _KDTREE_THRESHOLD:
        return None

    from mathutils import kdtree

    tree = kdtree.KDTree(len(points))
    for index, point in enumerate(points):
        tree.insert(point, index)
    tree.balance()
    longest = max(gradient.segment_lengths(points)[0])

    def segments_around(index):
        return {i for i in (index - 1, index) if 0 <= i < segments}

    def lookup(co):
        _point, nearest, distance = tree.find(co)
        candidates = segments_around(nearest)
        best = min(
            gradient.distance_to_segment(co, points[i], points[i + 1])
            for i in candidates
        )
        for _p, index, _d in tree.find_range(co, best + longest):
            candidates |= segments_around(index)
        return candidates

    return lookup


def write_weights(context, settings, points=None, curve=None):
    """Recompute and write both groups. Called live from every setting's update.

    `points` and `curve` are passed in rather than read off `settings`: the
    one-shot operator has two plain vectors and no ramp, the session has a
    handle collection and one. Everything else is duck-typed across both.
    """
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return

    mesh = obj.data
    if points is None:
        points = path_of(settings)
        curve = ramp_curve(settings)
    if len(points) < 2:
        return

    lookup = _lookup_for(points)
    try:
        metrics = gradient.segment_lengths(points)
        weights = {
            v.index: gradient.factor(
                v.co,
                points,
                shape=settings.shape,
                profile=settings.profile,
                midpoint=settings.midpoint,
                invert=settings.invert,
                curve=curve,
                metrics=metrics,
                lookup=lookup,
            )
            for v in mesh.vertices
        }
    except ValueError:  # handles coincident mid-drag; keep the last good weights
        return

    if settings.smooth_repeat:
        weights = gradient.smooth(
            weights, [tuple(e.vertices) for e in mesh.edges], settings.smooth_repeat
        )

    # Memberships in one pass: VertexGroup.weight() raises outside the group.
    per_vertex = [{g.group: g.weight for g in v.groups} for v in mesh.vertices]
    mask = obj.vertex_groups.get(settings.mask_group) if settings.mask_group else None

    name = settings.group_name
    if not name:
        return
    snapshot(obj, name)  # before the first write to this group, not before all
    group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
    for index, weight in weights.items():
        value = weight
        # ponytail: measured on 65k verts - a 2-handle path is ~70ms of maths
        # plus ~30ms of writes, a curved 8-handle one ~530ms. If that last case
        # starts hurting, cache per-vertex factors and only redo them when the
        # path changes, not when a group name does.
        if mask is not None:
            # Outside the mask the previous weight stands, so a region can be
            # protected; a soft mask edge blends old and new.
            influence = per_vertex[index].get(mask.index, 0.0)
            existing = per_vertex[index].get(group.index, 0.0)
            value = existing + (value - existing) * influence
        # ponytail: per-vertex add, measured 30ms/65k verts. Bucket by
        # quantised weight (2.8x) only for meshes several times that size.
        group.add([index], value, 'REPLACE')


def snapshot(obj, name):
    """Remember a group before this session first overwrites it.

    Recorded per group and lazily, because a session can now write several in a
    row: rename, adjust, Add, repeat. A name that has no group yet is recorded
    as None, so Cancel deletes what the session created rather than leaving it
    behind half-populated.
    """
    saved = _snapshots.setdefault(obj.name, {})
    if name in saved:
        return

    group = obj.vertex_groups.get(name)
    if group is None:
        saved[name] = None
        return
    saved[name] = [
        {g.group: g.weight for g in v.groups}.get(group.index)
        for v in obj.data.vertices
    ]


def restore(obj):
    """Put the mesh back exactly as snapshot() found it."""
    saved = _snapshots.pop(obj.name, None)
    if saved is None:
        return
    for name, weights in saved.items():
        group = obj.vertex_groups.get(name)
        if group is None:
            continue
        if weights is None:  # the session created this group; take it away again
            obj.vertex_groups.remove(group)
            continue
        for index, weight in enumerate(weights):
            if weight is None:  # vertex was not a member before
                group.remove([index])
            else:
                group.add([index], weight, 'REPLACE')


def forget(obj):
    _snapshots.pop(obj.name, None)


def _rewrite(self, context):
    if self.active:
        write_weights(context, self)


def _rewrite_handle(self, context):
    """A handle has no session state of its own; it lives on the owning group."""
    _rewrite(context.scene.tk_gradient, context)


class TK_PG_gradient_handle(bpy.types.PropertyGroup):
    """One point on the gradient path."""

    position: bpy.props.FloatVectorProperty(
        name="Position", subtype='TRANSLATION', unit='LENGTH', update=_rewrite_handle
    )
    # The ramp stop this handle belongs to, so a stop inserted in the middle
    # grows a handle in the middle rather than one on the end.
    t: bpy.props.FloatProperty(default=0.0, min=0.0, max=1.0)




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
    invert: bpy.props.BoolProperty(name="Invert", default=False, update=_rewrite)
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
        update=_rewrite,
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
