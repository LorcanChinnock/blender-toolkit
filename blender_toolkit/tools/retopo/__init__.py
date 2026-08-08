if "bpy" in locals():
    import importlib

    importlib.reload(operators)
    importlib.reload(ui)
else:
    from . import operators
    from . import ui

import bpy  # noqa: E402


def register():
    for cls in operators.classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(operators.classes):
        bpy.utils.unregister_class(cls)
