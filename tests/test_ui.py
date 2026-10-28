from bot.handlers import _result_header, _result_line, _welcome_keyboard
from bot.keyboards import admin_home, page_picker_keyboard, results_keyboard, settings_menu, source_list


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
            "adm:cleanup", "adm:groups", "adm:duplicates"} <= set(callbacks)


def test_result_header_shows_requester_timing_and_admin_branding() -> None:
    header = _result_header("KGF", "Varun", 1.07, 11, "Daya Movie Store")
    assert "KGF" in header
    assert "Varun" in header
    assert "1.07 seconds" in header
    assert "Daya Movie Store" in header


def test_result_header_escapes_user_supplied_html() -> None:
    header = _result_header("<KGF>", "A&B", 0.5, 1, "<brand>")
    assert "&lt;KGF&gt;" in header
    assert "A&amp;B" in header
    assert "&lt;brand&gt;" in header


def test_admin_menus_expose_brand_and_index_progress_controls() -> None:
    settings_callbacks = [
        button.callback_data
        for row in settings_menu({"powered_by": "Test"}).inline_keyboard
        for button in row
        if button.callback_data
    ]
    source_callbacks = [
        button.callback_data
        for row in source_list([], True).inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert "adm:powered_by" in settings_callbacks
    assert "adm:index_status" in source_callbacks


def test_result_header_shows_requester_timing_and_admin_branding() -> None:
    header = _result_header("KGF", "Varun", 1.07, 11, "Daya Movie Store")
    assert "KGF" in header
    assert "Varun" in header
    assert "1.07 seconds" in header
    assert "Daya Movie Store" in header


def test_result_header_escapes_user_supplied_html() -> None:
    header = _result_header("<KGF>", "A&B", 0.5, 1, "<brand>")
    assert "&lt;KGF&gt;" in header
    assert "A&amp;B" in header
    assert "&lt;brand&gt;" in header


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
