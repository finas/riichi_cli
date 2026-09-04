"""Regression checks for user-facing documentation claims."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readmes_match_current_menu_and_verification_coverage():
    for filename in ("README.md", "README-CN.md"):
        text = (ROOT / filename).read_text(encoding="utf-8")
        assert "replay_screen.py" in text
        assert "tests/xml/failed/" in text
        assert "1000 passed" not in text

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "menu option 8" in readme
    assert "main menu option 7" in readme
