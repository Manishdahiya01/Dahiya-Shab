import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import BadRequest, TelegramError

from bot.admins import AdminRegistry
from bot.handlers import (
    _deliver_movie,
    _group_added_by_label,
    _handle_admin_callback,
    _handle_admin_text,
    _index_job_status_line,
    _is_message_not_modified_error,
    _result_line,
    _result_header,
    _search_state,
    _schedule_result_auto_delete,
    _send_results,
    _subscription_count_text,
    _check_subscriptions,
    _welcome_keyboard,
    bot_membership_update,
    channel_post,
    group_post,
    start_command,
    _run_search,
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
    temporary_admin_remove_confirmation,
    temporary_admins_keyboard,
)


def test_result_file_name_links_to_bot_file_delivery_not_source_channel() -> None:
    line = _result_line(
        {
            "source_chat_id": "-1001234567890",
            "source_message_id": 42,
            "file_key": "0123456789abcdef",
            "filename": "Movie & Series 2024.mkv",
            "size": 1_500_000_000,
            "quality": "1080P",
        },
        "DahiyaShabBot",
    )
    assert '<a href="https://t.me/DahiyaShabBot?start=file_0123456789abcdef">' in line
    assert "https://t.me/c/" not in line
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


def test_admin_home_exposes_sections_without_removed_controls() -> None:
    callbacks = [
        button.callback_data
        for row in admin_home({"bot_enabled": True}).inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert {"adm:dashboard", "adm:sources", "adm:live", "adm:subscription",
            "adm:requests:0", "adm:broadcast", "adm:timers", "adm:settings",
            "adm:cleanup",
            "adm:groups"} <= set(callbacks)
    assert "adm:duplicates" not in callbacks
    assert "adm:toggle_group_search" not in callbacks
    assert "adm:admins" not in callbacks


def test_photo_welcome_hides_admin_panel_but_keeps_url_buttons() -> None:
    async def run_case():
        message = SimpleNamespace(
            reply_photo=AsyncMock(),
            reply_text=AsyncMock(),
        )
        settings = {
            "bot_enabled": True,
            "welcome_photo_file_id": "welcome-photo",
            "welcome_message": "Search files",
            "welcome_button": {"label": "Contact Admin", "url": "https://t.me/admin"},
            "custom_reply_buttons": [
                {"label": "Get Updates", "url": "https://t.me/updates"}
            ],
        }
        db = SimpleNamespace(
            register_member=AsyncMock(),
            get_settings=AsyncMock(return_value=settings),
        )
        context = SimpleNamespace(
            args=[],
            user_data={},
            application=SimpleNamespace(
                bot_data={
                    "services": {
                        "db": db,
                        "indexer": SimpleNamespace(running_chats=set()),
                        "admins": {7},
                    }
                }
            )
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_message=message,
        )

        await start_command(update, context)
        return message

    message = asyncio.run(run_case())
    message.reply_photo.assert_awaited_once()
    markup = message.reply_photo.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert {button.text for button in buttons} == {"Contact Admin", "Get Updates"}
    assert not any(button.callback_data == "adm:home" for button in buttons)


def test_temporary_admin_controls_offer_expiry_and_confirm_removal() -> None:
    keyboard = temporary_admins_keyboard(
        [{"user_id": 123456, "expires_at": None}],
        page=0,
        total=1,
    )
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    }
    assert "adm:tempadmin:add:3600" in callbacks
    assert "adm:tempadmin:add:86400" in callbacks
    assert "adm:tempadmin:add:604800" in callbacks
    assert "adm:tempadmin:confirm_remove:123456:0" in callbacks
    confirm_callbacks = [
        button.callback_data
        for row in temporary_admin_remove_confirmation(123456, 0).inline_keyboard
        for button in row
    ]
    assert "adm:tempadmin:remove:123456:0" in confirm_callbacks


