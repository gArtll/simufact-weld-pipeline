# -*- coding: utf-8 -*-
"""Structured explicit transition meshes: 2D quad topology, built by hand, checked, drawn.

Round 1 scope (structured_multi_ratio_transition_mesh_plan.md, section 30): straight boundary, one transition row,
the reference T2_pair and T3 templates, their periodic tilings and one mixed case. Nothing here touches `hm/` or any
case: no HyperMesh, no Simufact, no automesh. Pure Python plus numpy.

Vocabulary: a *cell* turns one coarse edge into r fine edges (r = 2 or 3). A *template* is the smallest region that
can be filled with quadrilaterals on its own: T3 is one cell, T2_pair is two adjacent cells. A *strip* is templates
laid side by side, welded on their shared vertical edge."""
from .mesh import Mesh, check_all
from .templates import t2_pair, t3, strip
from .quality import quality_table, quality_rows

__all__ = ["Mesh", "check_all", "t2_pair", "t3", "strip", "quality_table", "quality_rows"]
