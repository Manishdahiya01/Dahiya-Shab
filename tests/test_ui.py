import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest

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
    _send_results,
    _subscription_count_text,
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
    assert group_button.text == "Group search: OFF"


def test_only_configured_owners_see_temporary_admin_management() -> None:
    owner_callbacks = {
        button.callback_data
        for row in admin_home({}, can_manage_admins=True).inline_keyboard
        for button in row
    }
    regular_admin_callbacks = {
        button.callback_data
        for row in admin_home({}, can_manage_admins=False).inline_keyboard
        for button in row
    }
    assert "adm:admins" in owner_callbacks
    assert "adm:admins" not in regular_admin_callbacks


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
