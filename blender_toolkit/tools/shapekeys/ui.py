import bpy

from .operators import TK_OT_apply_modifiers_shapekeys, TK_OT_split_shapekey


class TK_PT_shapekeys(bpy.types.Panel):
    bl_label = "Shapekeys"
    bl_idname = "TK_PT_shapekeys"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Toolkit"

    def draw(self, context):
        col = self.layout.column()
        col.operator(TK_OT_apply_modifiers_shapekeys.bl_idname, icon='MODIFIER')
        col.operator(TK_OT_split_shapekey.bl_idname, icon='MOD_MIRROR')


classes = (TK_PT_shapekeys,)
