import sys
from pathlib import Path

# tests/에서 실행해도 backend/의 app 패키지를 import할 수 있도록 합니다.
BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
