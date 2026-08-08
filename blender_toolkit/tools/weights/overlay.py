"""Viewport feedback for an active Weight Gradient session.

The shader is built on first draw, never at import or register time:
gpu.shader.from_builtin() raises SystemError until the GPU module is
initialised, which never happens in a plain background Blender.
"""

import math

import bpy
from mathutils import Matrix, Vector

from . import gradient, properties, snapping

_handler = None
_shader = None

# ponytail: fixed pool. A ninth handle wants dynamic gizmo creation and a
# UIList; nobody has needed a 9-point gradient yet.
MAX_HANDLES = 8

LINE_COLOUR = (1.0, 1.0, 1.0, 0.6)


def _get_shader():
    global _shader
    if _shader is None:
        import gpu

        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    return _shader


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


def _draw():
    context = bpy.context
    settings = getattr(context.scene, "tk_gradient", None)
    obj = context.active_object
    if settings is None or not settings.active or obj is None or obj.type != 'MESH':
        return

    import gpu
    from gpu_extras.batch import batch_for_shader

    shader = _get_shader()
    handles, lines = _geometry(obj, settings)
    if not handles:
        return

    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    gpu.state.point_size_set(12.0)

    shader.bind()
    if lines:
        shader.uniform_float("color", LINE_COLOUR)
        batch_for_shader(shader, 'LINES', {"pos": lines}).draw(shader)

    for point, colour in zip(handles, gradient.handle_colours(
        len(handles), settings.invert
    )):
        shader.uniform_float("color", colour)
        batch_for_shader(shader, 'POINTS', {"pos": [point]}).draw(shader)

    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('LESS_EQUAL')


def enable():
    global _handler
    if _handler is None:
        _handler = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')


def disable():
    global _handler
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, 'WINDOW')
        _handler = None


def _handle_accessors(index):
    """Get/set closures mapping one gizmo onto one handle, in world space."""

    def settings_and_object():
        context = bpy.context
        return context.scene.tk_gradient, context.active_object

    def get():
        settings, obj = settings_and_object()
        if obj is None or index >= len(settings.handles):
            return [0.0, 0.0, 0.0]
        return list(obj.matrix_world @ Vector(settings.handles[index].position))

    def set(value):
        settings, obj = settings_and_object()
        if obj is None or index >= len(settings.handles):
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
        settings = getattr(context.scene, "tk_gradient", None)
        return bool(settings and settings.active and context.active_object)

    def setup(self, context):
        # A fixed pool, hidden as needed: this never mutates the gizmo
        # collection at runtime.
        self.handle_gizmos = []
        for index in range(MAX_HANDLES):
            gizmo = self.gizmos.new("GIZMO_GT_move_3d")
            gizmo.draw_options = {'ALIGN_VIEW'}
            gizmo.alpha = 0.9
            gizmo.color_highlight = (1.0, 1.0, 1.0)
            gizmo.alpha_highlight = 1.0
            gizmo.scale_basis = 0.12
            get, set = _handle_accessors(index)
            gizmo.target_set_handler("offset", get=get, set=set)
            self.handle_gizmos.append(gizmo)
        self.refresh(context)

    def refresh(self, context):
        settings = context.scene.tk_gradient
        obj = context.active_object
        if obj is None:
            return

        # The gradient is the control for how many handles there are, and it
        # is a datablock with no update callback to hook into. Redrawing is the
        # one moment we are guaranteed after its add/remove buttons are used, so
        # both the floor and the handle sync ride on it. Both no-op when nothing
        # changed, so this costs a length check per redraw.
        changed = properties.normalise_ramp(settings)
        changed = properties.sync_handles_to_ramp(settings) or changed
        if changed:
            properties.write_weights(context, settings)

        count = min(len(settings.handles), MAX_HANDLES)
        colours = gradient.handle_colours(count, settings.invert)
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
