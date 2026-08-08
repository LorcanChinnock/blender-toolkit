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
    # The one non-loop line any __init__ holds: the settings pointer has to
    # bracket class registration, and the panel and overlay both read it.
    # On the object, not the scene: a session is about one mesh, and two meshes
    # in a file each want their own handles, ramp and group.
    bpy.types.Object.tk_gradient = bpy.props.PointerProperty(
        type=properties.TK_PG_weight_gradient
    )


def unregister():
    overlay.disable()  # a stale draw handler would outlive the add-on and crash
    del bpy.types.Object.tk_gradient
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
