import bpy

from .operators import TK_OT_export_game_fbx


class TK_PT_export(bpy.types.Panel):
    bl_label = "Export"
    bl_idname = "TK_PT_export"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Toolkit"

    def draw(self, context):
        col = self.layout.column()
        col.operator(TK_OT_export_game_fbx.bl_idname, icon='EXPORT')


classes = (TK_PT_export,)
