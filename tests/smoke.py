"""Headless smoke test.

    /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python tests/smoke.py
"""

import os
import sys
import tempfile
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Vector

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


def _grid_with_key(name="Full", subdivisions=10):
    bpy.ops.mesh.primitive_grid_add(
        x_subdivisions=subdivisions, y_subdivisions=subdivisions, size=2
    )
    obj = bpy.context.active_object
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name=name)
    for point in key.data:
        point.co.z += 1.0
    obj.active_shape_key_index = 1
    return obj


def _band(obj, group_a, group_b, start, end, **kwargs):
    """A complementary pair the way the tool now expects: run it twice.

    There is no pair option any more - you write one group, then invert and
    write the other.
    """
    result = bpy.ops.tk.write_gradient(
        source='KEEP', start=start, end=end, group_name=group_a, **kwargs
    )
    bpy.ops.tk.write_gradient(
        source='KEEP', start=start, end=end, group_name=group_b,
        invert=True, **kwargs
    )
    return result


def _offset(key, index):
    return key.data[index].co.z - key.relative_key.data[index].co.z


def test_split_shapekey():
    reset()
    obj = _grid_with_key("Smile")
    keys = obj.data.shape_keys.key_blocks

    raises(bpy.ops.tk.split_shapekey, "Missing vertex group")
    assert _band(obj, "Left", "Right", (-1, 0, 0), (1, 0, 0)) == {'FINISHED'}
    assert bpy.ops.tk.split_shapekey() == {'FINISHED'}

    # The mask is baked into the coordinates, not left as a live group.
    assert keys["Smile_L"].vertex_group == ""
    assert keys["Smile_R"].vertex_group == ""
    assert keys["Smile_L"].relative_key == keys["Basis"]
    for vert in obj.data.vertices:
        weight = obj.vertex_groups["Left"].weight(vert.index)
        assert abs(_offset(keys["Smile_L"], vert.index) - weight) < 1e-5
        assert abs(_offset(keys["Smile_R"], vert.index) - (1.0 - weight)) < 1e-5

    # Named key rather than the active one, and drop the source afterwards.
    assert bpy.ops.tk.split_shapekey(
        key="Smile_L", suffix_a="_Up", suffix_b="_Lo", keep_source=False
    ) == {'FINISHED'}
    assert "Smile_L" not in keys
    assert "Smile_L_Up" in keys

    raises(bpy.ops.tk.split_shapekey, "No shapekey named", key="Nope")


def test_split_chain():
    """Two-level split: the first mask must survive the second."""
    reset()
    obj = _grid_with_key()
    keys = obj.data.shape_keys.key_blocks

    _band(obj, "Left", "Right", (-1, 0, 0), (1, 0, 0))
    _band(obj, "Upper", "Lower", (0, -1, 0), (0, 1, 0))

    assert bpy.ops.tk.split_shapekey() == {'FINISHED'}
    assert bpy.ops.tk.split_shapekey(
        key="Full_L", group_a="Upper", group_b="Lower",
        suffix_a="_Up", suffix_b="_Lo",
    ) == {'FINISHED'}

    for vert in obj.data.vertices:
        left = obj.vertex_groups["Left"].weight(vert.index)
        upper = obj.vertex_groups["Upper"].weight(vert.index)
        assert abs(_offset(keys["Full_L_Up"], vert.index) - left * upper) < 1e-5, (
            tuple(vert.co), left, upper, _offset(keys["Full_L_Up"], vert.index)
        )

    # The corner the first split excluded must not move at all.
    corner = min(obj.data.vertices, key=lambda v: (v.co.x, -v.co.y))
    assert obj.vertex_groups["Left"].weight(corner.index) == 0.0
    assert abs(_offset(keys["Full_L_Up"], corner.index)) < 1e-6

    # The four quadrant keys must still reconstruct the original.
    bpy.ops.tk.split_shapekey(
        key="Full_R", group_a="Upper", group_b="Lower",
        suffix_a="_Up", suffix_b="_Lo",
    )
    quadrants = ("Full_L_Up", "Full_L_Lo", "Full_R_Up", "Full_R_Lo")
    for vert in obj.data.vertices:
        total = sum(_offset(keys[q], vert.index) for q in quadrants)
        assert abs(total - 1.0) < 1e-5, (vert.index, total)


