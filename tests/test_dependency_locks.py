from scripts.dependency_locks import normalize_line_endings


def test_dependency_lock_comparison_ignores_platform_line_endings():
    assert normalize_line_endings(b"package==1.0\r\nnext==2.0\r\n") == b"package==1.0\nnext==2.0\n"
    assert normalize_line_endings(b"package==1.0\r\n") != normalize_line_endings(b"package==2.0\n")
