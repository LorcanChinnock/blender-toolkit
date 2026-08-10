"""Viewport feedback for the gradient being edited.

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

# Set by the depsgraph when the mesh changed for a reason that was not us. Only
# a suspicion: a posed armature, a shape key slider and a modifier tweak all
# raise it too, so the timer confirms it against what was actually written
# before letting go of the group.
_suspect = False


# A fixed pool of gizmos, hidden down to the handle count, so the gizmo
# collection is never mutated at runtime. 32 because that is Blender's own hard
# ceiling on ColorRamp elements - "Unable to add element to colorband (limit
# 32)" - and one handle per stop makes it ours too. There is no second limit to
# impose: the widget that adds handles cannot go past this one.
MAX_HANDLES = 32

LINE_COLOUR = (1.0, 1.0, 1.0, 0.6)

# The handle disc, as a fraction of the gizmo's own screen-space scale, and how
# far from its centre a click still counts as grabbing it, in pixels.
HANDLE_RADIUS = 0.12
HANDLE_PICK_RADIUS = 22.0

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
    when the mask's weights do - repainting the mask needs the field re-picked
    to show. Watching the weights costs a full scan on every redraw.
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
    if not properties.showing(obj):
        return
    settings = properties.active_gradient(obj)

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

    shader.bind()
    if lines:
        shader.uniform_float("color", LINE_COLOUR)
        batch_for_shader(shader, 'LINES', {"pos": lines}).draw(shader)

    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('LESS_EQUAL')


def _sync():
    """The heartbeat while editing: follow the ramp, do the deferred writes.

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
    if obj is not None and obj.type == 'MESH':
        # A group renamed in Blender's own list leaves its gradient pointing at
        # nothing. This is the writable context that can clear that up.
        properties.purge_orphans(obj)
    settings = properties.active_gradient(obj)
    if settings is None:
        # No gradient on the active group - another object, or none selected.
        # Keep polling: this is the add-on's write engine and it retires only
        # when the add-on does.
        return SYNC_INTERVAL

    global _suspect
    if _suspect:
        _suspect = False
        # Skipped while a write is already queued, which is every poll of a
        # drag: the comparison costs a full read of the group, and the rewrite
        # about to happen would make it meaningless anyway.
        if not properties.pending() and properties.hand_painted(obj, settings):
            properties.detach(obj, settings)
            return SYNC_INTERVAL

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
    """Drop what the mesh changing invalidates - but not on our own writes,
    which would rebuild several times a second."""
    if properties.writing():
        return
    global _suspect
    _suspect = True
    invalidate_mask()
    # Only when the geometry itself moved. A handle position is a property on
    # the object, and writing one still fires this handler - so invalidating
    # unconditionally rebuilt the snap tree once per mouse-move event, measured
    # at 31 ms per 40k verts, inside the very drag the timer exists to coalesce.
    if any(update.is_updated_geometry for update in depsgraph.updates):
        snapping.invalidate()


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


def _disc_shape_verts(segments=24):
    """A unit disc as triangles, in the gizmo's own XY plane."""
    ring = [
        (math.cos(a), math.sin(a), 0.0)
        for a in (i / segments * math.tau for i in range(segments + 1))
    ]
    return [
        point
        for i in range(segments)
        for point in ((0.0, 0.0, 0.0), ring[i], ring[i + 1])
    ]


def _handle_of(gizmo):
    """The object and handle a gizmo stands for, or (None, None)."""
    obj = bpy.context.active_object
    settings = properties.active_gradient(obj) if properties.showing(obj) else None
    if settings is None or gizmo.index >= len(settings.handles):
        return None, None
    return obj, settings.handles[gizmo.index]


