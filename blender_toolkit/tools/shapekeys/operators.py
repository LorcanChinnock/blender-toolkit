import bpy

from ...utils import ensure_mode


def _activate(context, obj):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _apply_all_modifiers(obj):
    for modifier in list(obj.modifiers):
        bpy.ops.object.modifier_apply(modifier=modifier.name)


def _bake(context, obj, index):
    """Duplicate obj showing only key `index`, with every modifier applied.

    Index 0 is the Basis, which is what the object itself becomes.
    """
    _activate(context, obj)
    bpy.ops.object.duplicate(linked=False)
    dup = context.active_object
    for i, key in enumerate(dup.data.shape_keys.key_blocks):
        key.value = 1.0 if i == index else 0.0
        key.slider_min, key.slider_max = 0.0, 1.0
    bpy.ops.object.shape_key_remove(all=True, apply_mix=True)
    _apply_all_modifiers(dup)
    return dup


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

        keys = obj.data.shape_keys.key_blocks
        # Skip the Basis; it becomes the base mesh once the keys are stripped.
        snapshot = [
            (k.name, k.value, k.slider_min, k.slider_max, k.vertex_group) for k in keys[1:]
        ]

        with ensure_mode(context, 'OBJECT'):
            # Bake every key on its own copy first. A modifier may change the
            # vertex count and still be fine - subsurf, mirror and solidify all
            # produce the same topology whatever the key does. What Join as
            # Shapes cannot survive is a count that *differs between the keys*,
            # which is what a geometry-dependent modifier (weld, decimate,
            # remesh, boolean) does. Nothing on the object is touched until
            # every copy is known to agree.
            basis = _bake(context, obj, 0)
            duplicates = [_bake(context, obj, i) for i in range(1, len(keys))]

            expected = len(basis.data.vertices)
            disagree = [
                name
                for (name, *_rest), dup in zip(snapshot, duplicates)
                if len(dup.data.vertices) != expected
            ]
            if disagree:
                for dup in [basis, *duplicates]:
                    bpy.data.objects.remove(dup, do_unlink=True)
                _activate(context, obj)
                self.report(
                    {'ERROR'},
                    "Modifiers give these shapekeys a different vertex count to "
                    f"the Basis: {', '.join(disagree)}. Apply or remove the "
                    "modifiers that rebuild geometry from its shape.",
                )
                return {'CANCELLED'}
            bpy.data.objects.remove(basis, do_unlink=True)

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
    """Split a shapekey into two, each weighted by one of a pair of vertex groups

    The weights are baked into the coordinates rather than left as a live
    ShapeKey.vertex_group mask, so the results can be split again.
    """

    bl_idname = "tk.split_shapekey"
    bl_label = "Split Shapekey"
    bl_options = {'REGISTER', 'UNDO'}

    key: bpy.props.StringProperty(
        name="Shapekey",
        description="Key to split. Empty uses the active one",
    )
    group_a: bpy.props.StringProperty(name="Group A", default="Left")
    group_b: bpy.props.StringProperty(name="Group B", default="Right")
    suffix_a: bpy.props.StringProperty(name="Suffix A", default="_L")
    suffix_b: bpy.props.StringProperty(name="Suffix B", default="_R")
    keep_source: bpy.props.BoolProperty(
        name="Keep Source",
        description="Leave the key that was split in place",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        # Not active_shape_key_index > 0: `key` can name any key, and removing a
        # source with keep_source off drops the active index back to the Basis.
        return (
            obj is not None
            and obj.type == 'MESH'
            and obj.data.shape_keys is not None
            and len(obj.data.shape_keys.key_blocks) > 1
        )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        obj = context.active_object
        layout = self.layout
        layout.use_property_split = True
        layout.prop_search(self, "key", obj.data.shape_keys, "key_blocks")
        layout.prop_search(self, "group_a", obj, "vertex_groups")
        layout.prop(self, "suffix_a")
        layout.prop_search(self, "group_b", obj, "vertex_groups")
        layout.prop(self, "suffix_b")
        layout.prop(self, "keep_source")

    def execute(self, context):
        obj = context.active_object
        keys = obj.data.shape_keys.key_blocks

        source = keys.get(self.key) if self.key else obj.active_shape_key
        if source is None:
            self.report({'ERROR'}, f"No shapekey named '{self.key}'")
            return {'CANCELLED'}
        if source == source.relative_key:
            self.report({'ERROR'}, f"'{source.name}' is a base key - nothing to split")
            return {'CANCELLED'}

        missing = [g for g in (self.group_a, self.group_b) if g not in obj.vertex_groups]
        if missing:
            self.report({'ERROR'}, f"Missing vertex group(s): {', '.join(missing)}")
            return {'CANCELLED'}

        reference = source.relative_key
        with ensure_mode(context, 'OBJECT'):
            # VertexGroup.weight() raises for vertices outside the group, so read
            # the memberships off the mesh instead.
            per_vertex = [
                {g.group: g.weight for g in v.groups} for v in obj.data.vertices
            ]
            for suffix, name in (
                (self.suffix_a, self.group_a),
                (self.suffix_b, self.group_b),
            ):
                group = obj.vertex_groups[name]
                new_key = obj.shape_key_add(name=f"{source.name}{suffix}", from_mix=False)
                for index, groups in enumerate(per_vertex):
                    weight = groups.get(group.index, 0.0)
                    base = reference.data[index].co
                    new_key.data[index].co = base + (source.data[index].co - base) * weight
                new_key.relative_key = reference
                new_key.slider_min = source.slider_min
                new_key.slider_max = source.slider_max

            if not self.keep_source:
                obj.shape_key_remove(source)

        self.report(
            {'INFO'}, f"Split into {self.suffix_a} / {self.suffix_b}"
        )
        return {'FINISHED'}


classes = (TK_OT_apply_modifiers_shapekeys, TK_OT_split_shapekey)

