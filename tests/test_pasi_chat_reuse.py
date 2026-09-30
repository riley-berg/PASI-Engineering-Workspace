from scripts.pasi_chat import select_chat_mode


def test_select_chat_mode_reuses_active_chat_even_when_stale_exhaustion_flags_are_set():
    live = {
        "chat_url": "https://chatgpt.com/c/current",
        "chat_exhausted": True,
        "usage_limited": True,
    }
    state = {
        "chat_url": "https://chatgpt.com/c/current",
        "chat_exhausted": True,
        "usage_limited": True,
    }

    assert select_chat_mode(state, live) == "reuse"


def test_select_chat_mode_creates_only_when_no_active_chat_is_known():
    live = {"chat_url": None, "chat_exhausted": True, "usage_limited": True}
    state = {"chat_url": None, "chat_exhausted": True, "usage_limited": True}

    assert select_chat_mode(state, live) == "new_chat"