def test_gradient_writes_weights():
    reset()
    obj = _grid_with_key()

    assert _band(obj, "Left", "Right", (-1, 0, 0), (1, 0, 0)) == {'FINISHED'}
    left = obj.vertex_groups["Left"]
    right = obj.vertex_groups["Right"]

    for vert in obj.data.vertices:
        a, b = left.weight(vert.index), right.weight(vert.index)
        assert abs(a + b - 1.0) < 1e-5, (vert.index, a, b)
        # Weight is the position along start -> end, remapped to 0..1.
        assert abs(a - (vert.co.x + 1.0) / 2.0) < 1e-5, (tuple(vert.co), a)

    # A band that is not axis-aligned: weight follows the diagonal.
    assert _band(obj, "Left", "Right", (-1, -1, 0), (1, 1, 0)) == {'FINISHED'}
    for vert in obj.data.vertices:
        expected = min(max((vert.co.x + vert.co.y + 2.0) / 4.0, 0.0), 1.0)
        assert abs(left.weight(vert.index) - expected) < 1e-5, tuple(vert.co)

    # Re-running must reuse the groups, not make Left.001.
    assert _band(obj, "Left", "Right", (-1, 0, 0), (1, 0, 0), smooth_repeat=2) == {
        'FINISHED'
    }
    assert [g.name for g in obj.vertex_groups] == ["Left", "Right"]
    assert abs(left.weight(0) + right.weight(0) - 1.0) < 1e-5

    raises(bpy.ops.tk.write_gradient, "same point", source='KEEP',
           start=(0, 0, 0), end=(0, 0, 0))


def test_gradient_maths():
    """Pure shape/profile/midpoint maths, no bpy."""
    from blender_toolkit.tools.weights import gradient

    path = [(0, 0, 0), (2, 0, 0)]
    start, end = path

    def f(x, **kwargs):
        return gradient.factor((x, 0, 0), path, **kwargs)

    assert f(0.0) == 0.0 and f(2.0) == 1.0
    assert abs(f(1.0) - 0.5) < 1e-9
    assert f(-5.0) == 0.0 and f(5.0) == 1.0  # clamped outside

    # Spherical: 0 at the centre, 1 at the radius, in every direction.
    assert gradient.factor((0, 0, 0), path, shape='SPHERICAL') == 0.0
    for point in ((2, 0, 0), (0, 2, 0), (0, 0, -2)):
        assert abs(gradient.factor(point, path, shape='SPHERICAL') - 1.0) < 1e-9

    # Band: 0 on the centre plane, 1 at both ends.
    assert abs(gradient.factor((1, 0, 0), path, shape='BAND')) < 1e-9
    for x in (0.0, 2.0):
        assert abs(gradient.factor((x, 0, 0), path, shape='BAND') - 1.0) < 1e-9

    # The radial shapes read only the ends, so a bend must not move them.
    bent = [(0, 0, 0), (1, 3, 0), (2, 0, 0)]
    assert gradient.factor((0, 2, 0), bent, shape='SPHERICAL') == gradient.factor(
        (0, 2, 0), path, shape='SPHERICAL'
    )

    # Midpoint slides where the ramp crosses 0.5.
    assert abs(f(0.5, midpoint=0.25) - 0.5) < 1e-9
    assert abs(f(1.5, midpoint=0.75) - 0.5) < 1e-9

    for name, _label, _desc in gradient.PROFILES:
        values = [f(x / 10.0 * 2.0, profile=name) for x in range(11)]
        assert all(0.0 <= v <= 1.0 for v in values), name
        assert all(b >= a - 1e-9 for a, b in zip(values, values[1:])), name
        if name != 'CONSTANT':
            assert values[0] == 0.0 and abs(values[-1] - 1.0) < 1e-9, name
        # Invert mirrors the direction - profile(1-t), not 1-profile(t) - so it
        # matches the handle colours swapping ends.
        assert abs(f(0.7, profile=name, invert=True)
                   - f(2.0 - 0.7, profile=name)) < 1e-9, name

    # A curve callable replaces the named profile; the session passes its ramp.
    assert abs(f(0.5, curve=lambda t: t * t) - 0.0625) < 1e-9
    assert abs(f(0.5, curve=lambda t: 5.0) - 1.0) < 1e-9  # results are clamped

    try:
        gradient.factor((0, 0, 0), [start, start])
        raise AssertionError("expected ValueError for a zero-length gradient")
    except ValueError:
        pass


