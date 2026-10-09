from fsbench.checks import heredoc_python_errors


def test_a_shell_line_left_inside_a_python_heredoc_is_caught():
    # From authored draft c12: the commit belonged after the terminator.
    bad = "set -e\npython3 - <<'PYEOF'\nx = 1\ngit -c user.name=oncall commit -qam \"fix\"\nPYEOF\necho ok\n"
    assert heredoc_python_errors(bad) == ["the python heredoc at line 2 does not compile: line 4: invalid syntax"]
    good = bad.replace('git -c user.name=oncall commit -qam "fix"\nPYEOF', 'PYEOF\ngit commit -qam "fix"')
    assert heredoc_python_errors(good) == []


def test_heredoc_forms_and_an_unterminated_one():
    assert heredoc_python_errors('python <<"EOF"\nprint(1)\nEOF\ncat <<X\nnot python (\nX\n') == []
    assert heredoc_python_errors("python3 - <<EOF\nprint(1)\n") == ["the python heredoc at line 1 never ends with EOF"]
