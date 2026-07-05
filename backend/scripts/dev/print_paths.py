import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main
app = main.app
paths = app.openapi().get('paths', {})
for p in sorted(paths.keys()):
    print(p)
