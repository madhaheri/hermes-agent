"""Path-scoped skill activation follows files as the agent reads them."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _write_skill(root: Path, name: str, *, paths: str, body: str, extra: str = "") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Skill for {name}\npaths: {paths}\n{extra}---\n\n{body}\n",
        encoding="utf-8",
    )


def _agent(cwd: Path):
    return SimpleNamespace(
        _subdirectory_hints=SimpleNamespace(working_dir=cwd),
        valid_tool_names={"read_file", "skill_view", "skills_list"},
        platform="cli",
        session_id="test-session",
    )


def test_matching_read_activates_skill_but_unmatched_file_does_not(tmp_path):
    from agent.skill_path_activation import append_path_skill_context
    from tools.skills_tool import clear_skills_cache

    (tmp_path / "src").mkdir()
    _write_skill(tmp_path / "skills", "python-guide", paths='"src/**/*.py"', body="Use the project Python conventions.")
    with patch("tools.skills_tool.SKILLS_DIR", tmp_path / "skills"):
        clear_skills_cache()
        agent = _agent(tmp_path)
        result = append_path_skill_context(
            agent, [], "read_file", {"path": "src/main.py"}, "source contents",
        )
        assert "Use the project Python conventions." in result
        assert "source contents" in result
        assert "read_file" not in append_path_skill_context(
            agent, [], "read_file", {"path": "README.md"}, "readme",
        )


def test_path_activation_is_session_deduplicated_and_skips_disabled_model_skills(tmp_path):
    from agent.skill_path_activation import append_path_skill_context
    from tools.skills_tool import clear_skills_cache

    _write_skill(tmp_path / "skills", "guide", paths='"src/**/*.py"', body="Guide body.")
    _write_skill(
        tmp_path / "skills", "manual-only", paths='"src/**/*.py"', body="Manual body.",
        extra="disable-model-invocation: true\n",
    )
    with patch("tools.skills_tool.SKILLS_DIR", tmp_path / "skills"):
        clear_skills_cache()
        agent = _agent(tmp_path)
        messages = []
        first = append_path_skill_context(agent, messages, "read_file", {"path": "src/main.py"}, "code")
        assert "Guide body." in first
        assert "Manual body." not in first
        messages.append({"role": "tool", "content": first})
        second = append_path_skill_context(agent, messages, "read_file", {"path": "src/other.py"}, "more code")
        assert "Guide body." not in second
        assert "more code" in second


def test_path_activation_rejects_files_outside_working_directory(tmp_path):
    from agent.skill_path_activation import append_path_skill_context

    result = append_path_skill_context(
        _agent(tmp_path), [], "read_file", {"path": str(tmp_path.parent / "outside.py")}, "outside",
    )
    assert result == "outside"


def test_enabled_plugin_skill_activates_for_a_matching_file(tmp_path):
    import json
    from unittest.mock import patch

    from agent.skill_path_activation import append_path_skill_context
    from tools.skills_tool import clear_skills_cache

    skill_file = tmp_path / "plugins" / "example" / "skills" / "guide" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: guide\ndescription: Plugin guide\npaths: [\"src/**/*.py\"]\n---\nPlugin path guidance.\n",
        encoding="utf-8",
    )
    manager = SimpleNamespace(
        list_plugin_skill_metadata=lambda: [{"name": "example:guide"}],
        find_plugin_skill=lambda _name: skill_file,
    )
    with (
        patch("tools.skills_tool.SKILLS_DIR", tmp_path / "empty-skills"),
        patch("hermes_cli.plugins.discover_plugins"),
        patch("hermes_cli.plugins.get_plugin_manager", return_value=manager),
        patch("tools.skills_tool.skill_view", return_value=json.dumps({"success": True, "content": "Plugin path guidance."})),
    ):
        clear_skills_cache()
        result = append_path_skill_context(
            _agent(tmp_path), [], "read_file", {"path": "src/main.py"}, "source",
        )

    assert "Plugin path guidance." in result
