"""Execute the production remote program through stdin with consuming Docker doubles."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/deployment.sh"
COUNTS = {
    "user": 2,
    "self_profile": 2,
    "medical_document_profile_null": 0,
    "prescription_profile_null": 0,
    "guide_profile_null": 0,
    "chat_session_profile_null": 0,
    "prescription_profile_mismatch": 0,
    "guide_profile_mismatch": 0,
    "chat_session_profile_mismatch": 0,
}


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "zero",
        "count_difference",
        "mismatch",
        "empty",
        "missing",
        "duplicate",
        "invalid",
        "sql_failure",
        "migration_failure",
    ],
)
def test_remote_stdin_and_profile_gate(tmp_path, case):
    dash = shutil.which("dash")
    assert dash, "This regression requires dash (the PostgreSQL image /bin/sh), including on developer machines."
    rows = [[name, str(value)] for name, value in COUNTS.items()]
    if case == "zero":
        rows = [[name, "0"] for name in COUNTS]
    elif case == "count_difference":
        rows[1][1] = "1"
    elif case == "mismatch":
        rows[-1][1] = "1"
    elif case == "empty":
        rows = []
    elif case == "missing":
        rows.pop()
    elif case == "duplicate":
        rows.append(rows[0])
    elif case == "invalid":
        rows[-1][1] = "not-a-count"

    (tmp_path / "project").mkdir()
    psql = tmp_path / "psql"
    psql.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "sql = sys.stdin.read()\n"
        "assert 'FROM chat_session' in sql\n"
        "Path(os.environ['SQL_LOG']).write_text(sql)\n"
        "if os.environ['CASE'] == 'sql_failure': sys.exit(17)\n"
        "separator = sys.argv[sys.argv.index('-F') + 1]\n"
        "for row in json.loads(os.environ['ROWS']): print(separator.join(row))\n"
    )
    psql.chmod(0o700)
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import os, shlex, subprocess, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['COMMAND_LOG'], 'a') as log: log.write(shlex.join(args) + '\\n')\n"
        "stdin = sys.stdin.read()\n"
        "if 'CREATE TEMP TABLE' in stdin:\n"
        "    with open(os.environ['SNAPSHOT_LOG'], 'a') as log: log.write(stdin)\n"
        "    print('alembic_revision\\ttest-head')\n"
        "else:\n"
        "    assert not stdin, 'Docker consumed deployment program instead of command input'\n"
        "if args == ['wait', 'migrate']:\n"
        "    print(23 if os.environ['CASE'] == 'migration_failure' else 0)\n"
        "if args[:4] == ['compose', 'exec', '-T', 'postgres'] and 'FROM chat_session' in args[-1]:\n"
        "    command = args[-1].replace('psql', shlex.quote(os.environ['PSQL_STUB']), 1)\n"
        "    sys.exit(subprocess.run([os.environ['DASH'], '-lc', command]).returncode)\n"
    )
    docker.chmod(0o700)
    source = SCRIPT.read_text()
    remote = source.split("bash -s\" <<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0]
    # Same SSH stdin transport and outer success message, without registry/network actions.
    success_echo = source.split("\nEOF\n", 1)[1].splitlines()[1]
    invocation = f"set -e\nbash -s <<'EOF'\n{remote}\nEOF\n{success_echo}\n"
    result = subprocess.run(
        ["bash", "-c", invocation],
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "DEPLOY_SERVICES": "fastapi ai-worker nginx",
            "COLOR_GREEN": "",
            "COLOR_NC": "",
            "CASE": case,
            "ROWS": json.dumps(rows),
            "PSQL_STUB": str(psql),
            "DASH": dash,
            "COMMAND_LOG": str(tmp_path / "commands"),
            "SQL_LOG": str(tmp_path / "sql"),
            "SNAPSHOT_LOG": str(tmp_path / "snapshots"),
        },
        text=True,
        capture_output=True,
        timeout=15,
    )
    commands = (tmp_path / "commands").read_text()
    assert "--force-recreate migrate" in commands, result.stderr
    snapshots = (tmp_path / "snapshots").read_text()
    assert snapshots.count("CREATE TEMP TABLE") == (1 if case == "migration_failure" else 2)
    if case != "migration_failure":
        assert "WHERE chat_session.profile_id" in (tmp_path / "sql").read_text()
    valid = case in {"valid", "zero"}
    assert (result.returncode == 0) is valid, result.stdout + result.stderr
    assert ("--wait fastapi ai-worker nginx" in commands) is valid
    assert ("Deployment finished." in result.stdout) is valid
    assert ("image prune" in commands) is valid
    if valid:
        assert commands.index("verify-db-head") < commands.index("--wait fastapi ai-worker nginx")
