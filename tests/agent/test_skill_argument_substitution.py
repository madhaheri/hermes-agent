"""Tests for slash-skill argument interpolation."""

from agent.skill_preprocessing import substitute_skill_arguments


def test_argument_substitution_preserves_windows_paths_and_escape_rules():
    content = r"$0|$1|$2|$unknown|\$1|\\$1|\$unknown|$ARGUMENTS[8]"
    expanded, boundary, consumed = substitute_skill_arguments(
        content, r'C:\Users\A\file.txt "two words"',
    )

    assert expanded == r"C:\Users\A\file.txt|two words|$2|$unknown|$1|\\two words|\$unknown|$ARGUMENTS[8]"
    assert boundary == 0
    assert consumed is True


def test_named_arguments_expand_by_declared_position_and_missing_named_values_are_empty():
    expanded, boundary, consumed = substitute_skill_arguments(
        "$issue/$branch/$ARGUMENTS", "42", ["issue", "branch"],
    )

    assert expanded == "42//42"
    assert boundary == 0
    assert consumed is True


def test_user_argument_text_is_inserted_once_without_rescanning_placeholders():
    expanded, _, _ = substitute_skill_arguments(
        "Summarize $0", '"$1 and !`not-a-command`"',
    )

    assert expanded == "Summarize $1 and !`not-a-command`"