def test_temporary_admin_registry_keeps_owner_and_expires_grants() -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    registry = AdminRegistry(
        {7},
        [
            {"user_id": 8, "expires_at": now + timedelta(hours=1)},
            {"user_id": 9, "expires_at": now - timedelta(seconds=1)},
        ],
    )
    assert 7 in registry
    assert registry.is_owner(7)
    assert 8 in registry
    assert not registry.is_owner(8)
    assert 9 not in registry
    registry.remove_temporary_admin(8)
    assert 8 not in registry


def test_temporary_admin_cannot_manage_other_admins() -> None:
    from datetime import datetime, timedelta, timezone

    async def run_case() -> AsyncMock:
        registry = AdminRegistry(
            {7},
            [{"user_id": 8, "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)}],
        )
        answer = AsyncMock()
        db = SimpleNamespace(register_member=AsyncMock())
        update = SimpleNamespace(
            callback_query=SimpleNamespace(answer=answer),
            effective_user=SimpleNamespace(id=8),
            effective_chat=SimpleNamespace(type="private"),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={
                    "services": {
                        "db": db,
                        "indexer": None,
                        "admins": registry,
                    }
                }
            )
        )
        await _handle_admin_callback(update, context, "adm:admins")
        db.register_member.assert_not_awaited()
        return answer

    answer = asyncio.run(run_case())
    answer.assert_awaited_once_with(
        "Only a configured owner can manage admins in the private chat.",
        show_alert=True,
    )


def test_configured_owner_can_grant_a_temporary_admin() -> None:
    from datetime import datetime, timedelta, timezone

    async def run_case() -> tuple[SimpleNamespace, SimpleNamespace, AdminRegistry]:
        expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        db = SimpleNamespace(
            grant_temporary_admin=AsyncMock(),
            count_temporary_admins=AsyncMock(return_value=1),
            list_temporary_admins=AsyncMock(
                return_value=[{"user_id": 8, "expires_at": expiry}]
            ),
        )
        registry = AdminRegistry({7})
        reply = AsyncMock()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_message=SimpleNamespace(text="8", reply_text=reply),
        )
        context = SimpleNamespace(
            user_data={
                "admin_state": "temporary_admin_id",
                "temporary_admin_duration_seconds": 3600,
            },
            bot=SimpleNamespace(id=99),
        )
        handled = await _handle_admin_text(update, context, db, registry)
        assert handled
        assert 8 in registry
        return db, context, registry

    db, context, _ = asyncio.run(run_case())
    db.grant_temporary_admin.assert_awaited_once()
    assert db.grant_temporary_admin.await_args.args[:2] == (8, 7)
    assert abs(
        (db.grant_temporary_admin.await_args.args[2] - datetime.now(timezone.utc)).total_seconds()
        - 3600
    ) < 5
    assert "admin_state" not in context.user_data


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


def test_index_status_refresh_adds_resume_button_even_when_text_is_unchanged() -> None:
    async def run_case():
        status_text = (
            "📈 INDEXING PROGRESS\n"
            "• Movies: RUNNING — 2/5 files processed; 2 indexed, 0 skipped, 0 errors"
        )
        query = SimpleNamespace(
            message=SimpleNamespace(text=status_text, chat_id=7),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        db = SimpleNamespace(
            register_member=AsyncMock(),
            get_settings=AsyncMock(return_value={}),
            get_sources=AsyncMock(return_value=[{"chat_id": "-100123", "title": "Movies"}]),
            get_latest_index_job=AsyncMock(
                return_value={
                    "status": "running",
                    "files_total": 5,
                    "files_processed": 2,
                    "indexed": 2,
                    "skipped": 0,
                    "errors": 0,
                }
            ),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={
                    "services": {
                        "db": db,
                        "indexer": SimpleNamespace(running_chats=set()),
                        "admins": {7},
                    }
                }
            )
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
        )
        await _handle_admin_callback(update, context, "adm:index_status")
        return query

    query = asyncio.run(run_case())
    query.edit_message_text.assert_awaited_once()
    keyboard = query.edit_message_text.await_args.kwargs["reply_markup"]
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    }
    assert "adm:index_status" in callbacks
    assert "adm:index_resume:-100123" in callbacks


