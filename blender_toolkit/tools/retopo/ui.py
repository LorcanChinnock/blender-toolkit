import bpy

from .operators import TK_OT_retopo_setup


class TK_PT_retopo(bpy.types.Panel):
    bl_label = "Retopology"
    bl_idname = "TK_PT_retopo"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Toolkit"

    def draw(self, context):
        col = self.layout.column()
        col.operator(TK_OT_retopo_setup.bl_idname, icon='MOD_MESHDEFORM')


classes = (TK_PT_retopo,)
