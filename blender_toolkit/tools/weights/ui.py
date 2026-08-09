import bpy

from . import properties
from .gradient import PATH_SHAPES
from .operators import (
    TK_OT_add_gradient,
    TK_OT_cancel_gradient,
    TK_OT_distribute_handle_weights,
    TK_OT_finish_gradient,
    TK_OT_start_gradient,
)


class TK_PT_weights(bpy.types.Panel):
    bl_label = "Weights"
    bl_idname = "TK_PT_weights"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Toolkit"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            layout.label(text="Select a mesh", icon='INFO')
            return

        settings = obj.tk_gradient
        if not settings.active:
            # One entry point. tk.write_gradient is the same thing without a
            # session, kept for scripting rather than shown as a rival button.
            layout.operator(TK_OT_start_gradient.bl_idname, icon='MOD_VERTEX_WEIGHT')
            return

        layout.label(text="Weight Gradient", icon='MOD_VERTEX_WEIGHT')

        # The group the gradient writes into comes first: it is the subject of
        # every control below, and of all three buttons.
        col = layout.column()
        col.use_property_split = True
        # A search field, not a plain text one: picking an existing group is how
        # you go back and edit one, and typing still names a new group.
        col.prop_search(
            settings, "group_name", obj, "vertex_groups", icon='GROUP_VERTEX'
        )
        if properties.has_record(obj, settings.group_name):
            col.label(text="Editing its saved gradient", icon='RECOVER_LAST')

        row = layout.row(align=True)
        row.operator(TK_OT_add_gradient.bl_idname, icon='ADD')
        row.operator(TK_OT_finish_gradient.bl_idname, icon='CHECKMARK')
        row.operator(TK_OT_cancel_gradient.bl_idname, icon='X')
        if settings.added_names:
            layout.label(text=f"Added: {settings.added_names}", icon='CHECKMARK')

        layout.use_property_split = True
        col = layout.column()
        col.prop_search(settings, "mask_group", obj, "vertex_groups")
        col.prop(settings, "shape")
        col.prop(settings, "snap")
        col.prop(settings, "invert")
        if settings.shape in PATH_SHAPES:
            col.prop(settings, "curved")
        col.prop(settings, "smooth_repeat")

        # Its + and - buttons are how handles are added and removed; the stops
        # are held greyscale because this picks a value, not a colour.
        if settings.ramp is not None:
            layout.template_color_ramp(settings.ramp, "color_ramp", expand=True)
        layout.operator(TK_OT_distribute_handle_weights.bl_idname, icon='ARROW_LEFTRIGHT')
        layout.prop(settings, "profile")

        if settings.shape in PATH_SHAPES:
            # Handles are placed by dragging them in the viewport, and their
            # number comes from the gradient above - one per stop.
            layout.label(
                text=f"{len(settings.handles)} handles - drag in the viewport, "
                "+ above adds one",
                icon='HANDLETYPE_VECTOR_VEC',
            )


classes = (TK_PT_weights,)
