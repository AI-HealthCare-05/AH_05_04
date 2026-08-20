import sys
from pathlib import Path

# tests/에서 실행해도 프로젝트 루트의 app 패키지를 import할 수 있도록 합니다.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
