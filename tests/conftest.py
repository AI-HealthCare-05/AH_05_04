import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# tests/에서 실행해도 backend/의 app 패키지를 import할 수 있도록 합니다.
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# tests/에 __init__.py가 없어 pytest가 테스트 디렉터리만 sys.path에 넣으므로,
# 저장소 루트의 ai_worker 패키지를 import하려면 루트도 함께 추가합니다.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
