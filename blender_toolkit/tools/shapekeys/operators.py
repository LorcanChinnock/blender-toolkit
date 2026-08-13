import bpy

from ...utils import ensure_mode
# The falloff maths, borrowed rather than restated: an axis split is a two-point
# linear gradient, which `gradient` already computes for a whole mesh at once.
# It is imported at module level rather than lazily because a property's enum
# items are needed when the class body runs. MODULE_NAMES lists shapekeys before
# weights, so on Reload Scripts this resolves against the session's cached copy
# of gradient - harmless here, because everything used off it is reached as an
# attribute, which cannot raise ImportError the way a renamed class would.
from ..weights import gradient
from . import preview


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
    """Split a shapekey into two halves, weighted by a mask and its complement

    One mask goes in and two keys come out, the second weighted 1 - w, so the
    halves always add back up to the key they came from. The weights are baked
    into the coordinates rather than left as a live ShapeKey.vertex_group mask,
    which holds one group and cannot stack - so the results can be split again.

    Suffix A goes on the half the mask covers. Along an axis that is the high
    end: +X for X, which is the side Blender's own .L convention means.
    """

    bl_idname = "tk.split_shapekey"
    bl_label = "Split Shapekey"
    bl_options = {'REGISTER', 'UNDO'}

    key: bpy.props.StringProperty(
        name="Shapekey",
        description="Key to split. Empty uses the active one",
    )
    mask_from: bpy.props.EnumProperty(
        name="Mask From",
        items=[
            ('AXIS', "Axis", "Split across a plane through the object origin"),
            ('GROUP', "Vertex Group", "Split by the weights of a vertex group"),
            ('SELECTION', "Selection", "Split by the selected vertices"),
        ],
        default='AXIS',
    )
    axis: bpy.props.EnumProperty(
        name="Axis",
        description="Axis the split plane cuts across",
        items=[(a, a, "") for a in ('X', 'Y', 'Z')],
        default='X',
    )
    offset: bpy.props.FloatProperty(
        name="Offset",
        description="Move the plane along the axis from the object origin",
        default=0.0, subtype='DISTANCE',
    )
    width: bpy.props.FloatProperty(
        name="Width",
        description="Width of the soft band across the plane. Zero splits hard",
        default=0.0, min=0.0, subtype='DISTANCE',
    )
    profile: bpy.props.EnumProperty(
        name="Profile", items=gradient.PROFILES, default='LINEAR'
    )
    group: bpy.props.StringProperty(
        name="Group",
        description="Group whose weights mask the split. The other half gets "
                    "what is left over",
    )
    smooth_repeat: bpy.props.IntProperty(
        name="Smooth",
        description="Average the mask with its neighbours, softening the seam",
        default=0, min=0, max=20,
    )
    suffix_a: bpy.props.StringProperty(name="Suffix A", default="_L")
    suffix_b: bpy.props.StringProperty(name="Suffix B", default="_R")
    keep_source: bpy.props.BoolProperty(
        name="Keep Source",
        description="Leave the key that was split in place",
        default=True,
    )
    show_mask: bpy.props.BoolProperty(
        name="Show Mask",
        description="Tint the mesh by the mask, in weight paint's colours. The "
                    "A half is shown on its own either way",
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
        # Runs straight away and leaves the settings to the redo panel, the way
        # Subdivide, Bevel and Separate all do. A props dialog owns the input
        # while it is open, so nothing it changes can be seen in the viewport -
        # and being able to watch the seam move is the point.
        #
        # The key is named rather than left empty so the redo panel says which
        # one is being split. Empty still means the active one for a scripted
        # call, which is what keeps that default worth having.
        active = context.active_object.active_shape_key
        if not self.key and active is not None:
            self.key = active.name
        return self.execute(context)

    def draw(self, context):
        obj = context.active_object
        layout = self.layout
        layout.use_property_split = True
        layout.prop_search(self, "key", obj.data.shape_keys, "key_blocks")
        layout.separator()
        layout.prop(self, "mask_from")
        if self.mask_from == 'AXIS':
            layout.prop(self, "axis")
            layout.prop(self, "offset")
            layout.prop(self, "width")
            if self.width > 0.0:
                layout.prop(self, "profile")
        elif self.mask_from == 'GROUP':
            layout.prop_search(self, "group", obj, "vertex_groups")
        layout.prop(self, "smooth_repeat")
        layout.separator()
        layout.prop(self, "suffix_a")
        layout.prop(self, "suffix_b")
        layout.prop(self, "keep_source")
        layout.prop(self, "show_mask")
        layout.separator()
        layout.operator(TK_OT_finish_split.bl_idname, icon='CHECKMARK')

    def _mask(self, obj):
        """The weight of every vertex in the half that gets suffix A.

        Returns (weights, error). Called in Object mode, so an Edit-mode
        selection has already been flushed onto the mesh.
        """
        import numpy as np

        mesh = obj.data
        count = len(mesh.vertices)

        if self.mask_from == 'GROUP':
            group = obj.vertex_groups.get(self.group)
            if group is None:
                return None, f"No vertex group named '{self.group}'"
            # VertexGroup.weight() raises for vertices outside the group, so read
            # the memberships off the mesh instead.
            weights = np.array(
                [
                    {g.group: g.weight for g in v.groups}.get(group.index, 0.0)
                    for v in mesh.vertices
                ]
            )
        elif self.mask_from == 'SELECTION':
            weights = np.empty(count)
            mesh.vertices.foreach_get("select", weights)
            covered = weights.sum()
            if covered == 0 or covered == count:
                return None, (
                    "Select the vertices that should go to "
                    f"'{self.suffix_a}', and leave the rest unselected"
                )
        else:
            coords = np.empty(count * 3)
            mesh.vertices.foreach_get("co", coords)
            # A hard split is a band of no width, which has no direction to ramp
            # along - raw_factors rejects it. The floor keeps the plane exact to
            # far more decimal places than a mesh coordinate carries.
            span = max(self.width, 1e-6) * 0.5
            index = "XYZ".index(self.axis)
            low, high = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
            low[index], high[index] = self.offset - span, self.offset + span
            raw = gradient.raw_factors(coords, [low, high], 'LINEAR')
            if self.profile == 'LINEAR':
                weights = raw  # value() is the identity for a linear profile
            else:
                # ponytail: scalar pass over the whole mesh. This runs once per
                # split, not once per mouse-move like the gradient tool's own
                # writes, so it never became worth vectorising the profiles.
                weights = np.array(
                    [gradient.value(t, profile=self.profile) for t in raw.tolist()]
                )

        if self.smooth_repeat:
            smoothed = gradient.smooth(
                dict(enumerate(weights.tolist())),
                [tuple(e.vertices) for e in mesh.edges],
                self.smooth_repeat,
            )
            weights = np.array([smoothed[i] for i in range(count)])
        return weights, None

    def execute(self, context):
        import numpy as np

        obj = context.active_object
        keys = obj.data.shape_keys.key_blocks

        source = keys.get(self.key) if self.key else obj.active_shape_key
        if source is None:
            self.report({'ERROR'}, f"No shapekey named '{self.key}'")
            return {'CANCELLED'}
        if source == source.relative_key:
            self.report({'ERROR'}, f"'{source.name}' is a base key - nothing to split")
            return {'CANCELLED'}
        if self.suffix_a == self.suffix_b:
            self.report({'ERROR'}, "The two suffixes have to differ")
            return {'CANCELLED'}

        reference = source.relative_key
        with ensure_mode(context, 'OBJECT'):
            weights, error = self._mask(obj)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}

            count = len(obj.data.vertices)
            base = np.empty(count * 3)
            reference.data.foreach_get("co", base)
            offsets = np.empty(count * 3)
            source.data.foreach_get("co", offsets)
            offsets -= base

            # 1 - w rather than gradient.value(invert=True): inverting negates
            # the weight for exactly this reason, so the two are the same number
            # and the subtraction is the one that says so.
            made = []
            for suffix, half in (
                (self.suffix_a, weights), (self.suffix_b, 1.0 - weights)
            ):
                new_key = obj.shape_key_add(name=f"{source.name}{suffix}", from_mix=False)
                new_key.data.foreach_set("co", base + offsets * np.repeat(half, 3))
                new_key.relative_key = reference
                new_key.slider_min = source.slider_min
                new_key.slider_max = source.slider_max
                made.append(new_key)

            source_name, source_value = source.name, source.value
            if not self.keep_source:
                obj.shape_key_remove(source)

            self._show(obj, weights, source_name, source_value, made)

        self.report(
            {'INFO'}, f"Split into {self.suffix_a} / {self.suffix_b}"
        )
        return {'FINISHED'}

    def _show(self, obj, weights, source_name, source_value, made):
        """Put the A half on screen on its own, and hand the mask to the preview.

        The user cannot preview this by dragging a slider themselves: every
        change in the redo panel undoes the split and runs it again, so the keys
        come back at zero and the drag is lost. It has to be set from here to
        survive a tweak.
        """
        keys = obj.data.shape_keys.key_blocks
        for block in keys:
            if block.name == source_name:
                block.value = 0.0
        made[0].value, made[1].value = 1.0, 0.0
        obj.active_shape_key_index = keys.find(made[0].name)
        preview.apply(
            obj, weights.tolist(), source_name, source_value,
            (made[0].name, made[1].name), tint=self.show_mask,
        )