def test_running_index_status_offers_pause_and_acknowledges_request() -> None:
    async def run_case():
        query = SimpleNamespace(
            message=SimpleNamespace(text=None, chat_id=7),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        paused_sources = []
        indexer = SimpleNamespace(
            running_chats={"-100123"},
            request_pause=lambda chat_id: paused_sources.append(chat_id) is None,
        )
        db = SimpleNamespace(
            register_member=AsyncMock(),
            get_settings=AsyncMock(return_value={}),
            get_sources=AsyncMock(return_value=[{"chat_id": "-100123", "title": "Movies"}]),
            get_latest_index_job=AsyncMock(
                return_value={
                    "status": "running",
                    "files_total": 5,
                    "files_processed": 2,
                    "indexed": 2,
                    "skipped": 0,
                    "errors": 0,
                }
            ),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={
                    "services": {
                        "db": db,
                        "indexer": indexer,
                        "admins": {7},
                    }
                }
            )
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
        )
        await _handle_admin_callback(update, context, "adm:index_status")
        keyboard = query.edit_message_text.await_args.kwargs["reply_markup"]
        callbacks = {
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        }
        assert "adm:index_pause:-100123" in callbacks
        assert "adm:index_resume:-100123" not in callbacks

        await _handle_admin_callback(update, context, "adm:index_pause:-100123")
        assert paused_sources == ["-100123"]
        assert "current file will finish" in query.edit_message_text.await_args.args[0]
        pause_keyboard = query.edit_message_text.await_args.kwargs["reply_markup"]
        assert pause_keyboard.inline_keyboard[0][0].callback_data == "adm:index_status"

    asyncio.run(run_case())


def test_welcome_message_and_button_menus_have_photo_controls() -> None:
    async def run_case():
        query = SimpleNamespace(
            message=SimpleNamespace(text=None, chat_id=7),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        settings = {
            "welcome_message": "Search the indexed movies.",
            "welcome_button": {"label": "Join", "url": "https://t.me/example"},
        }
        db = SimpleNamespace(
            register_member=AsyncMock(),
            get_settings=AsyncMock(return_value=settings),
            update_settings=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={
                    "services": {
                        "db": db,
                        "indexer": SimpleNamespace(running_chats=set()),
                        "admins": {7},
                    }
                }
            )
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
        )

        await _handle_admin_callback(update, context, "adm:welcome")
        welcome_markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        welcome_callbacks = {
            button.callback_data
            for row in welcome_markup.inline_keyboard
            for button in row
        }
        assert {
            "adm:edit_welcome",
            "adm:reset_welcome",
            "adm:clear_welcome",
            "adm:set_welcome_button",
            "adm:clear_welcome_button",
        } <= welcome_callbacks

        await _handle_admin_callback(update, context, "adm:welcome_button")
        button_markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        button_callbacks = {
            button.callback_data
            for row in button_markup.inline_keyboard
            for button in row
        }
        assert {"adm:set_welcome_button", "adm:clear_welcome_button"} <= button_callbacks

        await _handle_admin_callback(update, context, "adm:reset_welcome")
        db.update_settings.assert_awaited_with(
            {"welcome_message": "Send a movie or series name to search the indexed files."}
        )
        await _handle_admin_callback(update, context, "adm:clear_welcome")
        db.update_settings.assert_awaited_with({"welcome_message": ""})

    asyncio.run(run_case())


