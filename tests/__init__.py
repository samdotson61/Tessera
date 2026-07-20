import os

# Unit tests must never spawn a real local model: auto-serve is opt-out here
# (the serving tests exercise the planner with explicit mocks instead).
os.environ.setdefault("TESSERA_AUTOSERVE", "0")
