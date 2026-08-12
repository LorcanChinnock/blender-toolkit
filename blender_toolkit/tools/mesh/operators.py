import bmesh
import bpy

from . import checks


class TK_OT_validate_mesh(bpy.types.Operator):
    """Check the active mesh for problem geometry and select what it finds"""

    bl_idname = "tk.validate_mesh"
    bl_label = "Validate Mesh"
    # Unlike tk.validate_humanoid this changes the selection, so it is undoable.
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == 'MESH'
            and context.mode in {'OBJECT', 'EDIT_MESH'}
        )

    def execute(self, context):
        if context.mode != 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='DESELECT')

        mesh = context.active_object.data
        bm = bmesh.from_edit_mesh(mesh)
        found = checks.run(bm)

        for _, elements in found:
            for element in elements:
                element.select_set(True)
        # Flush by the user's select mode rather than forcing one on them.
        bm.select_flush_mode()
        bmesh.update_edit_mesh(mesh)

        problems = [(label, elements) for label, elements in found if elements]
        if problems:
            counts = ", ".join(
                f"{len(elements)} {label}" for label, elements in problems
            )
            self.report({'WARNING'}, counts)
        else:
            self.report({'INFO'}, "Mesh is clean")
        return {'FINISHED'}


classes = (TK_OT_validate_mesh,)
