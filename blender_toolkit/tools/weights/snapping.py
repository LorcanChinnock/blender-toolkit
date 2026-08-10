"""Landing gradient handles on mesh geometry, along the cursor's ray.

Two things this module refuses to do, both paid for:

**It does not look for the nearest point in 3D.** A handle dragged past the
surface is still under the cursor on the *near* side; `closest_point_on_mesh`
answers with the far side. Everything here starts from a ray the caller built
from the real mouse position, and a ray that misses returns the caller's
fallback rather than teleporting the handle somewhere it can be found.

**It does not query `obj.ray_cast` / `obj.closest_point_on_mesh`.** Those hit
evaluated geometry - their docstrings say so - while the index they hand back is
only meaningful against `obj.data`. With a shape key or a deform modifier the
two disagree; with a topology-changing modifier the index is out of range. So
the tree here is built explicitly over the evaluated mesh, and the hit is mapped
back to base coordinates through the hit triangle, which is exact for any stack
that preserves vertex order.

The gradient itself measures base coordinates, so base is what a handle stores;
the evaluated surface is only what the cursor is pointing at.
"""

from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform

# One cache per (object, evaluated topology): rebuilding per mouse-move event
# would undo the drag coalescing everything else here is careful about. Measured
# at ~31 ms per 40k verts, inside the drag.
_cache = (None, None)

MODES = (
    ('FREE', "Free", "Drag in the view plane, ignoring the surface"),
    ('VERTEX', "Vertex", "Snap to the nearest vertex"),
    ('EDGE', "Edge", "Snap to the nearest point on an edge"),
    ('FACE', "Face", "Snap to the nearest point on the surface"),
)


class _Surface:
    """The evaluated surface, plus what maps a hit on it back to base."""

    __slots__ = ("tree", "evaluated", "base", "triangles", "edges")

    def __init__(self, tree, evaluated, base, triangles, edges):
        self.tree = tree
        self.evaluated = evaluated  # coordinates, evaluated space
        self.base = base            # same indices, base space - or None
        self.triangles = triangles  # vertex index triples
        self.edges = edges          # real edges, so a triangulation diagonal
                                    # inside a quad is never snapped to

    def to_base(self, point, triangle):
        """`point`, which lies in `triangle`, expressed in base coordinates."""
        if self.base is None:
            return point
        source = [self.evaluated[i] for i in triangle]
        target = [self.base[i] for i in triangle]
        return barycentric_transform(point, *source, *target)


def _nearest_on_segment(point, a, b):
    direction = b - a
    length_sq = direction.dot(direction)
    if length_sq == 0.0:
        return a
    t = min(max((point - a).dot(direction) / length_sq, 0.0), 1.0)
    return a + direction * t


def _build(obj):
    """Triangulated evaluated geometry for `obj`, and its base counterpart.

    `base` is None when the modifier stack does not preserve vertex order - a
    subsurf, a mirror - because then an evaluated index means nothing on the
    base mesh. The evaluated coordinates stand in for both, which is what the
    old base-only version effectively did.
    """
    import bpy

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_obj = obj.evaluated_get(depsgraph)
    mesh = evaluated_obj.to_mesh()
    try:
        mesh.calc_loop_triangles()  # free when the cache is already filled
        coords = [v.co.copy() for v in mesh.vertices]
        triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
        edges = {tuple(sorted(e.vertices)) for e in mesh.edges}
    finally:
        # The temp mesh must not outlive the call - everything above is copied.
        evaluated_obj.to_mesh_clear()

    base = None
    if len(obj.data.vertices) == len(coords):
        base = [v.co.copy() for v in obj.data.vertices]
    return _Surface(
        BVHTree.FromPolygons(coords, triangles), coords, base, triangles, edges
    )


def _surface(obj):
    global _cache
    # Topology alone, because anything that only *moves* geometry - a shape key
    # value, a pose - arrives as a depsgraph update, and invalidate() runs then.
    key = (obj.name, len(obj.data.vertices), len(obj.data.polygons))
    if key != _cache[0]:
        _cache = (key, _build(obj))
    return _cache[1]


def invalidate():
    """Drop the cached surface - the geometry moved under it."""
    global _cache
    _cache = (None, None)


def snap(obj, origin, direction, mode, fallback):
    """Base object-space point where the cursor ray meets `obj`, per `mode`.

    `origin` and `direction` are in object space. `fallback` is returned
    untouched for FREE, for a non-mesh object, and whenever the ray misses - a
    handle that jumps to the origin because nothing was hit would be worse than
    one that does not snap.
    """
    fallback = Vector(fallback)
    if mode == 'FREE' or obj is None or obj.type != 'MESH' or not obj.data.polygons:
        return fallback

    surface = _surface(obj)
    location, _normal, index, _distance = surface.tree.ray_cast(
        Vector(origin), Vector(direction)
    )
    if location is None:
        return fallback

    triangle = surface.triangles[index]
    if mode == 'FACE':
        return surface.to_base(location, triangle)

    # ponytail: the hit triangle is the whole candidate set. Widen this to
    # tree.find_nearest_range() if a vertex just across a face boundary turns
    # out to be unreachable in practice.
    if mode == 'VERTEX':
        nearest = min(
            (surface.evaluated[i] for i in triangle),
            key=lambda co: (co - location).length_squared,
        )
    elif mode == 'EDGE':
        a, b, c = triangle
        pairs = [
            pair for pair in ((a, b), (b, c), (c, a))
            if tuple(sorted(pair)) in surface.edges
        ] or [(a, b), (b, c), (c, a)]
        nearest = min(
            (
                _nearest_on_segment(
                    location, surface.evaluated[i], surface.evaluated[j]
                )
                for i, j in pairs
            ),
            key=lambda co: (co - location).length_squared,
        )
    else:
        raise ValueError(f"Unknown snap mode: {mode}")
    return surface.to_base(nearest, triangle)
