"""Behavior tests for cache-safe skill-fork dispatch."""

import json
import os
from pathlib import Path
import subprocess
import sys

from agent.skill_fork import (
    ForkedSkillRequest,
    claim_forked_delegate,
    prepare_forked_skill_turn,
    register_forked_skill,
)


def test_fork_marker_hides_skill_body_and_dispatches_one_sync_child():
    request = ForkedSkillRequest(
        name="deep-research",
        invocation="/deep-research GPU inference",
        goal="Research GPU inference and cite findings.",
        context="GPU inference",
    )
    marker = register_forked_skill(request)

    user_message, persisted, title, prelude = prepare_forked_skill_turn(
        marker, marker, marker, None,
    )
    assert user_message == request.invocation
    assert persisted == request.invocation
    assert title == request.invocation
    assert request.goal not in user_message
    assert prelude is not None

    _content, tool_name, arguments = next(prelude)
    assert tool_name == "delegate_task"
    assert request.goal not in str(arguments)
    assert "deep-research" in arguments["goal"]
    assert claim_forked_delegate(arguments["goal"]) == request
    assert claim_forked_delegate(arguments["goal"]) is None
    try:
        next(prelude)
    except StopIteration:
        pass
    else:
        raise AssertionError("fork prelude should finish after the single child result")


def test_fork_marker_is_replaced_when_surface_prepends_turn_local_context():
    request = ForkedSkillRequest(
        name="deep-research", invocation="/deep-research subject",
        goal="Research subject.", context="subject",
    )
    marker = register_forked_skill(request)
    user_message = f"Transient turn note.\n\n{marker}"

    message, persisted, title, prelude = prepare_forked_skill_turn(
        user_message, marker, marker, None,
    )

    assert message == "Transient turn note.\n\n/deep-research subject"
    assert persisted == title == "/deep-research subject"
    assert prelude is not None


def test_agent_dispatches_the_child_goal_only_inside_the_fork_prelude():
    from types import SimpleNamespace
    from unittest.mock import patch

    from agent.skill_fork import _fork_prelude, dispatch_forked_delegate

    request = ForkedSkillRequest(
        name="deep-research", invocation="/deep-research topic",
        goal="Research topic privately.", context="topic",
    )
    prelude = _fork_prelude(request)
    _content, _name, arguments = next(prelude)
    parent = SimpleNamespace(_delegate_depth=0)
    with patch("tools.delegate_tool.delegate_task", return_value='{"success": true}') as delegate:
        result = dispatch_forked_delegate(parent, arguments)

    assert result == '{"success": true}'
    delegate.assert_called_once_with(
        goal=request.goal, context=request.context, role="leaf", background=False,
        parent_agent=parent,
    )
    assert arguments["goal"] != request.goal


def test_fork_request_with_unsupported_frontmatter_never_builds_a_child_prelude():
    request = ForkedSkillRequest(
        name="deep-research",
        invocation="/deep-research topic",
        goal="Hidden body must not run.",
        context="topic",
        failure_reason="named agent profiles are not supported yet",
    )
    marker = register_forked_skill(request)

    user_message, persisted, title, prelude = prepare_forked_skill_turn(
        marker, marker, marker, None,
    )

    assert "did not run this skill" in user_message
    assert "do not perform the task inline" in user_message
    assert request.goal not in user_message
    assert persisted == request.invocation
    assert title == request.invocation
    assert prelude is None


def test_expired_or_unknown_fork_marker_fails_closed():
    user_message, persisted, title, prelude = prepare_forked_skill_turn(
        "\x1eHERMES_SKILL_FORK_V1:unknown",
        "\x1eHERMES_SKILL_FORK_V1:unknown",
        "\x1eHERMES_SKILL_FORK_V1:unknown",
        None,
    )

    assert "expired before execution" in user_message
    assert persisted == user_message
    assert title == "Expired isolated skill request"
    assert prelude is None


def test_fork_marker_can_be_redeemed_by_a_different_process_without_carrying_skill_body(tmp_path):
    from unittest.mock import patch

    from agent.skill_commands import build_skill_invocation_message, scan_skill_commands

    home = tmp_path / "hermes-home"
    skill = home / "skills" / "fork-probe"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: fork-probe\ncontext: fork\narguments: [topic]\n---\n"
        "REMOTE_FORK_BODY: $ARGUMENTS\n",
        encoding="utf-8",
    )
    with patch("tools.skills_tool.SKILLS_DIR", home / "skills"):
        scan_skill_commands()
        marker = build_skill_invocation_message(
            "/fork-probe", "inference routing", dispatch_fork=True,
        )

        assert marker is not None
        assert "REMOTE_FORK_BODY" not in marker
        root = Path(__file__).resolve().parents[2]
        child_env = {
            key: os.environ[key] for key in ("PATH", "LANG", "TZ") if key in os.environ
        }
        child_env.update(HOME=str(tmp_path), HERMES_HOME=str(home), PYTHONPATH=str(root))
        code = """
import json, sys
from agent.skill_fork import prepare_forked_skill_turn, claim_forked_delegate
marker = sys.argv[1]
message, persisted, title, prelude = prepare_forked_skill_turn(
    marker, marker, marker, None, task_id='session-1')
content, tool, arguments = next(prelude)
request = claim_forked_delegate(arguments['goal'])
print(json.dumps({'message': message, 'persisted': persisted, 'title': title,
                  'tool': tool, 'goal': request.goal if request else None}))
"""
        completed = subprocess.run(
            [sys.executable, "-c", code, marker], cwd=root, env=child_env,
            capture_output=True, text=True, check=True, timeout=30,
        )

    result = json.loads(completed.stdout.splitlines()[-1])
    assert result["message"] == "/fork-probe inference routing"
    assert result["persisted"] == result["message"]
    assert result["title"] == result["message"]
    assert result["tool"] == "delegate_task"
    assert result["goal"] == "REMOTE_FORK_BODY: inference routing"
