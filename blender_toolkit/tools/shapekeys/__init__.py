import bpy

from ...utils import load_submodules

load_submodules(globals(), __package__, ("preview", "operators", "ui"))


def register():
    for cls in operators.classes:
        bpy.utils.register_class(cls)
    # Bracketing the class loop, which is the one thing allowed in here: the
    # timer that tidies a preview away must neither predate nor outlive the
    # add-on, and it costs nothing while there is no preview.
    preview.enable()


def unregister():
    preview.disable()
    for cls in reversed(operators.classes):
        bpy.utils.unregister_class(cls)
