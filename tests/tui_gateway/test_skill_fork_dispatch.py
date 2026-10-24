"""TUI slash resolution stays body-free and tolerates a turn-process handoff."""

from unittest.mock import patch
from typing import Any, cast

from agent.skill_fork import _consume_forked_skill, claim_forked_delegate, prepare_forked_skill_turn
from tools.skills_tool import clear_skills_cache


def test_tui_skill_dispatch_marker_is_redeemed_after_registry_boundary(tmp_path, monkeypatch):
    from tui_gateway import server
    from agent import skill_commands

    skill = tmp_path / "fork-probe"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: fork-probe\ncontext: fork\narguments: [topic]\n---\n"
        "TUI_FORK_INSTRUCTION_71C: $ARGUMENTS\n",
        encoding="utf-8",
    )
    with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
        clear_skills_cache()
        commands = {"/fork-probe": {"name": "fork-probe", "skill_dir": str(skill)}}
        monkeypatch.setattr(skill_commands, "get_interactive_skill_commands", lambda: commands)
        monkeypatch.setattr(server, "_tools_mod", lambda _module: skill_commands)
        dispatch_skill = cast(Any, server)._dispatch_skill
        reply = dispatch_skill(
            "dispatch", {}, {"session_key": "tui-fork-session"},
            "fork-probe", "remote owner check",
        )
        directive = reply["result"]
        marker = directive["message"]

        assert directive["type"] == "skill"
        assert directive["display"] == "/fork-probe remote owner check"
        assert "TUI_FORK_INSTRUCTION_71C" not in marker
        assert _consume_forked_skill(marker) is not None  # another process cannot share this registry
        invocation, persisted, title, prelude = prepare_forked_skill_turn(
            marker, marker, marker, None, task_id="tui-fork-session",
        )

    assert invocation == "/fork-probe remote owner check"
    assert persisted == invocation
    assert title == invocation
    assert prelude is not None
    _content, _tool, arguments = next(prelude)
    request = claim_forked_delegate(arguments["goal"])
    assert request is not None
    assert request.goal == "TUI_FORK_INSTRUCTION_71C: remote owner check"