def test_path_factor():
    """Arc-length position along a multi-handle path."""
    from blender_toolkit.tools.weights import gradient

    straight = [(0, 0, 0), (2, 0, 0)]
    assert gradient.path_factor((0, 0, 0), straight) == 0.0
    assert gradient.path_factor((2, 0, 0), straight) == 1.0
    assert abs(gradient.path_factor((1, 5, 0), straight) - 0.5) < 1e-9  # off-axis
    assert gradient.path_factor((-9, 0, 0), straight) == 0.0  # clamped past the end

    # An L: two legs of length 2, so the corner sits at half the arc length even
    # though it is nowhere near half the straight-line distance.
    bent = [(0, 0, 0), (2, 0, 0), (2, 2, 0)]
    assert abs(gradient.path_factor((2, 0, 0), bent) - 0.5) < 1e-9
    assert abs(gradient.path_factor((1, 0, 0), bent) - 0.25) < 1e-9
    assert abs(gradient.path_factor((2, 2, 0), bent) - 1.0) < 1e-9
    # A point beside the second leg reads its arc position, not its distance
    # from the start, which is what makes a bent gradient follow the bend.
    assert abs(gradient.path_factor((3, 1, 0), bent) - 0.75) < 1e-9

    try:
        gradient.path_factor((0, 0, 0), [(1, 1, 1)])
        raise AssertionError("expected ValueError for a one-handle path")
    except ValueError:
        pass


def test_path_curved():
    from blender_toolkit.tools.weights import gradient

    handles = [(0, 0, 0), (1, 1, 0), (2, 0, 0)]
    points = gradient.path_points(handles, curved=True, per_segment=8)

    # Catmull-Rom passes through its control points; a handle must end up where
    # it was put.
    for handle in handles:
        assert min(
            sum((a - b) ** 2 for a, b in zip(handle, p)) for p in points
        ) < 1e-12, handle

    lengths, offsets, total = gradient.segment_lengths(points)
    assert total > 0
    assert all(b >= a for a, b in zip(offsets, offsets[1:]))  # monotonic arc length

    # Collinear handles must sample back to a straight line.
    line = gradient.path_points([(0, 0, 0), (1, 0, 0), (2, 0, 0)], curved=True)
    assert all(abs(p[1]) < 1e-9 and abs(p[2]) < 1e-9 for p in line), line

    # Two handles cannot bend, so curved is a no-op there.
    assert gradient.path_points([(0, 0, 0), (1, 0, 0)], curved=True) == [
        (0, 0, 0), (1, 0, 0)
    ]


def test_path_lookup_matches_brute_force():
    """The KDTree shortcut is only worth having if it changes no answers.

    Swept densely and over a path that folds back on itself: picking only the
    nearest sample's own two segments looks right on a sparse sample and is
    wrong by ~0.2 here.
    """
    from blender_toolkit.tools.weights import gradient, properties

    handles = [(-1 + i * 0.25, (i % 2) * 0.5, 0) for i in range(8)]
    points = gradient.path_points(handles, curved=True)
    metrics = gradient.segment_lengths(points)
    lookup = properties._lookup_for(points)
    assert lookup is not None, "this path should be long enough to accelerate"

    steps = 60
    worst = 0.0
    for i in range(steps):
        for j in range(steps):
            co = (-1.5 + 3.0 * i / steps, -1.5 + 3.0 * j / steps, 0.0)
            worst = max(worst, abs(
                gradient.path_factor(co, points, metrics, lookup)
                - gradient.path_factor(co, points, metrics)
            ))
    assert worst < 1e-9, worst


def test_handle_colours():
    from blender_toolkit.tools.weights import gradient

    low, high = gradient.LOW_COLOUR, gradient.HIGH_COLOUR
    assert gradient.handle_colours(2) == [low, high]
    assert gradient.handle_colours(2, invert=True) == [high, low]

    middle = gradient.handle_colours(4)
    assert middle[0] == low and middle[-1] == high
    assert middle[1] == middle[2] == gradient.MID_COLOUR
    # Inverting swaps the ends and leaves the intermediates alone.
    assert gradient.handle_colours(4, invert=True)[1:3] == middle[1:3]
    assert gradient.handle_colours(0) == []


def test_snapping():
    from blender_toolkit.tools.weights import snapping

    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    obj = bpy.context.active_object
    outside = (2.0, 0.0, 0.0)

    assert tuple(snapping.snap(obj, outside, 'FREE')) == outside

    on_face = snapping.snap(obj, outside, 'FACE')
    assert abs(on_face.length - 1.0) < 1e-4, on_face.length

    on_vertex = snapping.snap(obj, outside, 'VERTEX')
    assert any(
        (v.co - on_vertex).length < 1e-6 for v in obj.data.vertices
    ), tuple(on_vertex)

    # An edge point lies on some polygon edge of the sphere.
    on_edge = snapping.snap(obj, outside, 'EDGE')
    best = min(
        (snapping._nearest_on_segment(
            on_edge, obj.data.vertices[a].co, obj.data.vertices[b].co
        ) - on_edge).length
        for polygon in obj.data.polygons
        for a, b in polygon.edge_keys
    )
    assert best < 1e-6, best

    # A non-mesh object must pass the point straight through, not explode.
    bpy.ops.object.empty_add()
    assert tuple(snapping.snap(bpy.context.active_object, outside, 'VERTEX')) == outside


