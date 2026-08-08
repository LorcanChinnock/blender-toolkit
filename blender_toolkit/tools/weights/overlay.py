"""Viewport feedback for an active Weight Gradient session.

The shader is built on first draw, never at import or register time:
gpu.shader.from_builtin() raises SystemError until the GPU module is
initialised, which never happens in a plain background Blender.
"""

import math

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector

from . import properties, snapping

_handler = None
_shader = None
_mask_shader = None
_mask_cache = (None, None)

# The ramp as it was last seen, so an edit to it can be noticed at all.
_signature = None

# A fixed pool of gizmos, hidden down to the handle count, so the gizmo
# collection is never mutated at runtime. 32 because that is Blender's own hard
# ceiling on ColorRamp elements - "Unable to add element to colorband (limit
# 32)" - and one handle per stop makes it ours too. There is no second limit to
# impose: the widget that adds handles cannot go past this one.
MAX_HANDLES = 32

LINE_COLOUR = (1.0, 1.0, 1.0, 0.6)

# Fast enough that clicking + on the ramp feels immediate, slow enough that the
# poll is free.
SYNC_INTERVAL = 0.15

# Tint over the part of the mesh the mask is holding back, so a low weight there
# reads as "protected" rather than "the gradient reached zero".
MASK_COLOUR = (1.0, 0.2, 0.1)
MASK_ALPHA = 0.4
# Lifted off the surface by this fraction of the object's size, or the tint
# z-fights with the mesh it is drawn over.
MASK_OFFSET = 0.001


def _get_shader():
    global _shader
    if _shader is None:
        import gpu

        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    return _shader


def _get_mask_shader():
    global _mask_shader
    if _mask_shader is None:
        import gpu

        _mask_shader = gpu.shader.from_builtin('SMOOTH_COLOR')
    return _mask_shader


def _circle(centre, radius, normal, segments=48):
    """Points of a circle around `centre` on the plane facing `normal`."""
    axis = Vector(normal)
    if axis.length == 0.0:
        axis = Vector((0.0, 0.0, 1.0))
    axis.normalize()
    side = axis.orthogonal().normalized()
    up = axis.cross(side)
    return [
        centre + side * (radius * math.cos(a)) + up * (radius * math.sin(a))
        for a in (i / segments * math.tau for i in range(segments + 1))
    ]


def _ring_segments(ring):
    return [p for i in range(len(ring) - 1) for p in (ring[i], ring[i + 1])]


def _geometry(obj, settings):
    """Handle positions and line segments, in world space."""
    matrix = obj.matrix_world
    handles = [matrix @ Vector(h.position) for h in settings.handles]
    if len(handles) < 2:
        return handles, []

    path = [matrix @ Vector(p) for p in properties.path_of(settings)]
    lines = [p for i in range(len(path) - 1) for p in (path[i], path[i + 1])]

    first, last = handles[0], handles[-1]
    direction = last - first
    if settings.shape == 'SPHERICAL':
        lines += _ring_segments(_circle(first, direction.length, direction))
    elif settings.shape == 'BAND':
        radius = direction.length * 0.5
        for centre in (first, last):
            lines += _ring_segments(_circle(centre, radius, direction))

    return handles, lines


def _mask_batch(obj, settings):
    """Triangles tinted by how much the mask is protecting them, or None.

    Fanned from the polygons rather than mesh.loop_triangles, because filling
    that cache is a write to the mesh and a draw handler is a read-only context.

    ponytail: rebuilt when the object, the mask or the topology changes, not
    when the mask's weights do - repainting the mask mid-session needs the field
    re-picked to show. Watching the weights costs a full scan on every redraw.
    """
    global _mask_cache
    mesh = obj.data
    mask = obj.vertex_groups.get(settings.mask_group) if settings.mask_group else None
    key = (obj.name, settings.mask_group, len(mesh.vertices), len(mesh.polygons))
    if key == _mask_cache[0]:
        return _mask_cache[1]

    batch = None
    if mask is not None and mesh.polygons:
        from gpu_extras.batch import batch_for_shader

        coords = [v.co for v in mesh.vertices]
        size = max(
            max(c[i] for c in coords) - min(c[i] for c in coords) for i in range(3)
        )
        offset = (size or 1.0) * MASK_OFFSET
        points, colours = [], []
        for vert in mesh.vertices:
            influence = {g.group: g.weight for g in vert.groups}.get(mask.index, 0.0)
            points.append(vert.co + vert.normal * offset)
            colours.append((*MASK_COLOUR, (1.0 - influence) * MASK_ALPHA))
        indices = [
            (poly.vertices[0], poly.vertices[i], poly.vertices[i + 1])
            for poly in mesh.polygons
            for i in range(1, len(poly.vertices) - 1)
        ]
        batch = batch_for_shader(
            _get_mask_shader(), 'TRIS', {"pos": points, "color": colours},
            indices=indices,
        )

    _mask_cache = (key, batch)
    return batch


def _draw_mask(obj, settings):
    import gpu

    batch = _mask_batch(obj, settings)
    if batch is None:
        return

    shader = _get_mask_shader()
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.matrix.push()
    gpu.matrix.multiply_matrix(obj.matrix_world)
    shader.bind()
    batch.draw(shader)
    gpu.matrix.pop()


