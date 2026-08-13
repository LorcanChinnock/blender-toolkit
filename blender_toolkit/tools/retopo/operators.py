import bpy

SHRINKWRAP_NAME = "Retopo Shrinkwrap"
MIRROR_NAME = "Retopo Mirror"


def selected_geometry(mesh):
    """(verts, faces) covering `mesh`'s selected faces, indices remapped.

    Face selection only syncs out of Edit mode, so read this with the object
    back in Object mode.
    """
    remap = {}
    verts = []
    faces = []
    for polygon in mesh.polygons:
        if not polygon.select:
            continue
        face = []
        for index in polygon.vertices:
            if index not in remap:
                remap[index] = len(verts)
                verts.append(tuple(mesh.vertices[index].co))
            face.append(remap[index])
        faces.append(face)
    return verts, faces


def retopo_shrinkwrap(obj):
    """The shrinkwrap marking `obj` as a retopo mesh, or None.

    Any shrinkwrap with a target counts, so a setup built by hand is adopted
    too; the one this operator adds wins when there are several. The modifier
    is the only marker - deleting it is how the user detaches the mesh.
    """
    targeted = [m for m in obj.modifiers if m.type == 'SHRINKWRAP' and m.target]
    for modifier in targeted:
        if modifier.name == SHRINKWRAP_NAME:
            return modifier
    return targeted[0] if targeted else None


def existing_retopo(source):
    """A mesh already retopologising `source`, or None."""
    for obj in bpy.data.objects:
        if obj is source or obj.type != 'MESH':
            continue
        modifier = retopo_shrinkwrap(obj)
        if modifier is not None and modifier.target is source:
            return obj
    return None


class TK_OT_retopo_setup(bpy.types.Operator):
    """Create or return to a low-poly mesh set up to retopologise the active sculpt"""

    bl_idname = "tk.retopo_setup"
    bl_label = "Setup Retopo"
    bl_options = {'REGISTER', 'UNDO'}

    offset: bpy.props.FloatProperty(
        name="Offset",
        description="Distance the retopo shell sits above the surface",
        default=0.0,
        subtype='DISTANCE',
    )
    mirror: bpy.props.BoolProperty(
        name="Mirror",
        description="Mirror the retopo mesh across X and turn on mesh symmetry",
        default=False,
    )
    seed_from_selection: bpy.props.BoolProperty(
        name="Seed From Selection",
        description="Start from a copy of the source's selected faces, not empty",
        default=False,
    )
    set_snapping: bpy.props.BoolProperty(
        name="Set Snapping",
        description="Turn on face project snapping",
        default=True,
    )
    auto_merge: bpy.props.BoolProperty(
        name="Auto Merge",
        description="Weld vertices dropped on top of each other",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == 'MESH'
            and context.mode in {'OBJECT', 'SCULPT', 'EDIT_MESH'}
        )

    def execute(self, context):
        active = context.active_object

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Pressed on the retopo mesh, on its sculpt, or on a sculpt with no
        # retopo mesh yet - all three arm the same workspace below.
        modifier = retopo_shrinkwrap(active)
        if modifier is not None:
            source, retopo = modifier.target, active
        else:
            source, retopo = active, existing_retopo(active)

        notes = []
        created = retopo is None
        if created:
            retopo = self._create(context, source, notes)
        elif self.seed_from_selection:
            # Seeding an adopted mesh would dump duplicate faces on top of work
            # that is already there.
            notes.append("seeding skipped")

        self._arm(context, source, retopo)

        if not retopo.data.vertices:
            notes.append("Ctrl+RMB to place the first vertex")

        verb = "ready" if created else "adopted"
        suffix = f" ({', '.join(notes)})" if notes else ""
        self.report({'INFO'}, f"Retopo mesh '{retopo.name}' {verb}{suffix}")
        return {'FINISHED'}

    def _create(self, context, source, notes):
        """The new retopo mesh, linked beside `source` and shrinkwrapped to it."""
        mesh = bpy.data.meshes.new(f"{source.name}_retopo")
        if self.seed_from_selection:
            verts, faces = selected_geometry(source.data)
            if faces:
                mesh.from_pydata(verts, [], faces)
            else:
                notes.append("nothing selected to seed from")

        retopo = bpy.data.objects.new(f"{source.name}_retopo", mesh)
        # Same collection as the sculpt so it does not land in the scene root.
        (source.users_collection or (context.scene.collection,))[0].objects.link(retopo)
        retopo.matrix_world = source.matrix_world
        retopo.show_in_front = True

        shrinkwrap = retopo.modifiers.new(SHRINKWRAP_NAME, 'SHRINKWRAP')
        shrinkwrap.target = source
        shrinkwrap.wrap_method = 'PROJECT'
        shrinkwrap.use_negative_direction = True
        shrinkwrap.use_positive_direction = True
        shrinkwrap.show_on_cage = True
        return retopo

    def _arm(self, context, source, retopo):
        """Apply the properties to `retopo` and leave the user editing it."""
        retopo_shrinkwrap(retopo).offset = self.offset

        mirror = retopo.modifiers.get(MIRROR_NAME)
        if self.mirror and mirror is None:
            mirror = retopo.modifiers.new(MIRROR_NAME, 'MIRROR')
            mirror.use_clip = True
            # Above the shrinkwrap, so the mirrored half projects too.
            retopo.modifiers.move(len(retopo.modifiers) - 1, 0)
        elif not self.mirror and mirror is not None:
            retopo.modifiers.remove(mirror)
        retopo.data.use_mirror_x = self.mirror

        tool_settings = context.scene.tool_settings
        if self.set_snapping:
            tool_settings.use_snap = True
            # In 5.x "project individual elements" is its own snap mode and
            # setting it clears snap_elements_base, so face snapping is this
            # line alone.
            tool_settings.snap_elements_individual = {'FACE_PROJECT'}
            tool_settings.use_snap_self = False
        if self.auto_merge:
            tool_settings.use_mesh_automerge = True

        bpy.ops.object.select_all(action='DESELECT')
        retopo.select_set(True)
        context.view_layer.objects.active = retopo
        bpy.ops.object.mode_set(mode='EDIT')


classes = (TK_OT_retopo_setup,)
