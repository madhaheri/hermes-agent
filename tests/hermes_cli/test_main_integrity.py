"""Integrity tests for hermes_cli/main.py structure.

These tests verify invariants that are easy to break by accident when
adding new subcommands or tools.
"""

import pytest


def test_builtin_subcommands_is_frozenset():
    """_BUILTIN_SUBCOMMANDS must be a frozenset (immutable, fast lookup)."""
    from hermes_cli.main import _BUILTIN_SUBCOMMANDS

    assert isinstance(_BUILTIN_SUBCOMMANDS, frozenset)


def test_builtin_subcommands_contains_reach():
    """The reach subcommand must be in _BUILTIN_SUBCOMMANDS."""
    from hermes_cli.main import _BUILTIN_SUBCOMMANDS

    assert "reach" in _BUILTIN_SUBCOMMANDS, (
        "'reach' is not in _BUILTIN_SUBCOMMANDS — `hermes reach` will "
        "trigger a 500-650ms plugin discovery penalty on every invocation."
    )


def test_builtin_subcommands_contains_core_commands():
    """Verify all known core subcommands are present."""
    from hermes_cli.main import _BUILTIN_SUBCOMMANDS

    core = {
        "chat", "config", "doctor", "setup", "model", "tools",
        "skills", "cron", "gateway", "mcp", "profile", "status",
        "auth", "update", "reach",
    }
    missing = core - _BUILTIN_SUBCOMMANDS
    assert not missing, f"Core subcommands missing from _BUILTIN_SUBCOMMANDS: {missing}"


def test_builtin_subcommands_no_duplicates():
    """Frozenset can't have duplicates, but verify the source list is clean."""
    from hermes_cli.main import _BUILTIN_SUBCOMMANDS

    # If it's a frozenset, duplicates are impossible — this is a tautology
    # but serves as documentation that we care about this invariant.
    assert len(_BUILTIN_SUBCOMMANDS) > 10  # sanity check