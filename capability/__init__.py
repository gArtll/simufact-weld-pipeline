# -*- coding: utf-8 -*-
"""Capability layer: what the input geometry is, and what the tool can do with it.

    geometry.py   bodies, faces and surface evaluation from a STEP file
    classify.py   joint features: part kinds, contacts, joint type, seams, sides, groove, surface class
    matrix.yaml   the capability matrix: supported feature combinations and the backends that serve them
    planner.py    features -> plan (mesh backend, bead strategy, contact strategy, tooling inputs) or capability gaps

Nothing here knows a case name. A case is the output of the planner applied to a geometry, never an input to it."""
