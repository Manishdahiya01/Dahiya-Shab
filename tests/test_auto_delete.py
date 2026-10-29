import asyncio
from unittest.mock import AsyncMock, call

from telegram.error import BadRequest

from bot.main import deletion_worker


def test_deletion_worker_deletes_request_along_with_result_and_countdown() -> None:
    async def run_case():
        shutdown = asyncio.Event()

        async def due_deliveries(_now):
            shutdown.set()
            return [
                {
                    "chat_id": 7,
                    "message_id": 200,
                    "request_message_id": 100,
                    "status_message_id": 201,
                }
            ]

        db = type(
            "DatabaseStub",
            (),
            {
                "due_deliveries": AsyncMock(side_effect=due_deliveries),
                "pending_deliveries": AsyncMock(return_value=[]),
                "remove_delivery": AsyncMock(),
                "update_delivery": AsyncMock(),
            },
        )()
        bot = type("BotStub", (), {"delete_message": AsyncMock()})()

        await deletion_worker(db, bot, shutdown)
        return db, bot

    db, bot = asyncio.run(run_case())
    assert bot.delete_message.await_args_list == [
        call(chat_id=7, message_id=200),
        call(chat_id=7, message_id=100),
        call(chat_id=7, message_id=201),
    ]
    db.remove_delivery.assert_awaited_once_with(7, 200)


def test_request_delete_failure_does_not_block_result_cleanup() -> None:
    async def run_case():
        shutdown = asyncio.Event()

        async def due_deliveries(_now):
            shutdown.set()
            return [
                {
                    "chat_id": 7,
                    "message_id": 200,
                    "request_message_id": 100,
                    "status_message_id": 201,
                }
            ]

        db = type(
            "DatabaseStub",
            (),
            {
                "due_deliveries": AsyncMock(side_effect=due_deliveries),
                "pending_deliveries": AsyncMock(return_value=[]),
                "remove_delivery": AsyncMock(),
                "update_delivery": AsyncMock(),
            },
        )()

        async def delete_message(*, chat_id, message_id):
            if message_id == 100:
                raise BadRequest("Not enough rights to delete this message")

        bot = type("BotStub", (), {"delete_message": AsyncMock(side_effect=delete_message)})()
        await deletion_worker(db, bot, shutdown)
        return db, bot

    db, bot = asyncio.run(run_case())
    assert bot.delete_message.await_args_list == [
        call(chat_id=7, message_id=200),
        call(chat_id=7, message_id=100),
        call(chat_id=7, message_id=201),
    ]
    db.remove_delivery.assert_awaited_once_with(7, 200)
