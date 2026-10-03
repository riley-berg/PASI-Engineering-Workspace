from scripts.pasi_chat import select_chat_mode


def test_select_chat_mode_reuses_current_chat_when_no_exhaustion_is_reported():
    live = {
        "chat_url": "https://chatgpt.com/c/current",
        "chat_exhausted": False,
        "usage_limited": False,
        "connection_failure": True,
    }
    state = {
        "chat_url": "https://chatgpt.com/c/current",
        "chat_exhausted": False,
        "usage_limited": False,
    }

    assert select_chat_mode(state, live) == "reuse"


def test_select_chat_mode_creates_new_chat_for_explicit_exhaustion():
    live = {
        "chat_url": "https://chatgpt.com/c/current",
        "chat_exhausted": True,
        "usage_limited": False,
    }
    assert select_chat_mode({}, live) == "new_chat"


def test_select_chat_mode_blocks_explicit_usage_limit():
    live = {
        "chat_url": "https://chatgpt.com/c/current",
        "chat_exhausted": False,
        "usage_limited": True,
    }
    assert select_chat_mode({}, live) == "blocked"
