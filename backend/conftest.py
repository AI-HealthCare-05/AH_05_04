import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# backend/에서 실행해도 저장소 루트의 provider_contracts 등을 import할 수 있도록 합니다.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
