import sys
from pathlib import Path

# permite "import arbitraje" al correr pytest desde la raíz del repo
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