def test_snapping_follows_the_cursor():
    """Snap to what the view ray hits, not to whatever is nearest in 3D.

    A handle dragged past the surface is still under the cursor on the near
    side; snapping by 3D proximity puts it on the far side of the mesh, which
    is what made dragging feel wrong.
    """
    from types import SimpleNamespace

    from mathutils import Matrix

    from blender_toolkit.tools.weights import snapping

    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    obj = bpy.context.active_object

    # Front view: viewer at -Y looking towards +Y.
    view = Matrix.Rotation(1.5707963, 4, 'X')
    view.translation = (0.0, -10.0, 0.0)
    region_data = SimpleNamespace(view_matrix=view.inverted(), is_perspective=True)

    dragged = Vector((0.0, 3.0, 0.0))  # beyond the sphere, cursor still over it
    for mode in ('FACE', 'VERTEX', 'EDGE'):
        with_ray = snapping.snap(obj, dragged, mode, region_data)
        nearest = snapping.snap(obj, dragged, mode, None)
        assert with_ray.y < 0.0, (mode, tuple(with_ray))   # the near face
        assert nearest.y > 0.0, (mode, tuple(nearest))     # what it used to do

    # An orthographic view has no eye position; the ray still points the way.
    ortho = SimpleNamespace(view_matrix=view.inverted(), is_perspective=False)
    assert snapping.snap(obj, dragged, 'FACE', ortho).y < 0.0

    # A ray that misses falls back rather than dropping the handle at the origin.
    away = Vector((50.0, 3.0, 0.0))
    assert snapping.snap(obj, away, 'FACE', region_data).length <= 1.0 + 1e-5


def test_handles_follow_ramp_stops():
    """The gradient is the control for how many handles there are."""
    from blender_toolkit.tools.weights import properties

    reset()
    _grid_with_key()
    settings = bpy.context.scene.tk_gradient
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}
    points = properties.ramp_of(settings).elements
    assert len(settings.handles) == len(points) == 2

    # A new stop grows a handle, seeded where it sits along the path - and in
    # stop order, so a stop in the middle is a handle in the middle.
    points.new(0.5)
    assert properties.sync_handles_to_ramp(settings) is True
    assert len(settings.handles) == 3
    assert [round(h.t, 3) for h in settings.handles] == [0.0, 0.5, 1.0]
    assert abs(settings.handles[1].position.x) < 1e-6, settings.handles[1].position

    # Idempotent: nothing changed, so nothing to do.
    assert properties.sync_handles_to_ramp(settings) is False

    # Dragging a handle must survive the next stop being added.
    settings.handles[1].position = (0.0, 0.7, 0.0)
    points.new(0.75)
    properties.sync_handles_to_ramp(settings)
    assert len(settings.handles) == 4
    assert [round(h.t, 3) for h in settings.handles] == [0.0, 0.5, 0.75, 1.0]
    assert abs(settings.handles[1].position.y - 0.7) < 1e-6  # kept, not reseeded
    assert settings.handles[2].position.x > 0.0  # the new one, past the middle

    # Removing stops takes the handles with them.
    while len(points) > 2:
        points.remove(points[1])
    assert properties.sync_handles_to_ramp(settings) is True
    assert len(settings.handles) == 2
    assert settings.active_handle < len(settings.handles)

    bpy.ops.tk.cancel_gradient()


def test_cancel_resets_the_session():
    """Cancel leaves no persisted state for the next session to inherit."""
    from blender_toolkit.tools.weights import properties

    reset()
    _grid_with_key()
    settings = bpy.context.scene.tk_gradient
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}

    properties.ramp_of(settings).elements.new(0.5)
    properties.sync_handles_to_ramp(settings)
    settings.invert = True
    settings.curved = True
    assert len(settings.handles) == 3

    assert bpy.ops.tk.cancel_gradient() == {'FINISHED'}
    assert len(properties.ramp_of(settings).elements) == 2
    assert not settings.invert and not settings.curved
    assert len(settings.handles) == 0  # reseeded on the next Start

    # And the next session comes up with the default two handles.
    assert bpy.ops.tk.start_gradient() == {'FINISHED'}
    assert len(settings.handles) == 2
    bpy.ops.tk.cancel_gradient()


