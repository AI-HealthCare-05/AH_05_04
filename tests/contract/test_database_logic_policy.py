from hashlib import sha256

from scripts.ci.check_database_logic import forbidden_definitions, violations


def test_rejects_multiline_and_comment_separated_definitions() -> None:
    for words in (
        ["CREATE", "CONSTRAINT", "TRIGGER", "example"],
        ["CREATE", "OR", "REPLACE", "TRIGGER", "example"],
        ["ALTER TABLE example ENABLE", "ROW", "LEVEL", "SECURITY"],
        ["ALTER TABLE example FORCE", "ROW", "LEVEL", "SECURITY"],
        ["CREATE", "POLICY", "example"],
        ["RETURNS", "trigger"],
    ):
        assert forbidden_definitions("\n".join(words))
        assert forbidden_definitions(" /* separator */ ".join(words))


def test_removal_and_domain_names_remain_allowed() -> None:
    assert not forbidden_definitions("DROP TRIGGER IF EXISTS old_guard ON example")
    assert not forbidden_definitions("ALTER TABLE example DISABLE ROW LEVEL SECURITY")
    assert not forbidden_definitions("trigger_catalog = []")


def test_legacy_exception_cannot_hide_changed_file(tmp_path) -> None:
    path = tmp_path / "old.sql"
    original = " ".join(["CREATE", "TRIGGER", "legacy"])
    path.write_text(original)
    baseline = {"old.sql": sha256(original.encode()).hexdigest()}
    assert violations(tmp_path, ["old.sql"], baseline) == []
    path.write_text(original + "\n-- modified")
    assert violations(tmp_path, ["old.sql"], baseline) == ["old.sql"]
    path.write_text("DROP TRIGGER legacy ON example")
    assert violations(tmp_path, ["old.sql"], baseline) == []
