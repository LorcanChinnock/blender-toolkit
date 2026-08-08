import re

import bpy

# Most specific first: a bone is claimed by the first part it matches, so
# "lowerarm" never gets counted as the "arm" fallback for upperarm.
PART_ALIASES = (
    ("hand", ("hand",)),
    ("lowerarm", ("lowerarm", "forearm", "armlower")),
    ("upperarm", ("upperarm", "armupper", "arm")),
    ("foot", ("foot", "ankle")),
    ("thigh", ("thigh", "upperleg", "legupper", "upleg")),
    ("calf", ("calf", "shin", "lowerleg", "leglower", "leg")),
    ("head", ("head",)),
    ("neck", ("neck",)),
    ("chest", ("chest", "ribcage", "torso", "spine1", "spine2")),
    ("hips", ("hips", "hip", "pelvis")),
    ("spine", ("spine", "abdomen")),
)

CENTER_PARTS = ("hips", "spine", "chest", "neck", "head")
LIMB_PARTS = ("upperarm", "lowerarm", "hand", "thigh", "calf", "foot")

_SIDE_LETTER = re.compile(r"(^|[._\- :])([lr])([._\- :]|$)")


def _side(name):
    """'L', 'R' or None, from Rigify/Unity/Mixamo style naming."""
    low = name.lower()
    if "left" in low:
        return 'L'
    if "right" in low:
        return 'R'
    match = _SIDE_LETTER.search(low)
    return match.group(2).upper() if match else None


def _part(name):
    """Which humanoid part a bone name refers to, or None."""
    flat = "".join(c for c in name.lower() if c.isalnum())
    for part, aliases in PART_ALIASES:
        if any(alias in flat for alias in aliases):
            return part
    return None


def missing_humanoid_bones(bones):
    """Humanoid parts absent from `bones`; limbs are reported per side."""
    found = set()
    for bone in bones:
        part = _part(bone.name)
        if part is None:
            continue
        found.add(part if part in CENTER_PARTS else (part, _side(bone.name)))

    missing = [p for p in CENTER_PARTS if p not in found]
    missing += [
        f"{part}.{side}"
        for part in LIMB_PARTS
        for side in ('L', 'R')
        if (part, side) not in found
    ]
    return missing


class TK_OT_validate_humanoid(bpy.types.Operator):
    """Check the active armature for the bones a humanoid rig needs"""

    bl_idname = "tk.validate_humanoid"
    bl_label = "Validate Humanoid"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE'

    def execute(self, context):
        missing = missing_humanoid_bones(context.active_object.data.bones)
        if missing:
            self.report({'WARNING'}, f"Missing bones: {', '.join(missing)}")
        else:
            self.report({'INFO'}, "Humanoid rig complete")
        return {'FINISHED'}


class TK_OT_add_twist_bones(bpy.types.Operator):
    """Add half-length twist bones to the selected bones"""

    bl_idname = "tk.add_twist_bones"
    bl_label = "Add Twist Bones"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_ARMATURE' and bool(context.selected_editable_bones)

    def execute(self, context):
        obj = context.active_object
        edit_bones = obj.data.edit_bones

        pairs = []
        for bone in list(context.selected_editable_bones):
            if bone.name.startswith("twist_"):
                continue
            twist = edit_bones.new(f"twist_{bone.name}")
            twist.head = bone.head
            twist.tail = bone.head.lerp(bone.tail, 0.5)
            twist.roll = bone.roll
            twist.parent = bone
            twist.use_connect = False
            pairs.append((twist.name, bone.name))

        if not pairs:
            self.report({'WARNING'}, "Nothing to do - only twist bones selected")
            return {'CANCELLED'}

        # edit_bones die on mode switch, hence the name pairs above.
        bpy.ops.object.mode_set(mode='POSE')
        for twist_name, source_name in pairs:
            constraint = obj.pose.bones[twist_name].constraints.new('COPY_ROTATION')
            constraint.target = obj
            constraint.subtarget = source_name
            constraint.use_x = False
            constraint.use_y = True
            constraint.use_z = False
            constraint.owner_space = 'LOCAL'
            constraint.target_space = 'LOCAL'
            constraint.influence = 0.5
        bpy.ops.object.mode_set(mode='EDIT')

        self.report({'INFO'}, f"Added {len(pairs)} twist bone(s)")
        return {'FINISHED'}


class TK_OT_toggle_pose_mode(bpy.types.Operator):
    """Toggle pose mode on the active armature, clearing the pose both ways"""

    bl_idname = "tk.toggle_pose_mode"
    bl_label = "Pose Mode"
    bl_options = {'REGISTER', 'UNDO'}

    reset: bpy.props.BoolProperty(
        name="Reset Pose",
        description="Clear every bone's transform on entering and leaving",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE'

    def execute(self, context):
        obj = context.active_object
        leaving = obj.mode == 'POSE'

        if self.reset:
            # Not pose.transforms_clear: that needs pose mode and only touches
            # the selection.
            for bone in obj.pose.bones:
                bone.matrix_basis.identity()

        bpy.ops.object.mode_set(mode='OBJECT' if leaving else 'POSE')

        direction = "Left" if leaving else "Entered"
        suffix = " (pose reset)" if self.reset else ""
        self.report({'INFO'}, f"{direction} pose mode{suffix}")
        return {'FINISHED'}


classes = (TK_OT_validate_humanoid, TK_OT_add_twist_bones, TK_OT_toggle_pose_mode)
