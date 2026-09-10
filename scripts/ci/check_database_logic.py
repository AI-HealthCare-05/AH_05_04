"""새 Trigger·RLS 정의를 차단하고 기존 제거 대상을 정확한 파일 hash로 한정합니다."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "docs/testing/issue-398/legacy-database-logic.json"
FORBIDDEN = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?TRIGGER\b"
    r"|\bRETURNS\s+(?:EVENT_)?TRIGGER\b"
    r"|\b(?:ENABLE|FORCE)\s+ROW\s+LEVEL\s+SECURITY\b"
    r"|\bCREATE\s+POLICY\b",
    re.IGNORECASE,
)


def forbidden_definitions(source: str) -> bool:
    # SQL 주석으로 키워드를 나눈 구문도 같은 정의로 검사합니다.
    normalized = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    normalized = re.sub(r"--[^\n]*", " ", normalized)
    return FORBIDDEN.search(normalized) is not None


def violations(root: Path, paths: list[str], legacy: dict[str, str]) -> list[str]:
    failures = []
    for name in paths:
        path = root / name
        if path.suffix not in {".py", ".sql", ".sh", ".yaml", ".yml"} or not path.is_file():
            continue
        content = path.read_bytes()
        if not forbidden_definitions(content.decode("utf-8")):
            continue
        if hashlib.sha256(content).hexdigest() != legacy.get(name):
            failures.append(name)
    return failures


def main() -> int:
    baseline = json.loads(BASELINE.read_text())
    paths = (
        subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
        .decode()
        .split("\0")
    )
    failures = violations(ROOT, paths, baseline["files"])
    if failures:
        print("새 Trigger/RLS 정의 또는 기존 제거 대상 변경을 발견했습니다:")
        print("\n".join(failures))
        return 1
    print("Trigger/RLS 재도입 검사 통과 (기존 제거 대상은 고정 hash로만 임시 허용)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