def test_cleanup_confirmation_deletes_only_indexed_records_and_checkpoints() -> None:
    async def run_case():
        query = SimpleNamespace(
            message=SimpleNamespace(text=None, chat_id=7),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        db = SimpleNamespace(
            register_member=AsyncMock(),
            get_settings=AsyncMock(return_value={}),
            index_data_counts=AsyncMock(return_value={"files": 6000, "checkpoints": 1}),
            clear_index_data=AsyncMock(return_value={"files": 6000, "checkpoints": 1}),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={
                    "services": {
                        "db": db,
                        "indexer": SimpleNamespace(running_chats=set()),
                        "admins": {7},
                    }
                }
            )
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
        )

        await _handle_admin_callback(update, context, "adm:cleanup")
        prompt_markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        prompt_callbacks = {
            button.callback_data
            for row in prompt_markup.inline_keyboard
            for button in row
        }
        assert "adm:cleanup:confirm" in prompt_callbacks
        assert "adm:home" in prompt_callbacks
        assert "6000" in query.edit_message_text.await_args.args[0]
        db.clear_index_data.assert_not_awaited()

        await _handle_admin_callback(update, context, "adm:cleanup:confirm")
        db.clear_index_data.assert_awaited_once_with()
        assert "deleted 6000 indexed file/video record(s)" in query.edit_message_text.await_args.args[0]

    asyncio.run(run_case())


def test_force_sub_menu_exposes_add_remove_and_total_count_actions() -> None:
    keyboard = subscription_list(enabled=False)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    callbacks = {button.callback_data for button in buttons}
    assert "adm:add_sub" in callbacks
    assert "adm:remove_sub" in callbacks
    assert "adm:home" in callbacks
    assert any(button.text == "Force-sub: OFF" for button in buttons)


def test_subscription_check_names_channel_when_user_is_not_a_member() -> None:
    async def run_check() -> tuple[bool, str | None]:
        update = SimpleNamespace(effective_user=SimpleNamespace(id=7))
        context = SimpleNamespace(
            bot=SimpleNamespace(
                get_chat_member=AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.LEFT))
            )
        )
        db = SimpleNamespace(
            get_settings=AsyncMock(return_value={"subscription": {"enabled": True}}),
            get_subscriptions=AsyncMock(return_value=[{"chat_id": "-100123", "title": "Dahiya Movie Store"}]),
        )
        return await _check_subscriptions(update, context, db)

    assert asyncio.run(run_check()) == (False, "Join Dahiya Movie Store first, then tap Try Again.")


def test_subscription_check_explains_when_telegram_cannot_verify_channel() -> None:
    async def run_check() -> tuple[bool, str | None]:
        update = SimpleNamespace(effective_user=SimpleNamespace(id=7))
        context = SimpleNamespace(
            bot=SimpleNamespace(get_chat_member=AsyncMock(side_effect=TelegramError("Forbidden")))
        )
        db = SimpleNamespace(
            get_settings=AsyncMock(return_value={"subscription": {"enabled": True}}),
            get_subscriptions=AsyncMock(return_value=[{"chat_id": "-100123", "title": "Dahiya movie chat"}]),
        )
        return await _check_subscriptions(update, context, db)

    is_subscribed, reason = asyncio.run(run_check())
    assert not is_subscribed
    assert reason == "Could not verify Dahiya movie chat. Check the bot's channel access and saved chat ID."


def test_subscription_check_accepts_membership_in_all_channels() -> None:
    async def run_check() -> tuple[bool, str | None]:
        update = SimpleNamespace(effective_user=SimpleNamespace(id=7))
        context = SimpleNamespace(
            bot=SimpleNamespace(
                get_chat_member=AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.MEMBER))
            )
        )
        db = SimpleNamespace(
            get_settings=AsyncMock(return_value={"subscription": {"enabled": True}}),
            get_subscriptions=AsyncMock(return_value=[
                {"chat_id": "-100123", "title": "Store"},
                {"chat_id": "-100456", "title": "Movie chat"},
            ]),
        )
        result = await _check_subscriptions(update, context, db)
        assert context.bot.get_chat_member.await_count == 2
        return result

    assert asyncio.run(run_check()) == (True, None)


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


def test_index_progress_line_shows_total_processed_and_error_counts() -> None:
    line = _index_job_status_line(
        "Movies",
        {
            "status": "running",
            "files_total": 1200,
            "files_processed": 300,
            "indexed": 290,
            "skipped": 7,
            "errors": 3,
        },
    )
    assert "300/1200 files processed" in line
    assert "290 indexed, 7 skipped, 3 errors" in line


