import bpy
from bpy_extras.io_utils import ExportHelper

# Game-engine friendly defaults. The properties below take their default= from
# here, and the GAME_READY preset restores it, so the two cannot drift.
RECOMMENDED = {
    "use_selection": True,
    "axis_forward": '-Z',
    "axis_up": 'Y',
    "mesh_smooth_type": 'FACE',
    "apply_scale_options": 'FBX_SCALE_ALL',
    "add_leaf_bones": False,
    "bake_anim": False,
}

AXES = [(a, a, "") for a in ('X', 'Y', 'Z', '-X', '-Y', '-Z')]


def _apply_preset(self, context):
    if self.preset == 'GAME_READY':
        for name, value in RECOMMENDED.items():
            setattr(self, name, value)


class TK_OT_export_game_fbx(bpy.types.Operator, ExportHelper):
    """Export the selection as an FBX with game-engine friendly settings"""

    bl_idname = "tk.export_game_fbx"
    bl_label = "Export FBX (Game Ready)"
    bl_options = {'REGISTER'}

    filename_ext = ".fbx"
    filter_glob: bpy.props.StringProperty(default="*.fbx", options={'HIDDEN'})

    preset: bpy.props.EnumProperty(
        name="Preset",
        description="Pick Game Ready to restore the recommended settings",
        items=[
            ('GAME_READY', "Game Ready", "Recommended defaults"),
            ('CUSTOM', "Custom", "Your own settings"),
        ],
        default='GAME_READY',
        update=_apply_preset,
    )
    use_selection: bpy.props.BoolProperty(
        name="Selected Objects",
        description="Export the selection only, rather than the whole scene",
        default=RECOMMENDED["use_selection"],
    )
    axis_forward: bpy.props.EnumProperty(
        name="Forward",
        items=AXES,
        default=RECOMMENDED["axis_forward"],
    )
    axis_up: bpy.props.EnumProperty(
        name="Up",
        items=AXES,
        default=RECOMMENDED["axis_up"],
    )
    apply_scale_options: bpy.props.EnumProperty(
        name="Apply Scalings",
        items=[
            ('FBX_SCALE_NONE', "All Local", "FBX scale stays at 1.0"),
            ('FBX_SCALE_UNITS', "FBX Units Scale", "Units scaling on FBX scale"),
            ('FBX_SCALE_CUSTOM', "FBX Custom Scale", "Custom scaling on FBX scale"),
            ('FBX_SCALE_ALL', "FBX All", "Custom and units scaling on FBX scale"),
        ],
        default=RECOMMENDED["apply_scale_options"],
    )
    mesh_smooth_type: bpy.props.EnumProperty(
        name="Smoothing",
        items=[
            ('OFF', "Normals Only", "Export only normals"),
            ('FACE', "Face", "Write face smoothing"),
            ('EDGE', "Edge", "Write edge smoothing"),
            ('SMOOTH_GROUP', "Smoothing Groups", "Write face smoothing groups"),
        ],
        default=RECOMMENDED["mesh_smooth_type"],
    )
    add_leaf_bones: bpy.props.BoolProperty(
        name="Add Leaf Bones",
        description="Append an extra tip bone to every chain end",
        default=RECOMMENDED["add_leaf_bones"],
    )
    bake_anim: bpy.props.BoolProperty(
        name="Bake Animation",
        description="Export baked keyframe animation",
        default=RECOMMENDED["bake_anim"],
    )

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "preset")

        col = layout.column(heading="Include")
        col.prop(self, "use_selection")

        col = layout.column()
        col.prop(self, "axis_forward")
        col.prop(self, "axis_up")
        col.prop(self, "apply_scale_options")
        col.prop(self, "mesh_smooth_type")

        col = layout.column(heading="Armature")
        col.prop(self, "add_leaf_bones")
        col.prop(self, "bake_anim")

    def execute(self, context):
        bpy.ops.export_scene.fbx(
            filepath=self.filepath,
            use_selection=self.use_selection,
            axis_forward=self.axis_forward,
            axis_up=self.axis_up,
            mesh_smooth_type=self.mesh_smooth_type,
            add_leaf_bones=self.add_leaf_bones,
            apply_scale_options=self.apply_scale_options,
            bake_anim=self.bake_anim,
        )
        self.report({'INFO'}, f"Exported to {self.filepath}")
        return {'FINISHED'}


classes = (TK_OT_export_game_fbx,)
