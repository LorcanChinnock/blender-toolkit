"""Mesh checks. Each takes a BMesh and returns the elements at fault.

No bpy in here, so the checks are testable against a bmesh built by hand
rather than through the operator.
"""

# A quad grid runs to four edges a vertex; five is the ordinary retopo pole.
MAX_POLE_EDGES = 5


def ngons(bm):
    """Faces with more than four sides."""
    return [face for face in bm.faces if len(face.verts) > 4]


def poles(bm):
    """Vertices joining more edges than a quad grid needs."""
    return [vert for vert in bm.verts if len(vert.link_edges) > MAX_POLE_EDGES]


def non_manifold(bm):
    """Edges shared by three or more faces.

    Boundary edges are deliberately not included. Blender counts an edge with
    one face as non-manifold, but a retopo shell in progress is all boundary,
    so reporting those would bury everything else.
    """
    return [edge for edge in bm.edges if len(edge.link_faces) > 2]


def loose(bm):
    """Vertices attached to no edge, and edges attached to no face."""
    return [vert for vert in bm.verts if not vert.link_edges] + [
        edge for edge in bm.edges if edge.is_wire
    ]


def inconsistent_winding(bm):
    """Manifold edges whose two faces disagree about which way is out.

    This is the exact, local test for what Recalculate Normals fixes, and it
    needs no notion of "outside", so it holds on an open mesh. is_contiguous is
    also False on a boundary edge, hence the manifold guard.
    """
    return [edge for edge in bm.edges if edge.is_manifold and not edge.is_contiguous]


# Report order.
CHECKS = (
    ("ngons", ngons),
    ("poles", poles),
    ("non-manifold edges", non_manifold),
    ("loose", loose),
    ("flipped", inconsistent_winding),
)


def run(bm):
    """[(label, elements), ...] for every check."""
    return [(label, check(bm)) for label, check in CHECKS]
