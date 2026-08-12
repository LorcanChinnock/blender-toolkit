import bpy

from .operators import TK_OT_validate_mesh


class TK_PT_mesh(bpy.types.Panel):
    bl_label = "Mesh"
    bl_idname = "TK_PT_mesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Toolkit"

    def draw(self, context):
        col = self.layout.column()
        col.operator(TK_OT_validate_mesh.bl_idname, icon='CHECKMARK')


classes = (TK_PT_mesh,)