def test_point_at_walks_the_path():
    from blender_toolkit.tools.weights import operators, properties

    reset()
    _grid_with_key()
    settings = bpy.context.scene.tk_gradient
    # An L of two equal legs, so arc position and straight distance differ.
    operators.set_handles(settings, [(0, 0, 0), (2, 0, 0), (2, 2, 0)])

    assert (properties.point_at(settings, 0.0) - Vector((0, 0, 0))).length < 1e-6
    assert (properties.point_at(settings, 0.5) - Vector((2, 0, 0))).length < 1e-6
    assert (properties.point_at(settings, 0.25) - Vector((1, 0, 0))).length < 1e-6
    assert (properties.point_at(settings, 0.75) - Vector((2, 1, 0))).length < 1e-6
    assert (properties.point_at(settings, 1.0) - Vector((2, 2, 0))).length < 1e-6


def test_ramp_values():
    from blender_toolkit.tools.weights import gradient, properties

    reset()
    _grid_with_key()
    settings = bpy.context.scene.tk_gradient
    ramp = properties.ensure_ramp(settings).color_ramp
    assert len(ramp.elements) == 2
    assert tuple(ramp.elements[0].color) == (0.0, 0.0, 0.0, 1.0)
    assert tuple(ramp.elements[-1].color) == (1.0, 1.0, 1.0, 1.0)

    ramp.elements.new(0.5).color = (0.25, 0.25, 0.25, 1.0)
    curve = properties.ramp_curve(settings)
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert abs(curve(t) - ramp.evaluate(t)[0]) < 1e-6, t
    assert abs(curve(0.5) - 0.25) < 1e-5

    path = [(0, 0, 0), (2, 0, 0)]
    assert abs(gradient.factor((1, 0, 0), path, curve=curve) - curve(0.5)) < 1e-6
    # Inverting reads the gradient mirrored.
    assert abs(
        gradient.factor((0.5, 0, 0), path, curve=curve, invert=True) - curve(0.75)
    ) < 1e-6

    settings.use_ramp = False
    assert properties.ramp_curve(settings) is None  # falls back to the profile
    settings.use_ramp = True

    properties.reset_ramp(settings)
    assert len(ramp.elements) == 2


def test_ramp_is_a_value_picker():
    """Greyscale and at least two stops, enforced however it was edited."""
    from blender_toolkit.tools.weights import properties

    reset()
    _grid_with_key()
    settings = bpy.context.scene.tk_gradient
    ramp = properties.ensure_ramp(settings).color_ramp

    # A colour picked in the widget is flattened to its value.
    ramp.elements[0].color = (0.9, 0.3, 0.0, 0.5)
    assert properties.normalise_ramp(settings) is True
    red, green, blue, alpha = ramp.elements[0].color
    assert red == green == blue, (red, green, blue)
    assert abs(red - 0.4) < 1e-6  # the mean of the RGB it was given
    assert alpha == 1.0  # opaque, so alpha cannot quietly scale the weight

    # Idempotent: already flat, nothing to do.
    assert properties.normalise_ramp(settings) is False

    # Blender's own floor is one stop; ours is two.
    ramp.elements.remove(ramp.elements[0])
    assert len(ramp.elements) == 1
    assert properties.normalise_ramp(settings) is True
    assert len(ramp.elements) == 2
    # The restored stop goes to the far end, never stacked on the survivor.
    positions = sorted(e.position for e in ramp.elements)
    assert positions[0] == 0.0 and positions[-1] == 1.0, positions


