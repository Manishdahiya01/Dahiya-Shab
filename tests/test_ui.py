import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.error import BadRequest

from bot.handlers import (
    _is_message_not_modified_error,
    _result_header,
    _result_line,
    _subscription_count_text,
    _welcome_keyboard,
    group_post,
)
from bot.keyboards import (
    admin_home,
    filter_options_keyboard,
    page_picker_keyboard,
    results_keyboard,
    settings_menu,
    source_list,
    subscription_list,
    subscription_remove_list,
)


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


def test_results_keyboard_matches_reference_grid_and_includes_all_filters() -> None:
    keyboard = results_keyboard(
        [{"file_key": "abc"}],
        page=0,
        total=90,
        filter_values={
            "languages": ["Hindi"],
            "year": ["2024"],
            "quality": ["1080P"],
        },
    )
    rows = keyboard.inline_keyboard
    labels = [[button.text for button in row] for row in rows]
    callbacks = [button.callback_data for row in rows for button in row]
    assert "send_all" in callbacks
    assert labels[0] == ["Send All", "LANGUAGES", "YEARS"]
    assert labels[1] == ["QUALITY", "EPISODES", "SEASONS"]
    assert labels[2] == ["PAGE", "1/9", "NEXT ➪"]
    assert {"filter:languages", "filter:year", "filter:quality", "filter:episode", "filter:season"} <= set(callbacks)
    assert "page:picker" in callbacks
    assert "page:1" in callbacks
    assert not any(value and value.startswith("get:") for value in callbacks)


def test_results_keyboard_has_previous_and_back_navigation_on_later_pages() -> None:
    keyboard = results_keyboard([{"file_key": "abc"}], page=1, total=90, filter_values={})
    rows = keyboard.inline_keyboard
    assert [button.text for button in rows[2]] == ["PREVIOUS ←", "2/9", "NEXT ➪"]
    assert rows[2][0].callback_data == "page:0"
    assert rows[-1][0].callback_data == "search:back"


def test_empty_filter_keyboard_keeps_a_back_button() -> None:
    keyboard = filter_options_keyboard("episode", [])
    assert len(keyboard.inline_keyboard) == 1
    assert keyboard.inline_keyboard[0][0].callback_data == "search:results"
    assert "Back" in keyboard.inline_keyboard[0][0].text


def test_admin_home_exposes_requested_sections() -> None:
    callbacks = [
        button.callback_data
        for row in admin_home({"bot_enabled": True}).inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert {"adm:dashboard", "adm:sources", "adm:live", "adm:subscription",
            "adm:requests:0", "adm:broadcast", "adm:timers", "adm:settings",
            "adm:groups", "adm:toggle_group_search"} <= set(callbacks)
    assert "adm:duplicates" not in callbacks
    assert "adm:cleanup" not in callbacks
    group_button = next(
        button
        for row in admin_home({"group_search_enabled": False}).inline_keyboard
        for button in row
        if button.callback_data == "adm:toggle_group_search"
    )
    assert group_button.text == "Group replies: OFF"


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


def test_force_sub_menu_exposes_add_remove_and_total_count_actions() -> None:
    keyboard = subscription_list(enabled=False)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    callbacks = {button.callback_data for button in buttons}
    assert "adm:add_sub" in callbacks
    assert "adm:remove_sub" in callbacks
    assert "adm:home" in callbacks
    assert any(button.text == "Force-sub: OFF" for button in buttons)


def test_force_sub_count_reports_number_of_links() -> None:
    assert _subscription_count_text([]) == "Total links: 0"
    assert _subscription_count_text([{"chat_id": "-1001"}, {"chat_id": "-1002"}]) == "Total links: 2"


def test_refresh_unchanged_message_is_treated_as_noop() -> None:
    assert _is_message_not_modified_error(BadRequest("Message is not modified"))
    assert not _is_message_not_modified_error(BadRequest("Message to edit not found"))


def test_force_sub_remove_menu_lists_each_link_and_back() -> None:
    keyboard = subscription_remove_list(
        [{"chat_id": "-100123", "title": "Movies"}, {"chat_id": "-100456", "title": "Series"}]
    )
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]
    assert "adm:del_sub:-100123" in callbacks
    assert "adm:del_sub:-100456" in callbacks
    assert "adm:subscription" in callbacks


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


def test_group_search_runs_only_when_enabled_and_bot_is_admin(monkeypatch) -> None:
    async def run_case(group_enabled: bool, bot_status: str) -> bool:
        user = SimpleNamespace(id=7, is_bot=False)
        message = SimpleNamespace(
            text="KGF",
            chat=SimpleNamespace(
                id=-100123,
                title="Movies",
                username=None,
                type="supergroup",
            ),
        )
        update = SimpleNamespace(effective_message=message, effective_user=user)
        context = SimpleNamespace(user_data={}, bot=SimpleNamespace(id=99))
        db = SimpleNamespace(
            get_settings=AsyncMock(
                return_value={
                    "group_search_enabled": group_enabled,
                    "bot_enabled": True,
                }
            ),
            get_bot_group=AsyncMock(return_value={"bot_status": bot_status}),
        )
        run_search = AsyncMock()
        monkeypatch.setattr("bot.handlers._services", lambda _: (db, None, {7}))
        monkeypatch.setattr("bot.handlers._run_search", run_search)

        await group_post(update, context)
        return run_search.await_count == 1

    assert asyncio.run(run_case(True, "administrator")) is True
    assert asyncio.run(run_case(False, "administrator")) is False
    assert asyncio.run(run_case(True, "member")) is False
