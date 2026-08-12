import bmesh
import bpy
from mathutils import Vector

from ...utils import ensure_mode
from . import gradient, properties


def _selection_points(context, obj):
    """End is the active vertex, start the centroid of the rest of the selection.

    Covers both "pick two vertices" and "pick a loop, then the vertex the
    gradient should reach".
    """
    if context.mode != 'EDIT_MESH':
        return None, "Select some vertices in Edit Mode first"

    bm = bmesh.from_edit_mesh(obj.data)
    active = bm.select_history.active
    if not isinstance(active, bmesh.types.BMVert):
        return None, "No active vertex - click the one it should end on"

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


class _SourceMixin:
    """Where the two points come from, shared by the one-shot and Add."""

    source: bpy.props.EnumProperty(
        name="Points From",
        items=[
            ('AUTO', "Auto", "Use the selection if there is one, else the "
             "object bounds"),
            ('SELECTION', "Selection", "Run from the selected vertices to the "
             "active one"),
            ('BOUNDS', "Object Bounds", "Span the object along an axis"),
            ('KEEP', "Keep Current", "Leave the handles where they are"),
        ],
        default='AUTO',
    )
    axis: bpy.props.EnumProperty(
        name="Axis",
        description="Axis to span when using the object bounds",
        items=[(a, a, "") for a in ('X', 'Y', 'Z')],
        default='X',
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'


class TK_OT_write_gradient(_SourceMixin, bpy.types.Operator):
    """Write a weight gradient into a vertex group in one go"""

    # The scripting entry point: no gradient is kept afterwards, so there is
    # nothing to go back and adjust. The panel offers tk.add_gradient instead.

    bl_idname = "tk.write_gradient"
    bl_label = "Write Weight Gradient"
    bl_options = {'REGISTER', 'UNDO'}

    shape: bpy.props.EnumProperty(name="Shape", items=gradient.SHAPES, default='LINEAR')
    profile: bpy.props.EnumProperty(
        name="Profile", items=gradient.PROFILES, default='LINEAR'
    )
    midpoint: bpy.props.FloatProperty(
        name="Midpoint",
        description="Where along the ramp the weight reaches half",
        default=0.5, min=0.0, max=1.0, subtype='FACTOR',
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
            # A masked write blends towards what the group already held, and
            # that has to be read before the write, not after. Nothing here
            # outlives the call, so it is read straight into the argument.
            baseline = (
                properties.read_weights(obj, self.group_name)
                if self.mask_group else None
            )
            properties.write_weights(
                context, self, points=[tuple(self.start), tuple(self.end)],
                baseline=baseline,
            )

        self.report({'INFO'}, f"Wrote gradient into '{self.group_name}'")
        return {'FINISHED'}


class _ActiveMixin:
    """Only meaningful when a gradient on the active object is selected."""

    @classmethod
    def poll(cls, context):
        return properties.active_gradient(context.active_object) is not None


class TK_OT_add_gradient(_SourceMixin, bpy.types.Operator):
    """Build the active vertex group's weights from a gradient, replacing them"""

    # Starts from a copy of the last gradient added, and makes a group when none
    # is selected. A one-line docstring on purpose: Blender shows it verbatim,
    # newlines and all, so a wrapped paragraph reads ragged in the tooltip.

    bl_idname = "tk.add_gradient"
    bl_label = "Add Gradient"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            return False
        # One gradient per group, by construction: with one already on the
        # active group the panel shows its settings instead of this button.
        return properties.active_gradient(obj) is None

    def execute(self, context):
        obj = context.active_object
        properties.purge_orphans(obj)

        active = obj.vertex_groups.active
        if active is not None and active.lock_weight:
            # Reported rather than silently doing nothing: putting a generator
            # on a group declared untouchable is a contradiction, not a no-op.
            self.report({'ERROR'}, f"Vertex group '{active.name}' is locked")
            return {'CANCELLED'}

        seeded, error, origin = _seed_points(self, context, obj)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}

        source = obj.tk_gradients[-1] if len(obj.tk_gradients) else None
        entry = obj.tk_gradients.add()
        if source is not None:
            properties.copy_gradient(source, entry)
        else:
            properties.ensure_ramp(entry)

        if seeded is not None:
            properties.set_handles(entry, seeded)
        if len(entry.handles) < 2:
            obj.tk_gradients.remove(len(obj.tk_gradients) - 1)
            self.report({'ERROR'}, "Need at least two handles")
            return {'CANCELLED'}
        if Vector(entry.handles[0].position) == Vector(entry.handles[-1].position):
            obj.tk_gradients.remove(len(obj.tk_gradients) - 1)
            self.report({'ERROR'}, "Start and end are the same point")
            return {'CANCELLED'}

        active = obj.vertex_groups.active
        entry.group_name = (
            active.name if active is not None
            else properties.unused_group_name(obj)
        )
        entry.name = entry.group_name

        # Weight paint is where the weights it writes are visible, so being
        # dropped there is what you wanted. It does not switch back - there is
        # no session to come out of.
        if context.mode != 'PAINT_WEIGHT' and bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='WEIGHT_PAINT')

        properties.flush(context, entry)
        if entry.group_name in obj.vertex_groups:
            obj.vertex_groups.active = obj.vertex_groups[entry.group_name]

        self.report({'INFO'}, f"Added a gradient to '{entry.group_name}' from {origin}")
        return {'FINISHED'}


class TK_OT_distribute_handles(_ActiveMixin, bpy.types.Operator):
    """Even out the handles - what they weigh, or where they sit"""

    bl_idname = "tk.distribute_handles"
    bl_label = "Distribute Handles"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(
        name="Mode",
        items=[
            ('WEIGHTS', "Weights", "Space the weights evenly along the path"),
            ('POSITIONS', "Space", "Space the handles evenly along the path"),
            ('RELAX', "Relax", "Smooth kinks out of the path. Repeat for more"),
        ],
        default='WEIGHTS',
    )
    factor: bpy.props.FloatProperty(
        name="Factor",
        description="How far each handle moves",
        default=0.5, min=0.0, max=1.0, subtype='FACTOR',
    )
    repeat: bpy.props.IntProperty(
        name="Repeat", description="Number of passes", default=1, min=1, max=50
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "mode")
        if self.mode == 'RELAX':
            layout.prop(self, "factor")
            layout.prop(self, "repeat")

    def execute(self, context):
        settings = properties.active_gradient(context.active_object)
        if self.mode == 'WEIGHTS':
            properties.spread_weights(settings)
            properties.mirror_weights_to_ramp(settings)
        else:
            handles = [tuple(h.position) for h in settings.handles]
            moved = (
                gradient.spaced_positions(handles, settings.curved)
                if self.mode == 'POSITIONS'
                else gradient.relax_positions(handles, self.factor, self.repeat)
            )
            # Assigned in place rather than through set_handles, which respreads
            # the weights - the whole point here is to move one and not the other.
            for handle, position in zip(settings.handles, moved):
                handle.position = position
        properties.mark_dirty()
        return {'FINISHED'}


classes = (
    TK_OT_write_gradient,
    TK_OT_add_gradient,
    TK_OT_distribute_handles,
)