def test_gradient_mask():
    reset()
    obj = _grid_with_key()

    protect = obj.vertex_groups.new(name="Protect")
    for vert in obj.data.vertices:
        protect.add([vert.index], 1.0 if vert.co.x > 0 else 0.0, 'REPLACE')
    prior = obj.vertex_groups.new(name="Left")
    for vert in obj.data.vertices:
        prior.add([vert.index], 0.25, 'REPLACE')

    assert _band(obj, "Left", "Right", (-1, 0, 0), (1, 0, 0),
                 mask_group="Protect") == {'FINISHED'}
    for vert in obj.data.vertices:
        weight = prior.weight(vert.index)
        if vert.co.x > 0:  # inside the mask, overwritten
            assert abs(weight - (vert.co.x + 1.0) / 2.0) < 1e-5
        else:  # outside it, the prior 0.25 survives
            assert abs(weight - 0.25) < 1e-5, (tuple(vert.co), weight)

    # One run writes exactly one group; the complement is a second run.
    reset()
    obj = _grid_with_key()
    assert bpy.ops.tk.write_gradient(
        source='KEEP', start=(-1, 0, 0), end=(1, 0, 0), group_name="Solo"
    ) == {'FINISHED'}
    assert [g.name for g in obj.vertex_groups] == ["Solo"]

    assert bpy.ops.tk.write_gradient(
        source='KEEP', start=(-1, 0, 0), end=(1, 0, 0),
        group_name="Solo.Other", invert=True,
    ) == {'FINISHED'}
    for vert in obj.data.vertices:
        a = obj.vertex_groups["Solo"].weight(vert.index)
        b = obj.vertex_groups["Solo.Other"].weight(vert.index)
        assert abs(a + b - 1.0) < 1e-5, (a, b)


def test_gradient_session():
    from blender_toolkit.tools.weights import overlay

    reset()
    obj = _grid_with_key()
    settings = bpy.context.scene.tk_gradient

    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}
    assert settings.active
    assert obj.mode == 'WEIGHT_PAINT'
    assert overlay._handler is not None
    assert settings.previous_mode == 'OBJECT'
    def weights():  # the name is chosen by auto-naming, so read it back
        group = obj.vertex_groups[settings.group_name]
        return [group.weight(v.index) for v in obj.data.vertices]

    before = weights()

    # A setting change rewrites the weights live, with no operator call.
    settings.invert = True
    assert all(abs(a + b - 1.0) < 1e-5 for a, b in zip(before, weights()))

    created = settings.group_name
    assert bpy.ops.tk.cancel_gradient() == {'FINISHED'}
    assert not settings.active
    assert overlay._handler is None
    assert obj.mode == 'OBJECT'
    assert created not in obj.vertex_groups  # Cancel removes what it created

    # Add keeps the weights and leaves the session running for the next group.
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='Y') == {'FINISHED'}
    kept = weights()
    assert bpy.ops.tk.add_gradient() == {'FINISHED'}
    assert settings.active, "Add must not end the session"
    assert obj.mode == 'WEIGHT_PAINT'
    assert weights() == kept

    # A second group without leaving: rename, invert, Add again.
    first = settings.group_name
    settings.group_name = "Second"
    settings.invert = True
    assert bpy.ops.tk.add_gradient() == {'FINISHED'}
    assert {first, "Second"} <= {g.name for g in obj.vertex_groups}
    for vert in obj.data.vertices:
        a = obj.vertex_groups[first].weight(vert.index)
        b = obj.vertex_groups["Second"].weight(vert.index)
        assert abs(a + b - 1.0) < 1e-5, (a, b)

    # Closing afterwards keeps everything that was added.
    assert bpy.ops.tk.cancel_gradient() == {'FINISHED'}
    assert obj.mode == 'OBJECT'
    assert not settings.active
    assert {first, "Second"} <= {g.name for g in obj.vertex_groups}


def test_overlay_draws():
    """The draw callback builds its shader and runs. Says nothing about looks."""
    import gpu

    from blender_toolkit.tools.weights import overlay

    gpu.init()  # background Blender has no GPU context until this is called
    reset()
    _grid_with_key()
    settings = bpy.context.scene.tk_gradient
    settings.start, settings.end = (-1, 0, 0), (1, 0, 0)
    settings.active = True
    try:
        for shape in ('LINEAR', 'SPHERICAL', 'BAND'):
            settings.shape = shape
            overlay._draw()
    finally:
        settings.active = False
    assert overlay._shader is not None


def test_gradient_seed_points():
    reset()
    obj = _grid_with_key()

    assert bpy.ops.tk.write_gradient(source='BOUNDS', axis='Y') == {'FINISHED'}
    assert [g.name for g in obj.vertex_groups] == ["Group"]
    for vert in obj.data.vertices:
        expected = (vert.co.y + 1.0) / 2.0
        assert abs(obj.vertex_groups["Group"].weight(vert.index) - expected) < 1e-5

    # Active vertex ends the band, the rest of the selection starts it.
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for vert in bm.verts:
        vert.select = False
    bm.select_history.clear()
    first, last = bm.verts[0], bm.verts[len(bm.verts) - 1]
    for vert in (first, last):
        vert.select = True
        bm.select_history.add(vert)
    # BMVerts die on the mode switch below - keep indices and coordinates.
    first_index, last_index = first.index, last.index
    start, end = first.co.copy(), last.co.copy()

    # AUTO is the default and prefers a usable selection over the bounds.
    assert bpy.ops.tk.write_gradient(group_name="A") == {'FINISHED'}
    bpy.ops.object.mode_set(mode='OBJECT')

    weights = obj.vertex_groups["A"]
    assert abs(weights.weight(first_index)) < 1e-5
    assert abs(weights.weight(last_index) - 1.0) < 1e-5

    direction = end - start
    for vert in obj.data.vertices:
        expected = min(max((vert.co - start).dot(direction) / direction.dot(direction), 0.0), 1.0)
        assert abs(weights.weight(vert.index) - expected) < 1e-5


