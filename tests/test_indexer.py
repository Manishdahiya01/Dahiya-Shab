import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telethon.tl.types import InputMessagesFilterDocument

from bot.indexer import HistoricalIndexer


def test_historical_scan_persists_total_processed_skipped_and_indexed_counts() -> None:
    async def run_scan() -> tuple[AsyncMock, AsyncMock]:
        messages = [
            SimpleNamespace(
                id=1,
                media=object(),
                file=SimpleNamespace(
                    name="Movie.mkv",
                    mime_type="video/x-matroska",
                    size=1024,
                ),
                message="Movie 2024 Hindi 1080p",
            ),
            SimpleNamespace(
                id=2,
                media=object(),
                file=SimpleNamespace(name=None, mime_type=None, size=None),
                message="Unsupported attachment",
            ),
        ]

        class FakeClient:
            def is_connected(self) -> bool:
                return True

            async def get_entity(self, chat_id: int) -> str:
                assert chat_id == -100123
                return "entity"

            async def get_messages(self, entity: str, *, limit: int, filter: object) -> object:
                assert entity == "entity"
                assert limit == 0
                assert isinstance(filter, InputMessagesFilterDocument)
                return SimpleNamespace(total=2)

            async def iter_messages(self, entity: str, *, reverse: bool, filter: object):
                assert entity == "entity"
                assert reverse is True
                assert isinstance(filter, InputMessagesFilterDocument)
                for message in messages:
                    yield message

        db = SimpleNamespace(
            sources=SimpleNamespace(find_one=AsyncMock(return_value={"title": "Movies"})),
            set_index_job=AsyncMock(),
            save_movie=AsyncMock(),
        )
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=77)),
            edit_message_text=AsyncMock(),
        )
        config = SimpleNamespace(historical_indexing_enabled=True)
        indexer = HistoricalIndexer(config, db, bot)
        indexer.client = FakeClient()

        await indexer.scan_chat(-100123, 7)
        return db.set_index_job, bot.edit_message_text

    set_job, edit_progress = asyncio.run(run_scan())
    final_job = set_job.await_args_list[-1].args[1]
    assert final_job["status"] == "completed"
    assert final_job["files_total"] == 2
    assert final_job["files_processed"] == 2
    assert final_job["indexed"] == 1
    assert final_job["skipped"] == 1
    assert final_job["errors"] == 0
    assert "Files processed: 2/2" in edit_progress.await_args.kwargs["text"]
    assert "Indexed/updated: 1" in edit_progress.await_args.kwargs["text"]
