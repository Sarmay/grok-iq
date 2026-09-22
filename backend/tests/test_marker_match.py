from app.marker_match import expected_marker_matched, normalize_marker_text


def test_marker_ignores_case_and_whitespace():
    assert expected_marker_matched("<!DOCTYPE html>", "```html\n<!doctype html>\n<html>")
    assert expected_marker_matched("RESULT=53", "计算过程略\nRESULT = 53")
    assert expected_marker_matched("探针校验通过", "**探针校验通过**")


def test_marker_ignores_full_width_forms():
    assert expected_marker_matched("RESULT=53", "ＲＥＳＵＬＴ＝５３")


def test_missing_marker_is_reported():
    assert not expected_marker_matched("RESULT=53", "RESULT=35")
    assert not expected_marker_matched("探针校验通过", "探针校验失败")


def test_empty_marker_always_matches():
    assert expected_marker_matched("", "anything")
    assert expected_marker_matched("   ", "")


def test_normalize_marker_text():
    assert normalize_marker_text(" A b\tC\n") == "abc"