def test_gradient_seeds_without_a_selection():
    """The button must work in Object mode with nothing selected."""
    reset()
    obj = _grid_with_key()

    # No selection to read, so AUTO spans the X bounds - left to right.
    assert bpy.ops.tk.write_gradient(group_name="A") == {'FINISHED'}
    for vert in obj.data.vertices:
        expected = (vert.co.x + 1.0) / 2.0
        assert abs(obj.vertex_groups["A"].weight(vert.index) - expected) < 1e-5

    # A session started cold gets the same treatment.
    settings = bpy.context.scene.tk_gradient
    assert bpy.ops.tk.start_gradient() == {'FINISHED'}
    assert [tuple(round(c, 3) for c in h.position) for h in settings.handles] == [
        (-1.0, 0.0, 0.0), (1.0, 0.0, 0.0)
    ]

    # Bounds come from obj.data.vertices, not obj.bound_box: this grid carries a
    # shape key offset in Z, which moves the evaluated bounding box away from
    # the base coordinates the weights are computed from.
    assert all(abs(h.position.z) < 1e-6 for h in settings.handles)
    bpy.ops.tk.cancel_gradient()

    # A flat mesh has no Z extent, so asking for Z must not produce a
    # zero-length gradient - it falls back to the longest axis.
    assert bpy.ops.tk.write_gradient(
        source='BOUNDS', axis='Z', group_name="C"
    ) == {'FINISHED'}
    values = [obj.vertex_groups["C"].weight(v.index) for v in obj.data.vertices]
    assert min(values) < 0.01 and max(values) > 0.99, (min(values), max(values))

    # Explicit SELECTION still errors rather than quietly falling back.
    raises(bpy.ops.tk.write_gradient, "Edit mode", source='SELECTION')


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


def test_export_preset_resets():
    from blender_toolkit.tools.export.operators import RECOMMENDED, _apply_preset

    # The class bl_rna only carries the base Operator members; the declared
    # properties live on the registered operator type.
    props = bpy.ops.tk.export_game_fbx.get_rna_type().properties
    for name, value in RECOMMENDED.items():
        assert name in props, name
        assert props[name].default == value, name

    dirty = SimpleNamespace(preset='GAME_READY', **{k: None for k in RECOMMENDED})
    _apply_preset(dirty, None)
    assert {k: getattr(dirty, k) for k in RECOMMENDED} == RECOMMENDED

    untouched = SimpleNamespace(preset='CUSTOM', **{k: None for k in RECOMMENDED})
    _apply_preset(untouched, None)
    assert untouched.mesh_smooth_type is None


def test_toggle_pose_mode():
    reset()
    obj = _armature(["Hips", "Spine"])
    obj.pose.bones["Hips"].location.x = 1.0
    assert bpy.ops.tk.toggle_pose_mode() == {'FINISHED'}
    assert obj.mode == 'POSE'
    assert obj.pose.bones["Hips"].location.x == 0.0

    assert bpy.ops.tk.toggle_pose_mode() == {'FINISHED'}
    assert obj.mode == 'OBJECT'

    obj.pose.bones["Hips"].location.x = 1.0
    assert bpy.ops.tk.toggle_pose_mode(reset=False) == {'FINISHED'}
    assert obj.mode == 'POSE'
    assert obj.pose.bones["Hips"].location.x == 1.0
    bpy.ops.tk.toggle_pose_mode(reset=False)

    reset()
    add_cube()
    assert not bpy.ops.tk.toggle_pose_mode.poll()


