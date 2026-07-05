import sys
from pathlib import Path

# Ensure the backend package root is on sys.path when running from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main

app = main.app
for r in app.routes:
    print(r.path, sorted(list(r.methods)))
