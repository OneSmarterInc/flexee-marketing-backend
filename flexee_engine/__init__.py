"""The engine seam. Everything Django needs from the simulation engine is in ``api``.

``vendor/`` is prepended to sys.path so the verified engine's top-level imports
(``from game_faculty import ...``) resolve once those modules are dropped in.
"""

import os
import sys

_VENDOR = os.path.join(os.path.dirname(__file__), "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