def test_group_list_shows_who_added_the_bot() -> None:
    assert _group_added_by_label(
        {"added_by_name": "Manish Dahiya", "added_by_username": "manish"}
    ) == "Manish Dahiya (@manish)"
    assert _group_added_by_label({}) == "Not recorded"


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
        context = SimpleNamespace(user_data={}, bot=SimpleNamespace(id=99, get_chat_member=AsyncMock(return_value=SimpleNamespace(status=bot_status))))
        db = SimpleNamespace(
            get_settings=AsyncMock(
                return_value={
                    "group_search_enabled": group_enabled,
                    "bot_enabled": True,
                }
            ),
            get_bot_group=AsyncMock(return_value={"bot_status": bot_status}),
            save_bot_group=AsyncMock(),
        )
        run_search = AsyncMock()
        monkeypatch.setattr("bot.handlers._services", lambda _: (db, None, {7}))
        monkeypatch.setattr("bot.handlers._run_search", run_search)

        await group_post(update, context)
        return run_search.await_count == 1

    assert asyncio.run(run_case(True, "administrator")) is True
    assert asyncio.run(run_case(False, "administrator")) is False
    assert asyncio.run(run_case(True, "member")) is False


def test_group_membership_records_who_added_the_bot(monkeypatch) -> None:
    async def run_case() -> AsyncMock:
        db = SimpleNamespace(
            get_bot_group=AsyncMock(return_value=None),
            save_bot_group=AsyncMock(),
        )
        actor = SimpleNamespace(id=99, first_name="A", last_name="B", username="admin")
        change = SimpleNamespace(
            new_chat_member=SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR),
            chat=SimpleNamespace(id=-100123, title="Movies", username=None, type="supergroup"),
            from_user=actor,
        )
        update = SimpleNamespace(my_chat_member=change)
        context = SimpleNamespace()
        monkeypatch.setattr("bot.handlers._services", lambda _: (db, None, set()))

        await bot_membership_update(update, context)
        return db.save_bot_group

    save_group = asyncio.run(run_case())
    save_group.assert_awaited_once_with(
        -100123,
        "Movies",
        "supergroup",
        "administrator",
        99,
        "A B",
        "admin",
    )


def test_file_deep_link_starts_direct_delivery(monkeypatch) -> None:
    async def run_case() -> tuple[AsyncMock, object, object, object]:
        item = {"file_key": "0123456789abcdef", "source_chat_id": "-1001", "source_message_id": 55}
        db = SimpleNamespace(
            register_member=AsyncMock(),
            get_settings=AsyncMock(return_value={"bot_enabled": True}),
            get_movie=AsyncMock(return_value=item),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7, first_name="User", last_name=None, username=None),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
        )
        context = SimpleNamespace(
            args=["file_0123456789abcdef"],
            user_data={},
            bot_data={},
        )
        deliver = AsyncMock()
        monkeypatch.setattr("bot.handlers._services", lambda _: (db, None, {7}))
        monkeypatch.setattr("bot.handlers._deliver_movie", deliver)

        await start_command(update, context)
        return deliver, update, context, db

    deliver, update, context, db = asyncio.run(run_case())
    deliver.assert_awaited_once_with(
        update,
        context,
        db,
        {"file_key": "0123456789abcdef", "source_chat_id": "-1001", "source_message_id": 55},
    )


def test_file_delivery_copies_source_without_forward_header() -> None:
    async def run_case() -> AsyncMock:
        copy = AsyncMock(return_value=SimpleNamespace(message_id=123))
        db = SimpleNamespace(get_settings=AsyncMock(return_value={"auto_delete": {"enabled": False}}))
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=7),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
        )
        context = SimpleNamespace(bot=SimpleNamespace(copy_message=copy))
        await _deliver_movie(
            update,
            context,
            db,
            {"source_chat_id": "-100123", "source_message_id": 42},
        )
        return copy

    copy = asyncio.run(run_case())
    copy.assert_awaited_once_with(chat_id=7, from_chat_id=-100123, message_id=42)


