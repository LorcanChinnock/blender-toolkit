import bpy

from .tools.export.operators import TK_OT_export_game_fbx
from .tools.retopo.operators import TK_OT_retopo_setup
from .tools.rigging.operators import TK_OT_add_twist_bones, TK_OT_validate_humanoid
from .tools.shapekeys.operators import (
    TK_OT_apply_modifiers_shapekeys,
    TK_OT_split_shapekey,
)
from .tools.weights.operators import TK_OT_add_gradient, TK_OT_distribute_handles
from .tools.weights.properties import active_gradient
from .utils import prefs


class TK_MT_pie_main(bpy.types.Menu):
    bl_label = "Toolkit"
    bl_idname = "TK_MT_pie_main"

    def draw(self, context):
        pie = self.layout.menu_pie()
        settings = prefs(context)

        # Pie slots fill in the order W, E, S, N, ... so exactly two entries go
        # in before the exporter to keep it pinned to the bottom (S) slot.
        if context.mode == 'SCULPT' and settings.use_retopo:
            pie.operator(TK_OT_retopo_setup.bl_idname, icon='MOD_MESHDEFORM')
            pie.separator()
        elif context.mode in {'OBJECT', 'EDIT_MESH'} and settings.use_shapekeys:
            pie.operator(TK_OT_apply_modifiers_shapekeys.bl_idname, icon='MODIFIER')
            pie.operator(TK_OT_split_shapekey.bl_idname, icon='MOD_MIRROR')
        elif context.mode == 'PAINT_WEIGHT' and settings.use_weights:
            pie.operator(TK_OT_add_gradient.bl_idname, icon='ADD')
            if active_gradient(context.active_object) is not None:
                pie.operator(
                    TK_OT_distribute_handles.bl_idname, icon='ARROW_LEFTRIGHT'
                ).mode = 'WEIGHTS'
            else:
                pie.separator()
        elif context.mode in {'POSE', 'EDIT_ARMATURE'} and settings.use_rigging:
            pie.operator(TK_OT_validate_humanoid.bl_idname, icon='ARMATURE_DATA')
            pie.operator(TK_OT_add_twist_bones.bl_idname, icon='CON_ROTLIKE')
        else:
            pie.separator()
            pie.separator()

        if settings.use_export:
            pie.operator(TK_OT_export_game_fbx.bl_idname, icon='EXPORT')


classes = (TK_MT_pie_main,)
addon_keymaps = []


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is None:  # background mode has no addon keyconfig
        return
    keymap = keyconfig.keymaps.new(name="3D View", space_type='VIEW_3D')
    item = keymap.keymap_items.new(
        "wm.call_menu_pie", 'Q', 'PRESS', shift=True, alt=True
    )
    item.properties.name = TK_MT_pie_main.bl_idname
    addon_keymaps.append((keymap, item))


def unregister():
    for keymap, item in addon_keymaps:
        keymap.keymap_items.remove(item)
    addon_keymaps.clear()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
