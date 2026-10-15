"""3MF export: one object made of named parts, each assigned to a filament slot.

Parts are standard 3MF components. Bambu Studio / OrcaSlicer read the per-part filament
("extruder") from Metadata/model_settings.config, so a terrain + route model opens as a
single object whose route is already set to a second AMS (lite) slot.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np

from .mesh import Mesh

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="text/xml"/>
</Types>
"""
RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


@dataclass
class Part:
    name: str
    mesh: Mesh
    filament: int = 1  # 1-based filament / AMS slot


def _mesh_xml(mesh: Mesh) -> str:
    v = mesh.vertices
    verts = "\n".join(
        f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in v.tolist()
    )
    tris = "\n".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces.tolist())
    return f"<mesh><vertices>\n{verts}\n</vertices><triangles>\n{tris}\n</triangles></mesh>"


def write_3mf(path: str | Path, parts: list[Part], name: str = "strava3d", bed_center=(90.0, 90.0)) -> None:
    """Write `parts` as one object, centred on the bed."""
    allv = np.vstack([p.mesh.vertices for p in parts])
    lo, hi = allv.min(axis=0), allv.max(axis=0)
    tx = bed_center[0] - (lo[0] + hi[0]) / 2
    ty = bed_center[1] - (lo[1] + hi[1]) / 2
    tz = -lo[2]
    object_id = len(parts) + 1

    resources = []
    for i, part in enumerate(parts, start=1):
        resources.append(f'<object id="{i}" type="model">{_mesh_xml(part.mesh)}</object>')
    components = "".join(f'<component objectid="{i}"/>' for i in range(1, len(parts) + 1))
    resources.append(f'<object id="{object_id}" type="model"><components>{components}</components></object>')

    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        f'<metadata name="Title">{_esc(name)}</metadata>\n'
        '<metadata name="Application">strava3d</metadata>\n'
        f"<resources>\n{chr(10).join(resources)}\n</resources>\n"
        f'<build><item objectid="{object_id}" transform="1 0 0 0 1 0 0 0 1 {tx:.4f} {ty:.4f} {tz:.4f}"/></build>\n'
        "</model>\n"
    )

    part_cfg = "".join(
        f'  <part id="{i}" subtype="normal_part">\n'
        f'    <metadata key="name" value={quoteattr(p.name)}/>\n'
        f'    <metadata key="extruder" value="{p.filament}"/>\n'
        "  </part>\n"
        for i, p in enumerate(parts, start=1)
    )
    settings = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
        f'  <object id="{object_id}">\n'
        f'    <metadata key="name" value={quoteattr(name)}/>\n'
        f'    <metadata key="extruder" value="{parts[0].filament}"/>\n'
        f"{part_cfg}  </object>\n</config>\n"
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("3D/3dmodel.model", model)
        zf.writestr("Metadata/model_settings.config", settings)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