def test_export_fbx():
    reset()
    add_cube()
    path = os.path.join(tempfile.mkdtemp(), "out.fbx")
    assert bpy.ops.tk.export_game_fbx(filepath=path) == {'FINISHED'}
    assert os.path.getsize(path) > 0

    # Non-default settings must reach the FBX exporter, not be ignored.
    other = os.path.join(tempfile.mkdtemp(), "edge.fbx")
    assert bpy.ops.tk.export_game_fbx(
        filepath=other, mesh_smooth_type='EDGE', axis_up='Z'
    ) == {'FINISHED'}
    assert os.path.getsize(other) > 0


def test_modules_wired():
    """Every tool directory is in MODULE_NAMES with a matching preference."""
    from blender_toolkit import tools

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "blender_toolkit", "tools")
    on_disk = {
        name for name in os.listdir(root)
        if os.path.isfile(os.path.join(root, name, "__init__.py"))
    }
    assert on_disk == set(tools.MODULE_NAMES), on_disk ^ set(tools.MODULE_NAMES)

    # __annotations__, not bl_rna: declared properties only reach bl_rna once
    # the class is registered, and preferences are not in a direct import.
    declared = blender_toolkit.preferences.TK_AddonPreferences.__annotations__
    for module, flag in tools.MODULES:
        assert flag in declared, flag
        assert module.ui.classes and module.operators.classes, module.__name__


def test_reload_with_stale_utils():
    """Reload Scripts on a session whose cached utils predates a new helper.

    utils is the one module load_submodules cannot load: re-executing the top
    __init__ resolves `from .utils import ...` against the session's cached copy,
    so a helper added to utils would raise ImportError before the loader that
    lives in it ever runs. The top __init__ reloads utils by hand first.
    """
    import importlib

    from blender_toolkit import utils

    helper = utils.load_submodules
    # Unregister before reloading: reloading rebinds every class object while
    # bpy still holds the old ones registered, and unregister_class on the new
    # objects then fails. This is the same reason a new module needs a full
    # disable/re-enable rather than F3.
    blender_toolkit.unregister()
    del utils.load_submodules
    try:
        importlib.reload(blender_toolkit)
    finally:
        if not hasattr(utils, "load_submodules"):
            utils.load_submodules = helper
        blender_toolkit.register()
    assert hasattr(utils, "load_submodules")


def test_load_submodules():
    """Reload Scripts on a session that predates a newly added submodule.

    The old session has no name bound for the new module, and calling
    importlib.reload() on a missing name raises NameError partway through the
    reload - leaving the add-on half updated, the old classes still registered
    and the new panel missing. Every __init__ routes through this helper.

    Driven against a namespace dict rather than a live package: reloading a real
    module rebinds its classes while bpy still holds the old ones registered,
    which is why a new module needs a full disable/re-enable, not F3.
    """
    from blender_toolkit.utils import load_submodules

    package = "blender_toolkit.tools.weights"
    namespace = {}

    # First load: nothing bound, everything imports fresh.
    load_submodules(namespace, package, ("gradient",))
    first = namespace["gradient"]
    assert first.factor((1, 0, 0), [(0, 0, 0), (2, 0, 0)]) == 0.5

    # Second load: already bound, so it reloads in place.
    load_submodules(namespace, package, ("gradient",))
    assert namespace["gradient"] is first

    # The regression: names added since the last load. Must import, not raise.
    load_submodules(namespace, package, ("gradient", "properties", "overlay"))
    assert list(namespace) == ["gradient", "properties", "overlay"]
    assert namespace["gradient"] is first  # the existing one still reloaded


def test_unregister_is_clean():
    from blender_toolkit.tools.weights import overlay

    blender_toolkit.unregister()
    try:
        assert not hasattr(bpy.types.Scene, "tk_gradient")
        assert overlay._handler is None
        # Gizmo groups are not exposed on bpy.types by name the way panels are,
        # and bl_rna survives unregister_class. This lookup is what tracks it.
        assert bpy.types.GizmoGroup.bl_rna_get_subclass_py(
            "TK_GGT_weight_gradient"
        ) is None
        assert not hasattr(bpy.types, "TK_PG_gradient_handle")
        assert not hasattr(bpy.types, "TK_PG_weight_gradient")
        for name in (
            "TK_PT_retopo", "TK_PT_shapekeys", "TK_PT_weights", "TK_PT_rigging",
            "TK_PT_export",
            "TK_MT_pie_main", "TK_AddonPreferences",
        ):
            assert not hasattr(bpy.types, name), name
        assert (not hasattr(bpy.ops.tk, "retopo_setup")
                or "tk.retopo_setup" not in dir(bpy.ops.tk))
    finally:
        # Re-register regardless: leaving the add-on down fails every later test
        # with a confusing "operator could not be found".
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
