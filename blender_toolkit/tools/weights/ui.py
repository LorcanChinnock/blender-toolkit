import bpy

from . import properties
from .gradient import PATH_SHAPES
from .operators import TK_OT_add_gradient, TK_OT_distribute_handles


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

        # No vertex group list here: Object Data Properties already has one, and
        # a second copy is a second thing to keep in step. The panel works on
        # whichever group is active there, and says which that is.
        active = obj.vertex_groups.active
        if active is not None:
            layout.label(text=active.name, icon='GROUP_VERTEX')

        settings = properties.active_gradient(obj)
        if settings is None:
            layout.operator(
                TK_OT_add_gradient.bl_idname,
                icon='MOD_VERTEX_WEIGHT',
                text="Add Gradient",
            )
            return

        row = layout.row()
        row.use_property_split = True
        row.prop(obj, "tk_gradient_snap")

        header, body = layout.panel("tk_gradient_falloff")
        header.label(text="Falloff")
        if body is not None:
            body.use_property_split = True
            body.prop(settings, "shape")
            if settings.shape in PATH_SHAPES:
                body.prop(settings, "curved")
            body.prop(settings, "invert")
            body.prop(settings, "smooth_repeat")
            body.prop(settings, "blend")
            body.prop_search(settings, "mask_group", obj, "vertex_groups")

        header, body = layout.panel("tk_gradient_weights")
        header.label(text="Weights")
        if body is not None:
            # The one weight editor. Its + and - are how handles are added and
            # removed, and its Pos field is how an exact weight is typed - there
            # is deliberately no second list of the same numbers beside it.
            if settings.ramp is not None:
                body.template_color_ramp(settings.ramp, "color_ramp", expand=True)

            # One operator, one entry per mode - the mesh.select_all idiom.
            # Weights and positions are independent, so separate presses.
            row = body.row(align=True)
            for mode, label, icon in (
                ('WEIGHTS', "Weights", 'ARROW_LEFTRIGHT'),
                ('POSITIONS', "Space", 'MOD_ARRAY'),
                ('RELAX', "Relax", 'MOD_SMOOTH'),
            ):
                row.operator(
                    TK_OT_distribute_handles.bl_idname, text=label, icon=icon
                ).mode = mode
            body.use_property_split = True
            body.prop(settings, "profile")

        # A state, not a warning, and there is no button to point at: painting
        # on the group is what ends the gradient, the way Blender's redo panel
        # closes the moment you do anything else.
        group = obj.vertex_groups.get(settings.group_name)
        if group is not None and group.lock_weight:
            layout.label(text="Locked - the gradient is paused", icon='LOCKED')
        else:
            layout.label(text="Painting here detaches the gradient", icon='INFO')


classes = (TK_PT_weights,)
