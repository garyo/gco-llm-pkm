"""Tests for the three-way merge used on save."""

from pkm_bridge.merge import three_way_merge

BASE = "* Monday\n- coffee\n- email\n"


def test_identical_sides_are_unchanged():
    result = three_way_merge(BASE, BASE + "x\n", BASE + "x\n")
    assert result.clean and result.merged == BASE + "x\n"


def test_only_mine_changed_is_a_plain_write():
    mine = BASE + "- gym\n"
    result = three_way_merge(BASE, mine, BASE)
    assert result.clean and result.merged == mine


def test_only_theirs_changed_yields_theirs():
    theirs = "* Tuesday\n" + BASE
    result = three_way_merge(BASE, BASE, theirs)
    assert result.clean and result.merged == theirs


def test_append_and_prefix_edit_merge_cleanly():
    mine = "* Monday (rainy)\n- coffee\n- email\n"  # edited line 1
    theirs = BASE + "- Claude appended this\n"  # appended at the end
    result = three_way_merge(BASE, mine, theirs)
    assert result.clean
    assert result.merged == "* Monday (rainy)\n- coffee\n- email\n- Claude appended this\n"


def test_same_line_edits_conflict_with_markers():
    mine = "* Monday\n- coffee (large)\n- email\n"
    theirs = "* Monday\n- coffee (decaf)\n- email\n"
    result = three_way_merge(BASE, mine, theirs)
    assert not result.clean
    assert (
        "<<<<<<< mine\n- coffee (large)\n=======\n- coffee (decaf)\n>>>>>>> disk\n" in result.merged
    )


def test_both_append_different_lines_at_the_end_conflicts():
    # Appends to the same spot are genuinely ambiguous in order; diff3 reports them.
    result = three_way_merge(BASE, BASE + "- mine\n", BASE + "- theirs\n")
    assert not result.clean
    assert "- mine\n" in result.merged and "- theirs\n" in result.merged


def test_missing_trailing_newline_is_not_a_conflict():
    base = "a\nb"
    mine = "a\nb\n"  # editor added the final newline
    theirs = "a\nb\nc\n"  # someone appended a line
    result = three_way_merge(base, mine, theirs)
    assert result.clean and result.merged == "a\nb\nc\n"


def test_crlf_on_disk_is_preserved():
    base = "one\r\ntwo\r\n"
    mine = "one\ntwo\nthree\n"  # editor normalised to LF
    theirs = "zero\r\none\r\ntwo\r\n"
    result = three_way_merge(base, mine, theirs)
    assert result.clean and result.merged == "zero\r\none\r\ntwo\r\nthree\r\n"


def test_empty_base_treats_both_as_additions():
    result = three_way_merge("", "mine\n", "theirs\n")
    assert not result.clean
