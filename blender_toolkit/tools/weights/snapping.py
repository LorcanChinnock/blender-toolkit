"""Landing gradient handles on mesh geometry.

One BVH query via closest_point_on_mesh, then a refinement inside the single
polygon it returns - so the cost does not grow with the mesh.
"""

from mathutils import Vector

MODES = (
    ('FREE', "Free", "Drag in the view plane, ignoring the surface"),
    ('VERTEX', "Vertex", "Snap to the nearest vertex"),
    ('EDGE', "Edge", "Snap to the nearest point on an edge"),
    ('FACE', "Face", "Snap to the nearest point on the surface"),
)


def _nearest_on_segment(point, a, b):
    direction = b - a
    length_sq = direction.dot(direction)
    if length_sq == 0.0:
        return a
    t = min(max((point - a).dot(direction) / length_sq, 0.0), 1.0)
    return a + direction * t


def _view_ray(obj, point, region_data):
    """A ray from the viewer through `point`, in object space.

    The dragged point already sits under the cursor on the view plane, so the
    ray through it hits whatever the cursor is over. Snapping to the nearest
    surface in 3D instead - which is what closest_point_on_mesh does - can yank
    a handle to the far side of the mesh, nowhere near where you are pointing.
    """
    to_world = obj.matrix_world
    to_local = to_world.inverted()
    world_point = to_world @ Vector(point)

    view = region_data.view_matrix.inverted()
    if region_data.is_perspective:
        origin = view.translation
        direction = (world_point - origin).normalized()
    else:
        direction = (view.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
        # Start well behind the mesh so the whole object is in front of the ray.
        origin = world_point - direction * 1e4

    return to_local @ origin, (to_local.to_3x3() @ direction).normalized()


def _surface_point(obj, point, region_data):
    """Where on the surface to snap to, and which polygon it landed in."""
    if region_data is not None:
        origin, direction = _view_ray(obj, point, region_data)
        hit, location, _normal, index = obj.ray_cast(origin, direction)
        if hit:
            return location, index

    # No viewport to aim through, or the ray missed the mesh entirely.
    hit, location, _normal, index = obj.closest_point_on_mesh(point)
    return (location, index) if hit else (None, None)


def snap(obj, point, mode, region_data=None):
    """Object-space `point` moved onto the mesh according to `mode`.

    Returns the point untouched for FREE, for a non-mesh object, or when the
    surface query misses - a handle that silently jumps to the origin because
    nothing was hit would be worse than one that does not snap.
    """
    point = Vector(point)
    if mode == 'FREE' or obj is None or obj.type != 'MESH':
        return point

    location, index = _surface_point(obj, point, region_data)
    if location is None:
        return point
    if mode == 'FACE':
        return location

    mesh = obj.data
    polygon = mesh.polygons[index]
    if mode == 'VERTEX':
        return min(
            (mesh.vertices[i].co for i in polygon.vertices),
            key=lambda co: (co - location).length_squared,
        ).copy()
    if mode == 'EDGE':
        return min(
            (
                _nearest_on_segment(
                    location, mesh.vertices[a].co, mesh.vertices[b].co
                )
                for a, b in polygon.edge_keys
            ),
            key=lambda co: (co - location).length_squared,
        )
    raise ValueError(f"Unknown snap mode: {mode}")