def test_auto_delete_schedules_original_search_request_with_result() -> None:
    async def run_case() -> AsyncMock:
        result_message = SimpleNamespace(chat_id=7, message_id=200)
        request_message = SimpleNamespace(message_id=100)
        make_delivery = AsyncMock()
        db = SimpleNamespace(
            get_delivery=AsyncMock(return_value=None),
            make_delivery=make_delivery,
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(
                send_message=AsyncMock(return_value=SimpleNamespace(message_id=201))
            )
        )

        await _schedule_result_auto_delete(
            context,
            db,
            result_message,
            {"auto_delete": {"enabled": True, "seconds": 30}},
            request_message=request_message,
        )
        return make_delivery

    make_delivery = asyncio.run(run_case())
    make_delivery.assert_awaited_once()
    assert make_delivery.await_args.args[:3] == (7, 200, 201)
    assert make_delivery.await_args.kwargs["request_message_id"] == 100



def test_channel_search_requires_enabled_toggle_and_bot_admin(monkeypatch) -> None:
    async def run_case(enabled: bool, bot_status: str) -> AsyncMock:
        chat = SimpleNamespace(id=-100123, title="Movies", username="movies", type="channel")
        message = SimpleNamespace(
            text="/search KGF",
            chat=chat,
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            channel_post=message,
            effective_message=message,
            effective_chat=chat,
            effective_user=None,
        )
        context = SimpleNamespace(
            chat_data={},
            user_data={},
            bot=SimpleNamespace(id=99, get_chat_member=AsyncMock(return_value=SimpleNamespace(status=bot_status))),
        )
        db = SimpleNamespace(
            get_settings=AsyncMock(
                return_value={"group_search_enabled": enabled, "bot_enabled": True}
            ),
            get_bot_group=AsyncMock(return_value={"bot_status": bot_status}),
            save_bot_group=AsyncMock(),
        )
        run_search = AsyncMock()
        monkeypatch.setattr("bot.handlers._services", lambda _: (db, None, set()))
        monkeypatch.setattr("bot.handlers._run_search", run_search)

        await channel_post(update, context)
        return run_search

    run = asyncio.run(run_case(True, ChatMemberStatus.ADMINISTRATOR))
    run.assert_awaited_once()
    assert run.await_args.args[3] == "KGF"
    assert asyncio.run(run_case(False, ChatMemberStatus.ADMINISTRATOR)).await_count == 0
    assert asyncio.run(run_case(True, ChatMemberStatus.MEMBER)).await_count == 0


def test_channel_search_uses_chat_state_without_a_user(monkeypatch) -> None:
    async def run_case() -> tuple[dict, dict, AsyncMock, AsyncMock]:
        chat = SimpleNamespace(id=-100123, title="Movies", type="channel")
        update = SimpleNamespace(effective_user=None, effective_chat=chat, effective_message=SimpleNamespace())
        chat_data: dict = {}
        user_data: dict = {}
        context = SimpleNamespace(chat_data=chat_data, user_data=user_data)
        db = SimpleNamespace(
            register_member=AsyncMock(),
            record_request=AsyncMock(),
            get_settings=AsyncMock(return_value={"fuzzy_search_enabled": True}),
            search_movies=AsyncMock(return_value=[]),
        )
        send_results = AsyncMock()
        monkeypatch.setattr("bot.handlers._send_results", send_results)

        await _run_search(update, context, db, "KGF")
        return chat_data, user_data, db.record_request, send_results

    chat_data, user_data, record_request, send_results = asyncio.run(run_case())
    search_data = send_results.await_args.kwargs["search_data"]
    assert search_data["last_search"] == "KGF"
    assert search_data["search_requested_by"] == "Movies"
    assert send_results.await_args.kwargs["request_message"] is None
    assert user_data == {}
    record_request.assert_not_awaited()
    send_results.assert_awaited_once()


