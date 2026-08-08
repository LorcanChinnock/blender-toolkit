import bpy

from .utils import ADDON_PACKAGE


def _refresh_panels(self, context):
    from . import tools

    tools.refresh_panels()


class TK_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_PACKAGE

    use_retopo: bpy.props.BoolProperty(
        name="Retopology",
        description="Enable the retopology tools",
        default=True,
        update=_refresh_panels,
    )
    use_shapekeys: bpy.props.BoolProperty(
        name="Shapekeys",
        description="Enable the shapekey tools",
        default=True,
        update=_refresh_panels,
    )
    use_rigging: bpy.props.BoolProperty(
        name="Rigging",
        description="Enable the rigging tools",
        default=True,
        update=_refresh_panels,
    )
    use_export: bpy.props.BoolProperty(
        name="Export",
        description="Enable the export tools",
        default=True,
        update=_refresh_panels,
    )

    def draw(self, context):
        col = self.layout.column(heading="Modules")
        col.prop(self, "use_retopo")
        col.prop(self, "use_shapekeys")
        col.prop(self, "use_rigging")
        col.prop(self, "use_export")


classes = (TK_AddonPreferences,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
