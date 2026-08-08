import bpy
from bpy_extras.io_utils import ExportHelper


class TK_OT_export_game_fbx(bpy.types.Operator, ExportHelper):
    """Export the selection as an FBX with game-engine friendly settings"""

    bl_idname = "tk.export_game_fbx"
    bl_label = "Export FBX (Game Ready)"
    bl_options = {'REGISTER'}

    filename_ext = ".fbx"
    filter_glob: bpy.props.StringProperty(default="*.fbx", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        bpy.ops.export_scene.fbx(
            filepath=self.filepath,
            use_selection=True,
            axis_forward='-Z',
            axis_up='Y',
            mesh_smooth_type='FACE',
            add_leaf_bones=False,
            apply_scale_options='FBX_SCALE_ALL',
            bake_anim=False,
        )
        self.report({'INFO'}, f"Exported to {self.filepath}")
        return {'FINISHED'}


classes = (TK_OT_export_game_fbx,)
