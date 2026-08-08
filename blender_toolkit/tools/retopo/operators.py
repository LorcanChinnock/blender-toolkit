import bpy


class TK_OT_retopo_setup(bpy.types.Operator):
    """Create a new low-poly mesh set up to retopologise the active sculpt"""

    bl_idname = "tk.retopo_setup"
    bl_label = "Setup Retopo"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and context.mode in {'OBJECT', 'SCULPT'}

    def execute(self, context):
        source = context.active_object

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        mesh = bpy.data.meshes.new(f"{source.name}_retopo")
        retopo = bpy.data.objects.new(f"{source.name}_retopo", mesh)
        # Same collection as the sculpt so it does not land in the scene root.
        (source.users_collection or (context.scene.collection,))[0].objects.link(retopo)
        retopo.matrix_world = source.matrix_world
        retopo.show_in_front = True

        shrinkwrap = retopo.modifiers.new("Retopo Shrinkwrap", 'SHRINKWRAP')
        shrinkwrap.target = source
        shrinkwrap.wrap_method = 'PROJECT'
        shrinkwrap.use_negative_direction = True
        shrinkwrap.use_positive_direction = True
        shrinkwrap.show_on_cage = True

        tool_settings = context.scene.tool_settings
        tool_settings.use_snap = True
        # In 5.x "project individual elements" is its own snap mode and setting
        # it clears snap_elements_base, so face snapping is this line alone.
        tool_settings.snap_elements_individual = {'FACE_PROJECT'}
        tool_settings.use_snap_self = False

        bpy.ops.object.select_all(action='DESELECT')
        retopo.select_set(True)
        context.view_layer.objects.active = retopo
        bpy.ops.object.mode_set(mode='EDIT')

        self.report({'INFO'}, f"Retopo mesh '{retopo.name}' ready")
        return {'FINISHED'}


classes = (TK_OT_retopo_setup,)