def test_group_search_command_routes_query(monkeypatch) -> None:
    async def run_case() -> AsyncMock:
        user = SimpleNamespace(id=7, is_bot=False)
        message = SimpleNamespace(
            text="/search KGF",
            chat=SimpleNamespace(id=-100123, title="Movies", username=None, type="supergroup"),
        )
        update = SimpleNamespace(effective_message=message, effective_user=user)
        context = SimpleNamespace(user_data={}, bot=SimpleNamespace(id=99))
        db = SimpleNamespace(
            get_settings=AsyncMock(return_value={"group_search_enabled": True, "bot_enabled": True}),
            get_bot_group=AsyncMock(return_value={"bot_status": ChatMemberStatus.ADMINISTRATOR}),
        )
        run_search = AsyncMock()
        monkeypatch.setattr("bot.handlers._services", lambda _: (db, None, {7}))
        monkeypatch.setattr("bot.handlers._run_search", run_search)

        await group_post(update, context)
        return run_search

    run = asyncio.run(run_case())
    run.assert_awaited_once()
    assert run.await_args.args[3] == "KGF"



def test_group_search_refreshes_stale_membership_after_promotion(monkeypatch) -> None:
    async def run_case() -> tuple[AsyncMock, AsyncMock]:
        user = SimpleNamespace(id=7, is_bot=False)
        chat = SimpleNamespace(id=-100123, title="Movies", username=None, type="supergroup")
        message = SimpleNamespace(text="KGF", chat=chat)
        update = SimpleNamespace(effective_message=message, effective_user=user)
        get_member = AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR))
        context = SimpleNamespace(user_data={}, bot=SimpleNamespace(id=99, get_chat_member=get_member))
        db = SimpleNamespace(
            get_settings=AsyncMock(return_value={"group_search_enabled": True, "bot_enabled": True}),
            get_bot_group=AsyncMock(return_value={"bot_status": ChatMemberStatus.MEMBER}),
            save_bot_group=AsyncMock(),
        )
        run_search = AsyncMock()
        monkeypatch.setattr("bot.handlers._services", lambda _: (db, None, {7}))
        monkeypatch.setattr("bot.handlers._run_search", run_search)

        await group_post(update, context)
        return get_member, run_search

    get_member, run_search = asyncio.run(run_case())
    get_member.assert_awaited_once_with(-100123, 99)
    run_search.assert_awaited_once()
    assert run_search.await_args.args[3] == "KGF"


def test_group_anonymous_admin_message_reaches_search(monkeypatch) -> None:
    async def run_case() -> tuple[AsyncMock, AsyncMock, AsyncMock, object]:
        chat = SimpleNamespace(id=-100123, title="Movies", type="supergroup")
        anonymous_bot = SimpleNamespace(id=1087968824, is_bot=True)
        message = SimpleNamespace(text="KGF", chat=chat, sender_chat=chat)
        update = SimpleNamespace(effective_message=message, effective_chat=chat, effective_user=anonymous_bot)
        context = SimpleNamespace(user_data={}, chat_data={}, bot=SimpleNamespace(id=99))
        db = SimpleNamespace(
            get_settings=AsyncMock(return_value={"group_search_enabled": True, "bot_enabled": True}),
            get_bot_group=AsyncMock(return_value={"bot_status": ChatMemberStatus.ADMINISTRATOR}),
        )
        run_search = AsyncMock()
        subscribed = AsyncMock(return_value=False)
        join_gate = AsyncMock()
        monkeypatch.setattr("bot.handlers._services", lambda _: (db, None, set()))
        monkeypatch.setattr("bot.handlers._run_search", run_search)
        monkeypatch.setattr("bot.handlers._is_subscribed", subscribed)
        monkeypatch.setattr("bot.handlers._show_join_gate", join_gate)

        await group_post(update, context)
        return run_search, subscribed, join_gate, update

    run_search, subscribed, join_gate, update = asyncio.run(run_case())
    run_search.assert_awaited_once()
    assert run_search.await_args.args[0] is update
    assert run_search.await_args.args[3] == "KGF"
    subscribed.assert_not_awaited()
    join_gate.assert_not_awaited()


