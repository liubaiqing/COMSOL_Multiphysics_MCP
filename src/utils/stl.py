"""Small STL inspection helpers used before COMSOL import.

The implementation intentionally avoids optional mesh libraries so the MCP
server can diagnose STL files in a default installation.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import numpy as np


def analyze_binary_stl(file_path: str | Path) -> dict[str, Any]:
    """Return basic topology and size information for a binary STL file."""
    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"File not found: {path}"}
    if not path.is_file():
        return {"success": False, "error": f"Path is not a file: {path}"}

    size = path.stat().st_size
    if size < 84:
        return {"success": False, "error": f"File too small to be a binary STL: {path}"}

    try:
        with path.open("rb") as handle:
            header = handle.read(80)
            triangle_count = struct.unpack("<I", handle.read(4))[0]
            expected_size = 84 + triangle_count * 50
            if expected_size != size:
                return {
                    "success": False,
                    "error": "Only standard binary STL files are supported by this analyzer.",
                    "file_size": size,
                    "triangle_count_from_header": triangle_count,
                    "expected_binary_size": expected_size,
                }

            dtype = np.dtype(
                [
                    ("normal", "<f4", (3,)),
                    ("vertices", "<f4", (3, 3)),
                    ("attribute", "<u2"),
                ]
            )
            data = np.fromfile(handle, dtype=dtype, count=triangle_count)
    except Exception as exc:
        return {"success": False, "error": f"Failed to read STL: {exc}"}

    vertices = data["vertices"].reshape(-1, 3).astype(float)
    if vertices.size == 0:
        return {
            "success": True,
            "file": str(path),
            "file_size": size,
            "triangle_count": 0,
            "unique_vertices": 0,
        }

    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    dimensions = bbox_max - bbox_min
    center = (bbox_min + bbox_max) / 2

    triangles = data["vertices"].astype(float)
    edge1 = triangles[:, 1] - triangles[:, 0]
    edge2 = triangles[:, 2] - triangles[:, 0]
    cross = np.cross(edge1, edge2)
    surface_area = float(0.5 * np.linalg.norm(cross, axis=1).sum())
    signed_volume = float(
        np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum()
        / 6.0
    )

    unique_vertices, inverse = np.unique(vertices, axis=0, return_inverse=True)
    faces = inverse.reshape(-1, 3)
    edges = np.sort(
        np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]),
        axis=1,
    )
    edge_view = np.ascontiguousarray(edges).view([("a", edges.dtype), ("b", edges.dtype)])
    _, edge_counts = np.unique(edge_view, return_counts=True)
    boundary_edges = int(np.count_nonzero(edge_counts == 1))
    nonmanifold_edges = int(np.count_nonzero(edge_counts > 2))

    return {
        "success": True,
        "file": str(path),
        "file_size": size,
        "triangle_count": int(triangle_count),
        "unique_vertices": int(len(unique_vertices)),
        "bounding_box": {
            "min": bbox_min.tolist(),
            "max": bbox_max.tolist(),
            "dimensions": dimensions.tolist(),
            "center": center.tolist(),
        },
        "surface_area": surface_area,
        "signed_volume": signed_volume,
        "boundary_edges": boundary_edges,
        "nonmanifold_edges": nonmanifold_edges,
        "is_boundary_free": boundary_edges == 0,
        "is_edge_manifold": boundary_edges == 0 and nonmanifold_edges == 0,
        "notes": _stl_notes(boundary_edges, nonmanifold_edges),
    }


def _stl_notes(boundary_edges: int, nonmanifold_edges: int) -> list[str]:
    notes: list[str] = []
    if boundary_edges:
        notes.append("STL has open boundary edges; geometry solid import may fail.")
    if nonmanifold_edges:
        notes.append("STL has non-manifold edges; prefer mesh import or repair before Boolean/CFD use.")
    if not notes:
        notes.append("STL edge topology looks closed and manifold.")
    return notes
