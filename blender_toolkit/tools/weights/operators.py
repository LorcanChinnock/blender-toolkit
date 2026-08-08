import bmesh
import bpy
from mathutils import Vector

from ...utils import ensure_mode
from . import gradient, overlay, properties, snapping


def _selection_points(context, obj):
    """End is the active vertex, start the centroid of the rest of the selection.

    Covers both "pick two vertices" and "pick a loop, then the vertex the
    gradient should reach".
    """
    if context.mode != 'EDIT_MESH':
        return None, "Select the gradient's vertices in Edit mode first"

    bm = bmesh.from_edit_mesh(obj.data)
    active = bm.select_history.active
    if not isinstance(active, bmesh.types.BMVert):
        return None, "No active vertex - click the vertex the gradient should end on"

    others = [v.co for v in bm.verts if v.select and v != active]
    if not others:
        return None, "Select at least two vertices"

    start = sum(others, Vector()) / len(others)
    return (start, active.co.copy()), None


def _bounds_points(obj, axis):
    """A gradient spanning the mesh along one axis.

    Measured from obj.data.vertices, not obj.bound_box: the bounding box comes
    from the evaluated object, so an active shape key or a modifier shifts it
    away from the base coordinates the weights are actually computed from.

    Falls back to the longest axis when the requested one is flat, so a plane
    gives a usable gradient instead of "start and end are the same point".
    """
    coords = [v.co for v in obj.data.vertices]
    if not coords:
        return Vector((0.0, 0.0, 0.0)), Vector((1.0, 0.0, 0.0))

    low = [min(c[i] for c in coords) for i in range(3)]
    high = [max(c[i] for c in coords) for i in range(3)]
    spans = [high[i] - low[i] for i in range(3)]

    index = "XYZ".index(axis)
    if spans[index] <= 1e-6:
        index = max(range(3), key=lambda i: spans[i])

    middle = Vector(((low[i] + high[i]) * 0.5 for i in range(3)))
    start, end = middle.copy(), middle.copy()
    start[index], end[index] = low[index], high[index]
    return start, end


def _seed_points(operator, context, obj):
    """The two points to start from. Returns (points, error, description).

    AUTO is the default because needing an Edit-mode selection before the button
    does anything is friction for the common case: a left-to-right gradient
    across the whole object.
    """
    if operator.source == 'KEEP':
        return None, None, "kept"

    if operator.source in {'AUTO', 'SELECTION'}:
        points, error = _selection_points(context, obj)
        if points is not None:
            return points, None, "selection"
        if operator.source == 'SELECTION':
            return None, error, None

    return _bounds_points(obj, operator.axis), None, f"{operator.axis} bounds"


def set_handles(settings, points):
    """Replace the path with the given points, spread evenly across the ramp."""
    settings.handles.clear()
    last = max(len(points) - 1, 1)
    for index, point in enumerate(points):
        handle = settings.handles.add()
        handle.position = point
        handle.t = index / last
    settings.active_handle = 0


