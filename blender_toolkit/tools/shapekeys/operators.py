import bpy

from ...utils import ensure_mode

LEFT_GROUP = "Left"
RIGHT_GROUP = "Right"


def _activate(context, obj):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _apply_all_modifiers(obj):
    for modifier in list(obj.modifiers):
        bpy.ops.object.modifier_apply(modifier=modifier.name)


class TK_OT_apply_modifiers_shapekeys(bpy.types.Operator):
    """Apply every modifier on the active object, keeping its shapekeys intact"""

    bl_idname = "tk.apply_modifiers_shapekeys"
    bl_label = "Apply Modifiers (Keep Shapekeys)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == 'MESH'
            and len(obj.modifiers) > 0
            and obj.data.shape_keys is not None
        )

    def execute(self, context):
        obj = context.active_object

        # Join as Shapes needs matching vertex counts, so a topology-changing
        # modifier would silently produce garbage shapekeys. Refuse instead.
        evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
        eval_mesh = evaluated.to_mesh()
        topology_changed = len(eval_mesh.vertices) != len(obj.data.vertices)
        evaluated.to_mesh_clear()
        if topology_changed:
            self.report(
                {'ERROR'},
                "Modifiers change the vertex count - shapekeys cannot survive. "
                "Apply or remove those modifiers manually.",
            )
            return {'CANCELLED'}

        keys = obj.data.shape_keys.key_blocks
        # Skip the Basis; it becomes the base mesh once the keys are stripped.
        snapshot = [
            (k.name, k.value, k.slider_min, k.slider_max, k.vertex_group) for k in keys[1:]
        ]

        with ensure_mode(context, 'OBJECT'):
            duplicates = []
            for index, (name, *_rest) in enumerate(snapshot, start=1):
                _activate(context, obj)
                bpy.ops.object.duplicate(linked=False)
                dup = context.active_object
                dup.name = f"__tk_shapekey_{name}"
                for i, key in enumerate(dup.data.shape_keys.key_blocks):
                    key.value = 1.0 if i == index else 0.0
                    key.slider_min, key.slider_max = 0.0, 1.0
                bpy.ops.object.shape_key_remove(all=True, apply_mix=True)
                _apply_all_modifiers(dup)
                duplicates.append(dup)

            _activate(context, obj)
            bpy.ops.object.shape_key_remove(all=True)
            _apply_all_modifiers(obj)

            for dup, (name, value, slider_min, slider_max, vertex_group) in zip(
                duplicates, snapshot
            ):
                dup.select_set(True)
                bpy.ops.object.join_shapes()
                dup.select_set(False)
                new_key = obj.data.shape_keys.key_blocks[-1]
                new_key.name = name
                new_key.slider_min, new_key.slider_max = slider_min, slider_max
                new_key.value = value
                new_key.vertex_group = vertex_group

            for dup in duplicates:
                bpy.data.objects.remove(dup, do_unlink=True)

            _activate(context, obj)

        self.report({'INFO'}, f"Applied modifiers, kept {len(snapshot)} shapekeys")
        return {'FINISHED'}


class TK_OT_split_shapekey(bpy.types.Operator):
    """Split the active shapekey into _L and _R halves masked by vertex groups"""

    bl_idname = "tk.split_shapekey"
    bl_label = "Split Shapekey L/R"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == 'MESH'
            and obj.data.shape_keys is not None
            and obj.active_shape_key_index > 0
        )

    def execute(self, context):
        obj = context.active_object
        missing = [g for g in (LEFT_GROUP, RIGHT_GROUP) if g not in obj.vertex_groups]
        if missing:
            self.report({'ERROR'}, f"Missing vertex group(s): {', '.join(missing)}")
            return {'CANCELLED'}

        source = obj.active_shape_key
        with ensure_mode(context, 'OBJECT'):
            for suffix, group in (("_L", LEFT_GROUP), ("_R", RIGHT_GROUP)):
                new_key = obj.shape_key_add(name=f"{source.name}{suffix}", from_mix=False)
                for target, original in zip(new_key.data, source.data):
                    target.co = original.co
                new_key.vertex_group = group
                new_key.slider_min = source.slider_min
                new_key.slider_max = source.slider_max

        self.report({'INFO'}, f"Split '{source.name}' into _L / _R")
        return {'FINISHED'}


classes = (TK_OT_apply_modifiers_shapekeys, TK_OT_split_shapekey)
