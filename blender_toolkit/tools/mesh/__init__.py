import bpy

from ...utils import load_submodules

load_submodules(globals(), __package__, ("checks", "operators", "ui"))


def register():
    for cls in operators.classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(operators.classes):
        bpy.utils.unregister_class(cls)