class TK_OT_finish_split(bpy.types.Operator):
    """Put the split's preview away, keeping the keys it made

    Blender has no call that closes a redo panel - screen.redo_last only opens
    one, and context.active_operator is a C-side getter with no setter. What
    replaces a redo panel is the next operator that runs, so this button is both
    the way to end the preview and the only way to dismiss the panel holding it.
    Without it the panel and its tint outlast any amount of orbiting, because
    view navigation does not register as an operator.

    The values the preview set are put back rather than left: it turned the
    source key off and the A half up to show one side on its own, and that was
    never the user's arrangement of the sliders.
    """

    bl_idname = "tk.finish_split"
    bl_label = "Done"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return preview.stored() is not None

    def execute(self, context):
        state = preview.stored()
        preview.clear()

        obj = context.active_object
        keys = obj.data.shape_keys if obj and obj.type == 'MESH' else None
        if state is not None and keys is not None:
            blocks = keys.key_blocks
            source = blocks.get(state["source"])
            if source is not None:
                source.value = state["source_value"]
            for name in state["halves"]:
                half = blocks.get(name)
                if half is not None:
                    half.value = 0.0

        # The tint is gone from the data; ask for the redraw that shows it.
        screen = context.screen
        for area in screen.areas if screen else ():
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return {'FINISHED'}


classes = (
    TK_OT_apply_modifiers_shapekeys, TK_OT_split_shapekey, TK_OT_finish_split,
)

