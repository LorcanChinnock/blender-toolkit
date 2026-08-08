"""Headless smoke test.

    /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python tests/smoke.py
"""

import os
import sys
import tempfile
from types import SimpleNamespace

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blender_toolkit  # noqa: E402


def raises(operator, message, **kwargs):
    """bpy.ops raises RuntimeError when an operator reports {'ERROR'}."""
    try:
        operator(**kwargs)
    except RuntimeError as exc:
        assert message in str(exc), str(exc)
        return
    raise AssertionError(f"expected an error containing {message!r}")


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def add_cube(name="Cube"):
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    obj.name = name
    return obj


def test_retopo():
    reset()
    sculpt = add_cube("Sculpt")
    assert bpy.ops.tk.retopo_setup() == {'FINISHED'}

    retopo = bpy.context.active_object
    assert retopo.name == "Sculpt_retopo", retopo.name
    assert retopo.show_in_front
    assert len(retopo.data.vertices) == 0
    modifier = retopo.modifiers[0]
    assert modifier.type == 'SHRINKWRAP' and modifier.target == sculpt
    assert modifier.show_on_cage

    tool_settings = bpy.context.scene.tool_settings
    assert tool_settings.use_snap
    assert tool_settings.snap_elements_individual == {'FACE_PROJECT'}
    assert bpy.context.mode == 'EDIT_MESH'
    bpy.ops.object.mode_set(mode='OBJECT')


def _cube_with_keys(modifier_type):
    obj = add_cube()
    obj.shape_key_add(name="Basis")
    for name in ("Smile", "Frown", "Blink"):
        key = obj.shape_key_add(name=name)
        key.value = 0.25
        key.data[0].co.x += 0.5
    obj.modifiers.new("Mod", modifier_type)
    return obj


def test_apply_modifiers_rejects_topology_change():
    reset()
    _cube_with_keys('SUBSURF')
    raises(bpy.ops.tk.apply_modifiers_shapekeys, "change the vertex count")


def test_apply_modifiers_keeps_shapekeys():
    reset()
    obj = _cube_with_keys('CAST')  # deforms, does not change vertex count
    moved = obj.data.shape_keys.key_blocks["Smile"].data[0].co.copy()
    assert bpy.ops.tk.apply_modifiers_shapekeys() == {'FINISHED'}

    keys = obj.data.shape_keys.key_blocks
    assert [k.name for k in keys] == ["Basis", "Smile", "Frown", "Blink"], [
        k.name for k in keys
    ]
    assert not obj.modifiers
    assert keys["Smile"].value == 0.25
    # The key still carries the offset, now with the modifier baked in.
    assert keys["Smile"].data[0].co != keys["Basis"].data[0].co
    assert keys["Smile"].data[0].co != moved
    assert not [o for o in bpy.data.objects if o.name.startswith("__tk_")]


def test_split_shapekey():
    reset()
    obj = add_cube()
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name="Smile")
    key.data[0].co.x += 0.5
    obj.active_shape_key_index = 1

    raises(bpy.ops.tk.split_shapekey, "Missing vertex group")
    obj.vertex_groups.new(name="Left")
    obj.vertex_groups.new(name="Right")
    assert bpy.ops.tk.split_shapekey() == {'FINISHED'}

    keys = obj.data.shape_keys.key_blocks
    assert keys["Smile_L"].vertex_group == "Left"
    assert keys["Smile_R"].vertex_group == "Right"
    assert keys["Smile_L"].data[0].co == keys["Smile"].data[0].co


