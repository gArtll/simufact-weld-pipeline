# -*- coding: utf-8 -*-
"""Write STEP files of boxes (solid bodies with shared vertices and edges), for joint classification tests.

A box is enough to make every joint type the classifier knows: a thin box standing on a wide one is a T joint, two
plates edge to edge a butt joint, two overlapping plates a lap joint. None of these is one of the repository's
regression cases, which is the point."""
import io


class StepWriter(object):
    def __init__(self):
        self.lines, self.n = [], 0

    def add(self, text):
        self.n += 1
        self.lines.append("#%d = %s ;" % (self.n, text))
        return self.n

    def pt(self, p):
        return self.add("CARTESIAN_POINT ( 'NONE', ( %r, %r, %r ) )" % tuple(float(v) for v in p))

    def dirn(self, d):
        return self.add("DIRECTION ( 'NONE', ( %r, %r, %r ) )" % tuple(float(v) for v in d))

    def box(self, name, lo, hi):
        x0, y0, z0 = lo
        x1, y1, z1 = hi
        c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        return self.hexa(name, c, normals=[(0, 0, -1), (0, 0, 1), (0, -1, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0)])

    def hexa(self, name, c, normals=None):
        """A solid with 8 corners in box order (0-3 one end, 4-7 the other) and planar faces; the outward normal of
        each face is computed from its corners when not given."""
        v = [self.add("VERTEX_POINT ( 'NONE', #%d )" % self.pt(p)) for p in c]
        edges = {}

        def edge(a, b):
            key = tuple(sorted((a, b)))
            if key not in edges:
                p, q = c[key[0]], c[key[1]]
                L = sum((q[i] - p[i]) ** 2 for i in range(3)) ** 0.5
                d = self.dirn([(q[i] - p[i]) / L for i in range(3)])
                line = self.add("LINE ( 'NONE', #%d, #%d )" % (self.pt(p), self.add("VECTOR ( 'NONE', #%d, %r )" % (d, L))))
                edges[key] = self.add("EDGE_CURVE ( 'NONE', #%d, #%d, #%d, .T. )" % (v[key[0]], v[key[1]], line))
            return edges[key], (a, b) == key
        loops = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        if normals is None:
            cen = [sum(p[i] for p in c) / 8.0 for i in range(3)]
            normals = []
            for q in loops:
                a, b, d = c[q[0]], c[q[1]], c[q[3]]
                u = [b[i] - a[i] for i in range(3)]
                w = [d[i] - a[i] for i in range(3)]
                n = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]]
                L = sum(x * x for x in n) ** 0.5
                n = [x / L for x in n]
                if sum(n[i] * (a[i] - cen[i]) for i in range(3)) < 0:
                    n = [-x for x in n]
                normals.append(tuple(round(x, 15) + 0.0 for x in n))
        quads = list(zip(loops, normals))
        faces = []
        for q, n in quads:
            oes = []
            for k in range(4):
                e, same = edge(q[k], q[(k + 1) % 4])
                oes.append(self.add("ORIENTED_EDGE ( 'NONE', *, *, #%d, %s )" % (e, ".T." if same else ".F.")))
            loop = self.add("EDGE_LOOP ( 'NONE', ( %s ) )" % ", ".join("#%d" % o for o in oes))
            bound = self.add("FACE_OUTER_BOUND ( 'NONE', #%d, .T. )" % loop)
            ref = (1, 0, 0) if abs(n[0]) < 0.5 else (0, 1, 0)
            ax = self.add("AXIS2_PLACEMENT_3D ( 'NONE', #%d, #%d, #%d )" % (self.pt(c[q[0]]), self.dirn(n), self.dirn(ref)))
            faces.append(self.add("ADVANCED_FACE ( 'NONE', ( #%d ), #%d, .T. )" % (bound, self.add("PLANE ( 'NONE', #%d )" % ax))))
        shell = self.add("CLOSED_SHELL ( 'NONE', ( %s ) )" % ", ".join("#%d" % f for f in faces))
        return self.add("MANIFOLD_SOLID_BREP ( '%s', #%d )" % (name, shell)), faces

    def write(self, path):
        io.open(path, "w", encoding="latin-1", newline="\n").write(
            "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION (( 'STEP AP203' ), '1' );\nENDSEC;\n\nDATA;\n"
            + "\n".join(self.lines) + "\nENDSEC;\nEND-ISO-10303-21;\n")


def t_joint(path):
    s = StepWriter()
    s.box("plate", (0, -10, 0), (200, 0, 400))
    s.box("web", (97, 0, 0), (103, 100, 400))
    s.write(path)


def butt_joint(path):
    s = StepWriter()
    s.box("left", (0, -10, 0), (100, 0, 400))
    s.box("right", (100, -10, 0), (200, 0, 400))
    s.write(path)


def lap_joint(path):
    s = StepWriter()
    s.box("lower", (0, 0, 0), (100, 10, 400))
    s.box("upper", (50, 10, 0), (150, 20, 400))
    s.write(path)


def tapered_t_joint(path):
    """A web on a plate whose length grows across its width: 400 mm at x = 0, 440 mm at x = 200, so the two
    plate ends are oblique planes. The plate is a sweep along the web normal (x) of one profile topology, but not
    an extrusion: its end profiles differ."""
    s = StepWriter()
    s.hexa("plate", [(0, -10, 0), (200, -10, -20), (200, 0, -20), (0, 0, 0),
                     (0, -10, 400), (200, -10, 420), (200, 0, 420), (0, 0, 400)])
    s.box("web", (97, 0, 0), (103, 100, 400))
    s.write(path)
