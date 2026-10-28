from bot.handlers import _result_line, _welcome_keyboard
from bot.keyboards import admin_home, page_picker_keyboard, results_keyboard


def test_result_file_name_is_a_blue_telegram_post_link() -> None:
    line = _result_line(
        {
            "source_chat_id": "-1001234567890",
            "source_message_id": 42,
            "filename": "Movie & Series 2024.mkv",
            "size": 1_500_000_000,
            "quality": "1080P",
        }
    )
    assert '<a href="https://t.me/c/1234567890/42">' in line
    assert "Movie &amp; Series 2024.mkv" in line
    assert "</a>" in line


def test_results_keyboard_uses_filters_and_not_file_title_buttons() -> None:
    keyboard = results_keyboard(
        [{"file_key": "abc"}],
        page=0,
        total=11,
        filter_values={
            "languages": ["Hindi"],
            "year": ["2024"],
            "quality": ["1080P"],
            "episode": ["3"],
            "season": ["2"],
        },
    )
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert "send_all" in callbacks
    assert "filter:episode" in callbacks
    assert "page:picker" in callbacks
    assert "page:1" in callbacks
    assert not any(value.startswith("get:") for value in callbacks)


def test_admin_home_exposes_requested_sections() -> None:
    callbacks = [
        button.callback_data
        for row in admin_home({"bot_enabled": True}).inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert {"adm:dashboard", "adm:sources", "adm:live", "adm:subscription",
            "adm:requests:0", "adm:broadcast", "adm:timers", "adm:settings",
            "adm:cleanup"} <= set(callbacks)


def test_admin_welcome_has_no_slash_admin_command() -> None:
    keyboard = _welcome_keyboard({}, is_admin=True)
    assert keyboard is not None
    assert any(
        button.callback_data == "adm:home"
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_page_picker_shows_page_choices() -> None:
    keyboard = page_picker_keyboard(total=25, current_page=1)
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert "page:0" in callbacks
    assert "page:1" in callbacks
    assert "page:2" in callbacks