def _armature(bone_names):
    bpy.ops.object.armature_add()
    obj = bpy.context.active_object
    bpy.ops.object.mode_set(mode='EDIT')
    for bone in list(obj.data.edit_bones):
        obj.data.edit_bones.remove(bone)
    for index, name in enumerate(bone_names):
        bone = obj.data.edit_bones.new(name)
        bone.head = (0, 0, index)
        bone.tail = (0, 0, index + 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


HUMANOID = [
    "Hips", "Spine", "Chest", "Neck", "Head",
    "UpperArm_L", "UpperArm_R", "lower_arm.L", "lower_arm.R", "Hand_L", "Hand_R",
    "Thigh_L", "Thigh_R", "Calf_L", "Calf_R", "Foot_L", "Foot_R",
]


MIXAMO = [
    "mixamorig:Hips", "mixamorig:Spine", "mixamorig:Spine2", "mixamorig:Neck",
    "mixamorig:Head",
    "mixamorig:LeftArm", "mixamorig:RightArm",
    "mixamorig:LeftForeArm", "mixamorig:RightForeArm",
    "mixamorig:LeftHand", "mixamorig:RightHand",
    "mixamorig:LeftUpLeg", "mixamorig:RightUpLeg",
    "mixamorig:LeftLeg", "mixamorig:RightLeg",
    "mixamorig:LeftFoot", "mixamorig:RightFoot",
]


def test_validate_humanoid():
    from blender_toolkit.tools.rigging.operators import (
        _part,
        _side,
        missing_humanoid_bones,
    )

    for name, part, side in (
        ("UpperArm_L", "upperarm", 'L'),
        ("lower_arm.R", "lowerarm", 'R'),
        ("mixamorig:LeftForeArm", "lowerarm", 'L'),
        ("mixamorig:RightUpLeg", "thigh", 'R'),
        ("mixamorig:LeftLeg", "calf", 'L'),
        ("mixamorig:Spine2", "chest", None),
    ):
        assert _part(name) == part, (name, _part(name))
        assert _side(name) == side, (name, _side(name))

    bones = [SimpleNamespace(name=n) for n in HUMANOID]
    assert missing_humanoid_bones(bones) == []
    assert missing_humanoid_bones([SimpleNamespace(name=n) for n in MIXAMO]) == []

    partial = [b for b in bones if b.name not in {"Neck", "Foot_R"}]
    assert missing_humanoid_bones(partial) == ["neck", "foot.R"]

    reset()
    _armature(HUMANOID)
    assert bpy.ops.tk.validate_humanoid() == {'FINISHED'}


def test_twist_bones():
    reset()
    obj = _armature(["UpperArm_L", "Thigh_L"])
    bpy.ops.object.mode_set(mode='EDIT')
    for bone in obj.data.edit_bones:
        bone.select = True
    lengths = {b.name: b.length for b in obj.data.edit_bones}
    assert bpy.ops.tk.add_twist_bones() == {'FINISHED'}

    assert "twist_UpperArm_L" in obj.data.edit_bones
    twist = obj.data.edit_bones["twist_UpperArm_L"]
    assert abs(twist.length - lengths["UpperArm_L"] * 0.5) < 1e-5
    assert twist.parent.name == "UpperArm_L"

    bpy.ops.object.mode_set(mode='POSE')
    constraint = obj.pose.bones["twist_UpperArm_L"].constraints[0]
    assert constraint.type == 'COPY_ROTATION'
    assert constraint.subtarget == "UpperArm_L"
    assert (constraint.use_x, constraint.use_y, constraint.use_z) == (False, True, False)
    assert constraint.owner_space == constraint.target_space == 'LOCAL'
    assert constraint.influence == 0.5
    bpy.ops.object.mode_set(mode='OBJECT')


def test_export_fbx():
    reset()
    add_cube()
    path = os.path.join(tempfile.mkdtemp(), "out.fbx")
    assert bpy.ops.tk.export_game_fbx(filepath=path) == {'FINISHED'}
    assert os.path.getsize(path) > 0


def test_unregister_is_clean():
    blender_toolkit.unregister()
    for name in (
        "TK_PT_retopo", "TK_PT_shapekeys", "TK_PT_rigging", "TK_PT_export",
        "TK_MT_pie_main", "TK_AddonPreferences",
    ):
        assert not hasattr(bpy.types, name), name
    assert not hasattr(bpy.ops.tk, "retopo_setup") or "tk.retopo_setup" not in dir(bpy.ops.tk)
    blender_toolkit.register()


def main():
    blender_toolkit.register()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - report and keep going
            failures += 1
            import traceback

            traceback.print_exc()
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