class _SourceMixin:
    """Where the two points come from, shared by the one-shot and Start."""

    source: bpy.props.EnumProperty(
        name="Points From",
        items=[
            ('AUTO', "Auto",
             "The Edit-mode selection if there is a usable one, else the "
             "object bounds"),
            ('SELECTION', "Selection", "Active vertex ends it, the rest start it"),
            ('BOUNDS', "Object Bounds", "Span the bounding box along an axis"),
            ('KEEP', "Keep Current", "Leave the handles where they are"),
        ],
        default='AUTO',
    )
    axis: bpy.props.EnumProperty(
        name="Axis",
        description="Bounding box axis to span when there is no selection to use",
        items=[(a, a, "") for a in ('X', 'Y', 'Z')],
        default='X',
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'


class TK_OT_write_gradient(_SourceMixin, bpy.types.Operator):
    """Write a spatial gradient into a vertex group in one shot, no session

    The scripting entry point. The panel offers tk.start_gradient instead.
    """

    bl_idname = "tk.write_gradient"
    bl_label = "Write Weight Gradient"
    bl_options = {'REGISTER', 'UNDO'}

    shape: bpy.props.EnumProperty(name="Shape", items=gradient.SHAPES, default='LINEAR')
    profile: bpy.props.EnumProperty(
        name="Profile", items=gradient.PROFILES, default='LINEAR'
    )
    midpoint: bpy.props.FloatProperty(
        name="Midpoint", default=0.5, min=0.0, max=1.0, subtype='FACTOR'
    )
    invert: bpy.props.BoolProperty(name="Invert", default=False)
    smooth_repeat: bpy.props.IntProperty(name="Smooth", default=0, min=0, max=20)
    group_name: bpy.props.StringProperty(name="Group", default="Group")
    mask_group: bpy.props.StringProperty(name="Mask")
    start: bpy.props.FloatVectorProperty(name="Start", subtype='TRANSLATION')
    end: bpy.props.FloatVectorProperty(
        name="End", subtype='TRANSLATION', default=(1.0, 0.0, 0.0)
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "group_name")
        layout.prop_search(self, "mask_group", context.active_object, "vertex_groups")
        layout.separator()
        layout.prop(self, "source")
        if self.source == 'BOUNDS':
            layout.prop(self, "axis")
        elif self.source == 'KEEP':
            layout.prop(self, "start")
            layout.prop(self, "end")
        layout.separator()
        for name in ("shape", "profile", "midpoint", "invert", "smooth_repeat"):
            layout.prop(self, name)

    def execute(self, context):
        obj = context.active_object
        seeded, error, _origin = _seed_points(self, context, obj)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        if seeded is not None:
            self.start, self.end = seeded
        # Resolved points become plain numbers so the redo panel can nudge them.
        self.source = 'KEEP'

        if Vector(self.start) == Vector(self.end):
            self.report({'ERROR'}, "Start and end are the same point")
            return {'CANCELLED'}

        with ensure_mode(context, 'OBJECT'):
            properties.write_weights(
                context, self, points=[tuple(self.start), tuple(self.end)]
            )

        self.report({'INFO'}, f"Wrote gradient into '{self.group_name}'")
        return {'FINISHED'}


class _SessionMixin:
    """Only meaningful while a session is running on the active object."""

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.tk_gradient.active


class TK_OT_start_gradient(_SourceMixin, bpy.types.Operator):
    """Build vertex group weights from a spatial gradient, adjusted live

    Drops into weight paint with draggable handles. Add keeps a group and
    stays open for the next one; Close returns you to the mode you were in.
    """

    bl_idname = "tk.start_gradient"
    bl_label = "Weight Gradient"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        settings = context.active_object.tk_gradient

        # Every session opens clean: last round's shape, mask and ramp are not
        # what you want on the next group, and neither is its name. Pick a group
        # from the panel to edit one that already exists - that loads its saved
        # gradient - rather than inheriting whatever was left over.
        properties.reset_settings(settings)
        settings.group_name = properties.unused_group_name(obj)

        seeded, error, origin = _seed_points(self, context, obj)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        if seeded is not None:
            set_handles(settings, seeded)
        if len(settings.handles) < 2:
            self.report({'ERROR'}, "The gradient needs at least two handles")
            return {'CANCELLED'}
        if Vector(settings.handles[0].position) == Vector(settings.handles[-1].position):
            self.report({'ERROR'}, "Start and end are the same point")
            return {'CANCELLED'}

        properties.ensure_ramp(settings)
        settings.previous_mode = obj.mode

        if obj.mode != 'WEIGHT_PAINT':
            bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
        settings.active = True
        properties.write_weights(context, settings)  # snapshots as it goes
        if settings.group_name in obj.vertex_groups:
            obj.vertex_groups.active = obj.vertex_groups[settings.group_name]
        overlay.enable()

        # Say which it used: with AUTO, "selection" vs "X bounds" is the
        # difference between what you picked and a sensible default.
        self.report({'INFO'}, f"Gradient session started from {origin}")
        return {'FINISHED'}


def _end_session(context, obj):
    settings = obj.tk_gradient
    settings.active = False
    overlay.disable()
    if obj.mode != settings.previous_mode:
        bpy.ops.object.mode_set(mode=settings.previous_mode)


class TK_OT_add_gradient(_SessionMixin, bpy.types.Operator):
    """Keep this group and stay in the session to build another

    Rename the group, adjust the gradient - invert it for the opposite side -
    and Add again. The panel stays open the whole time.
    """

    bl_idname = "tk.add_gradient"
    bl_label = "Add"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and bool(
            context.active_object.tk_gradient.group_name
        )

    def execute(self, context):
        obj = context.active_object
        settings = obj.tk_gradient
        # Anything still pending has to land before it is committed: writes are
        # deferred to the session timer, and Add can arrive inside that window.
        properties.flush(context, settings)

        # Committing means dropping the way back: from here on Cancel has
        # nothing to take back for the groups written so far.
        name = settings.group_name
        properties.save_record(obj, settings, name)
        properties.forget(obj)
        self.report(
            {'INFO'}, f"Kept '{name}' and saved its gradient - rename and Add "
            "again for another"
        )
        return {'FINISHED'}


class TK_OT_cancel_gradient(_SessionMixin, bpy.types.Operator):
    """Close the session, undoing anything not yet added

    Groups you already hit Add on are kept; only the gradient in progress is
    rolled back.
    """

    bl_idname = "tk.cancel_gradient"
    bl_label = "Close"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        properties.restore(obj)
        _end_session(context, obj)
        # Cancel leaves no trace. Start resets too, so this is belt and braces -
        # but it also means the panel is not showing stale settings in between.
        properties.reset_settings(obj.tk_gradient)
        self.report({'INFO'}, "Gradient cancelled")
        return {'FINISHED'}


classes = (
    TK_OT_write_gradient,
    TK_OT_start_gradient,
    TK_OT_add_gradient,
    TK_OT_cancel_gradient,
)
