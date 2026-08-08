import bpy

from .operators import (
    TK_OT_add_twist_bones,
    TK_OT_toggle_pose_mode,
    TK_OT_validate_humanoid,
)


class TK_PT_rigging(bpy.types.Panel):
    bl_label = "Rigging"
    bl_idname = "TK_PT_rigging"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Toolkit"

    def draw(self, context):
        col = self.layout.column()
        # Same operator twice; the small side button keeps the current pose.
        row = col.row(align=True)
        row.operator(TK_OT_toggle_pose_mode.bl_idname, icon='POSE_HLT')
        row.operator(
            TK_OT_toggle_pose_mode.bl_idname, text="", icon='ARMATURE_DATA'
        ).reset = False
        col.operator(TK_OT_validate_humanoid.bl_idname, icon='ARMATURE_DATA')
        col.operator(TK_OT_add_twist_bones.bl_idname, icon='CON_ROTLIKE')


classes = (TK_PT_rigging,)
