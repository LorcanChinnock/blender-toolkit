import bpy

from ...utils import load_submodules

load_submodules(
    globals(),
    __package__,
    ("gradient", "snapping", "properties", "overlay", "operators", "ui"),
)

classes = properties.classes + operators.classes + overlay.classes


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    # The one non-loop section any __init__ holds: these have to bracket class
    # registration, because a CollectionProperty cannot exist before the
    # PropertyGroup it holds.
    #
    # On the object, not the scene: a gradient belongs to one mesh's vertex
    # group, and two meshes in a file each want their own. Not indexed either -
    # the active vertex group is what picks one, so there is no second list.
    bpy.types.Object.tk_gradients = bpy.props.CollectionProperty(
        type=properties.TK_PG_weight_gradient
    )
    # Not on the gradient: how a handle is placed is a viewport preference, the
    # same for every gradient, and no part of what one produces.
    bpy.types.Object.tk_gradient_snap = bpy.props.EnumProperty(
        name="Snap",
        description="What a dragged handle lands on",
        items=snapping.MODES,
        default='FREE',
    )
    # The draw handler and the write timer run for the add-on's lifetime rather
    # than being switched on and off: both cost nothing when there is no
    # gradient on the active group, and a file opened with gradients already in
    # it has nobody to switch them on.
    overlay.enable()


def unregister():
    overlay.disable()  # a stale draw handler would outlive the add-on and crash
    del bpy.types.Object.tk_gradient_snap
    del bpy.types.Object.tk_gradients
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