def test_group_search_state_is_selected_by_result_message() -> None:
    first = {"last_search": "first"}
    second = {"last_search": "second"}
    context = SimpleNamespace(
        chat_data={"_search_results": {"41": first, "42": second}},
        user_data={},
    )
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(type="supergroup"),
        callback_query=SimpleNamespace(message=SimpleNamespace(message_id=42)),
    )

    assert _search_state(update, context) is second



def test_group_results_store_state_for_inline_buttons(monkeypatch) -> None:
    async def run_case() -> tuple[dict, dict]:
        chat = SimpleNamespace(id=-100123, type="supergroup")
        message = SimpleNamespace(
            reply_text=AsyncMock(return_value=SimpleNamespace(message_id=42, chat_id=chat.id))
        )
        update = SimpleNamespace(effective_chat=chat, effective_message=message, callback_query=None)
        state = {
            "last_search": "KGF",
            "search_hits": [{
                "file_key": "0123456789abcdef",
                "source_chat_id": "-100123",
                "source_message_id": 55,
                "filename": "KGF.mkv",
            }],
            "search_filters": {},
            "search_requested_by": "Movies",
            "search_elapsed_seconds": 0.01,
        }
        context = SimpleNamespace(
            user_data={},
            chat_data={},
            bot_data={"bot_username": "DahiyaShabBot"},
        )
        db = SimpleNamespace(
            get_settings=AsyncMock(return_value={"powered_by": "Dahiya Movie Store", "auto_delete": {"enabled": False}})
        )
        monkeypatch.setattr("bot.handlers._schedule_result_auto_delete", AsyncMock())

        await _send_results(update, context, db, search_data=state)
        return context.chat_data, state

    chat_data, state = asyncio.run(run_case())
    assert chat_data["_search_results"]["42"] is state



def test_anonymous_group_search_does_not_register_pseudobot(monkeypatch) -> None:
    async def run_case() -> tuple[AsyncMock, AsyncMock, AsyncMock, dict]:
        chat = SimpleNamespace(id=-100123, title="Movies", type="supergroup")
        anonymous_bot = SimpleNamespace(id=1087968824, is_bot=True, first_name="Group")
        message = SimpleNamespace(sender_chat=chat)
        update = SimpleNamespace(effective_user=anonymous_bot, effective_message=message, effective_chat=chat)
        context = SimpleNamespace(user_data={}, chat_data={})
        db = SimpleNamespace(
            register_member=AsyncMock(),
            record_request=AsyncMock(),
            get_settings=AsyncMock(return_value={"fuzzy_search_enabled": True}),
            search_movies=AsyncMock(return_value=[]),
        )
        send_results = AsyncMock()
        monkeypatch.setattr("bot.handlers._send_results", send_results)

        await _run_search(update, context, db, "KGF")
        return db.register_member, db.record_request, send_results, context.user_data

    register_member, record_request, send_results, user_data = asyncio.run(run_case())
    register_member.assert_not_awaited()
    record_request.assert_not_awaited()
    assert send_results.await_args.kwargs["search_data"]["search_requested_by"] == "Movies"
    assert user_data == {}


def test_private_search_passes_user_request_message_for_auto_delete(monkeypatch) -> None:
    async def run_case() -> tuple[AsyncMock, object]:
        user = SimpleNamespace(id=7, first_name="User", last_name=None, username=None)
        message = SimpleNamespace(message_id=123, text="Movie")
        chat = SimpleNamespace(type=ChatType.PRIVATE)
        update = SimpleNamespace(
            effective_user=user,
            effective_message=message,
            effective_chat=chat,
        )
        context = SimpleNamespace(user_data={})
        db = SimpleNamespace(
            register_member=AsyncMock(),
            record_request=AsyncMock(),
            get_settings=AsyncMock(return_value={"fuzzy_search_enabled": True}),
            search_movies=AsyncMock(return_value=[]),
        )
        send_results = AsyncMock()
        monkeypatch.setattr("bot.handlers._send_results", send_results)

        await _run_search(update, context, db, "Movie")
        return send_results, message

    send_results, message = asyncio.run(run_case())
    assert send_results.await_args.kwargs["request_message"] is message
