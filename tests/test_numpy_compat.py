"""The hosted server installs the newest NumPy, which removes things an older one only warns about (np.cross on 2D vectors was one).
Run the geometry code with every NumPy deprecation turned into an error, so that cannot slip through on a developer's older install."""
import warnings
import numpy as np
import fitz
from core import geometry

warnings.simplefilter("error", DeprecationWarning)
warnings.simplefilter("error", FutureWarning)

# the simplifier directly: a square with a jog, a straight run, and a degenerate loop
sq = [(0, 0), (10, 0), (10, 0.2), (10, 10), (0, 10)]
out = geometry._simplify(sq, 0.5)
assert len(out) == 4, out
assert len(geometry._simplify([(0, 0), (5, 0), (10, 0), (10, 5), (10, 10)], 0.1)) >= 3
assert geometry._simplify([(0, 0), (0, 0), (0, 0), (0, 0)], 0.1) is not None
print("PASS simplify runs with deprecations as errors, and drops a 0.2 jog:", len(out), "corners")

# a whole room pick through the same path the server uses
d = fitz.open()
p = d.new_page(width=842, height=595)
for a, b in (((100, 100), (400, 100)), ((400, 100), (400, 400)), ((400, 400), (100, 400)), ((100, 400), (100, 100))):
    p.draw_line(a, b, color=(0, 0, 0), width=1.0)
import tempfile, os
f = os.path.join(tempfile.mkdtemp(), "room.pdf")
d.save(f)
pts = geometry.room_polygon(f, 0, 250, 250)
assert pts and len(pts) >= 4
print("PASS a room outline is found with deprecations as errors:", len(pts), "points, numpy", np.__version__)
