from automation.orchestrator.bridge import completion_markers_satisfied


def test_completion_markers_accept_exact_line():
    assert completion_markers_satisfied(
        "NETWORK_CORRELATION_OK_2026",
        ["NETWORK_CORRELATION_OK_2026"],
    )


def test_completion_markers_accept_colon_suffix():
    assert completion_markers_satisfied(
        "NETWORK_CORRELATION_OK_2026: verified",
        ["NETWORK_CORRELATION_OK_2026"],
    )


def test_completion_markers_reject_stale_nonmatching_response():
    assert not completion_markers_satisfied(
        "Good — this is the previous assistant response.",
        ["NETWORK_CORRELATION_OK_2026"],
    )


def test_completion_markers_allow_unmarked_operations():
    assert completion_markers_satisfied(
        "A normal response",
        None,
    )