class TK_GT_gradient_handle(bpy.types.Gizmo):
    """One draggable handle, positioned by the add-on rather than by the gizmo.

    `GIZMO_GT_move_3d` was what this used to be, and it cannot snap. It owns its
    drag: the mouse delta accumulates into `matrix_basis`/`matrix_offset`, both
    of which are *added* to whatever its target reports, and it never re-reads
    the target while modal. So a setter that snapped moved the data and not the
    disc - the handle slid in the view plane while the path underneath it went
    to the surface, and only a later refresh, forced by dragging some *other*
    handle, put the two back together.

    Owning the modal instead means the position comes from the real cursor ray
    every event, `matrix_basis` is rebuilt from the stored point on every
    redraw, and `matrix_offset` is never written at all. `use_draw_modal` is
    what keeps the disc drawn during its own drag; it defaults to False.
    """

    bl_idname = "TK_GT_gradient_handle"

    __slots__ = ("custom_shape", "index", "init_position")

    def setup(self):
        if not hasattr(self, "custom_shape"):
            self.custom_shape = self.new_custom_shape('TRIS', _disc_shape_verts())
        self.index = 0
        self.init_position = (0.0, 0.0, 0.0)

    def draw(self, context):
        self.draw_custom_shape(self.custom_shape)

    def test_select(self, context, location):
        """Pick in 2D, so the pick radius is the disc as it looks on screen."""
        obj, handle = _handle_of(self)
        if obj is None:
            return -1
        from bpy_extras.view3d_utils import location_3d_to_region_2d

        screen = location_3d_to_region_2d(
            context.region, context.region_data,
            obj.matrix_world @ Vector(handle.position),
        )
        if screen is None:
            return -1
        distance = (screen - Vector(location)).length
        return int(distance) if distance <= HANDLE_PICK_RADIUS else -1

    def invoke(self, context, event):
        _obj, handle = _handle_of(self)
        if handle is None:
            return {'CANCELLED'}
        self.init_position = tuple(handle.position)
        return {'RUNNING_MODAL'}

    def modal(self, context, event, tweak):
        obj, handle = _handle_of(self)
        if obj is None:
            return {'CANCELLED'}
        from bpy_extras import view3d_utils

        region, region_data = context.region, context.region_data
        mouse = (event.mouse_region_x, event.mouse_region_y)
        to_local = obj.matrix_world.inverted()

        # The plane the handle started on is where a FREE drag - and a ray that
        # misses the mesh - has to land, or the handle would have no depth.
        world_start = obj.matrix_world @ Vector(self.init_position)
        fallback = to_local @ view3d_utils.region_2d_to_location_3d(
            region, region_data, mouse, world_start
        )

        origin = to_local @ view3d_utils.region_2d_to_origin_3d(
            region, region_data, mouse
        )
        direction = (
            to_local.to_3x3()
            @ view3d_utils.region_2d_to_vector_3d(region, region_data, mouse)
        ).normalized()

        handle.position = snapping.snap(
            obj, origin, direction, obj.tk_gradient_snap, fallback
        )
        properties.mark_dirty()
        return {'RUNNING_MODAL'}

    def exit(self, context, cancel):
        _obj, handle = _handle_of(self)
        if cancel and handle is not None:
            handle.position = self.init_position
            properties.mark_dirty()


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
        return properties.showing(obj)

    def setup(self, context):
        # A fixed pool, hidden as needed: this never mutates the gizmo
        # collection at runtime.
        self.handle_gizmos = []
        for index in range(MAX_HANDLES):
            gizmo = self.gizmos.new(TK_GT_gradient_handle.bl_idname)
            gizmo.index = index
            gizmo.alpha = 0.9
            gizmo.color_highlight = (1.0, 1.0, 1.0)
            gizmo.alpha_highlight = 1.0
            gizmo.scale_basis = HANDLE_RADIUS
            gizmo.use_draw_modal = True  # or the disc vanishes during its drag
            # No use_grab_cursor: it wraps the pointer at the region edge, and
            # the position here comes from the absolute mouse coordinate rather
            # than an accumulated delta, so a wrap would teleport the handle.
            gizmo.use_undo = True
            self.handle_gizmos.append(gizmo)
        self.draw_prepare(context)

    def draw_prepare(self, context):
        """Hold every gizmo to its handle, once per redraw.

        `matrix_basis` is the whole position - `matrix_offset` is left at
        identity, because the two compose and writing both puts a handle at
        twice its distance from the origin. Rebuilt here rather than in
        `refresh`, which does not run per redraw, and *not* skipped while modal:
        the drag writes the handle, so this is what draws the drag.

        The rotation faces the view, which is what `move_3d`'s ALIGN_VIEW used
        to do for the disc it drew.
        """
        obj = context.active_object
        settings = properties.active_gradient(obj) if properties.showing(obj) else None
        if settings is None:
            return

        facing = Matrix.Identity(4)
        if context.region_data is not None:
            facing = context.region_data.view_matrix.inverted().to_3x3().to_4x4()

        count = min(len(settings.handles), MAX_HANDLES)
        colours = properties.handle_colours(settings)
        for index, gizmo in enumerate(self.handle_gizmos):
            gizmo.hide = index >= count
            if gizmo.hide:
                continue
            gizmo.color = colours[index][:3]
            matrix = facing.copy()
            matrix.translation = obj.matrix_world @ Vector(
                settings.handles[index].position
            )
            gizmo.matrix_basis = matrix


classes = (TK_GT_gradient_handle, TK_GGT_weight_gradient)