def _draw():
    context = bpy.context
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return
    settings = obj.tk_gradient
    if not settings.active:
        return

    import gpu
    from gpu_extras.batch import batch_for_shader

    shader = _get_shader()
    handles, lines = _geometry(obj, settings)

    gpu.state.blend_set('ALPHA')
    _draw_mask(obj, settings)  # under the path, and the only depth-tested part
    if not handles:
        gpu.state.blend_set('NONE')
        gpu.state.depth_test_set('LESS_EQUAL')
        return

    gpu.state.depth_test_set('NONE')
    gpu.state.point_size_set(12.0)

    shader.bind()
    if lines:
        shader.uniform_float("color", LINE_COLOUR)
        batch_for_shader(shader, 'LINES', {"pos": lines}).draw(shader)

    for point, colour in zip(handles, properties.handle_colours(settings)):
        shader.uniform_float("color", colour)
        batch_for_shader(shader, 'POINTS', {"pos": [point]}).draw(shader)

    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('LESS_EQUAL')


def _sync():
    """The session's heartbeat: follow the ramp, and do the deferred writes.

    Two jobs, both of which have to be here rather than anywhere more direct.

    The ramp has no update callback and its add/remove buttons are not even
    operators, so a stop appearing or moving can only be *noticed* - by counting
    the handles against it, and by comparing a signature with the last one. That
    noticing cannot ride on drawing: a draw handler and GizmoGroup.refresh both
    run in a read-only context, where writing the handle collection raises
    "Writing to ID classes in this context is not allowed". A timer runs in a
    writable one.

    Everything else - sliders, gizmo drags, the panel - only sets a flag, so a
    drag firing an update per mouse-move event collapses into one write here.
    """
    global _signature
    context = bpy.context
    obj = context.active_object or context.view_layer.objects.active
    settings = getattr(obj, "tk_gradient", None) if obj is not None else None
    if settings is None or not settings.active:
        return None  # session over; the timer retires with it

    if properties.normalise_ramp(settings):
        properties.mark_dirty()
    if properties.sync_handles_to_ramp(settings):
        properties.mark_dirty()
    signature = properties.ramp_signature(settings)
    if signature != _signature:
        _signature = signature
        properties.mark_dirty()

    if properties.take_dirty():
        properties.write_weights(context, settings)
        # The mouse is often over the panel rather than the viewport, so nothing
        # else is going to ask the 3D view to redraw with the new result.
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    return SYNC_INTERVAL


@persistent
def _on_depsgraph(scene, depsgraph):
    """Rebuild the mask tint when the mesh changes under it - but not when the
    change is our own write, which would rebuild it several times a second."""
    if not properties.writing():
        invalidate_mask()


def invalidate_mask():
    global _mask_cache
    _mask_cache = (None, None)


def enable():
    global _handler
    if _handler is None:
        _handler = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')
    if not bpy.app.timers.is_registered(_sync):
        bpy.app.timers.register(_sync)
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def disable():
    global _handler, _signature
    invalidate_mask()  # a batch must not outlive the mesh it was built from
    _signature = None
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    if bpy.app.timers.is_registered(_sync):
        bpy.app.timers.unregister(_sync)
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)


def _handle_accessors(index):
    """Get/set closures mapping one gizmo onto one handle, in world space."""

    def settings_and_object():
        obj = bpy.context.active_object
        return (obj.tk_gradient if obj is not None else None), obj

    def get():
        settings, obj = settings_and_object()
        if settings is None or index >= len(settings.handles):
            return [0.0, 0.0, 0.0]
        return list(obj.matrix_world @ Vector(settings.handles[index].position))

    def set(value):
        settings, obj = settings_and_object()
        if settings is None or index >= len(settings.handles):
            return
        local = obj.matrix_world.inverted() @ Vector(value)
        # Snap as the handle moves rather than on release, so what you see
        # during the drag is what you get. region_data lets it aim down the
        # view ray and land on what the cursor is over, rather than on whatever
        # happens to be nearest in 3D.
        settings.handles[index].position = snapping.snap(
            obj, local, settings.snap, bpy.context.region_data
        )

    return get, set


class TK_GGT_weight_gradient(bpy.types.GizmoGroup):
    """Draggable handles for the gradient path."""

    bl_idname = "TK_GGT_weight_gradient"
    bl_label = "Weight Gradient"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH' and obj.tk_gradient.active)

    def setup(self, context):
        # A fixed pool, hidden as needed: this never mutates the gizmo
        # collection at runtime.
        self.handle_gizmos = []
        for index in range(MAX_HANDLES):
            gizmo = self.gizmos.new("GIZMO_GT_move_3d")
            # FILL makes it a solid disc; without it move_3d draws a ring
            # outline, which is what reads as hollow over weight paint.
            gizmo.draw_options = {'ALIGN_VIEW', 'FILL'}
            gizmo.alpha = 0.9
            gizmo.color_highlight = (1.0, 1.0, 1.0)
            gizmo.alpha_highlight = 1.0
            gizmo.scale_basis = 0.12
            get, set = _handle_accessors(index)
            gizmo.target_set_handler("offset", get=get, set=set)
            self.handle_gizmos.append(gizmo)
        self.refresh(context)

    def refresh(self, context):
        obj = context.active_object
        if obj is None:
            return
        settings = obj.tk_gradient

        count = min(len(settings.handles), MAX_HANDLES)
        colours = properties.handle_colours(settings)
        for index, gizmo in enumerate(self.handle_gizmos):
            gizmo.hide = index >= count
            if gizmo.hide:
                continue
            gizmo.color = colours[index][:3]
            # Identity, not obj.matrix_world: the target below is already in
            # world space, so anything else here transforms it twice - which
            # only looks right while the object sits at the origin.
            gizmo.matrix_basis = Matrix.Identity(4)


classes = (TK_GGT_weight_gradient,)
