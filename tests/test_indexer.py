import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from typing import Any

from telethon.tl.types import InputMessagesFilterDocument, InputMessagesFilterVideo

from bot.database import utc_now
from bot.indexer import HistoricalIndexer


def test_historical_scan_indexes_documents_and_videos() -> None:
    async def run_scan() -> tuple[AsyncMock, AsyncMock, AsyncMock]:
        documents = [
            SimpleNamespace(
                id=1,
                media=object(),
                file=SimpleNamespace(
                    name="Movie.mkv",
                    mime_type="application/octet-stream",
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
        videos = [
            SimpleNamespace(
                id=3,
                media=object(),
                file=SimpleNamespace(
                    name="Clip.mp4",
                    mime_type="video/mp4",
                    size=2048,
                ),
                message="Clip 2025",
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
                if isinstance(filter, InputMessagesFilterDocument):
                    return SimpleNamespace(total=len(documents))
                assert isinstance(filter, InputMessagesFilterVideo)
                return SimpleNamespace(total=len(videos))

            async def iter_messages(
                self,
                entity: str,
                *,
                reverse: bool,
                filter: object,
                min_id: int = 0,
            ):
                assert entity == "entity"
                assert reverse is True
                if isinstance(filter, InputMessagesFilterDocument):
                    selected = documents
                else:
                    assert isinstance(filter, InputMessagesFilterVideo)
                    selected = videos
                for message in selected:
                    if message.id > min_id:
                        yield message

        db = SimpleNamespace(
            sources=SimpleNamespace(find_one=AsyncMock(return_value={"title": "Movies"})),
            get_latest_index_job=AsyncMock(return_value=None),
            set_index_job=AsyncMock(),
            save_movie=AsyncMock(return_value=True),
        )
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=77)),
            edit_message_text=AsyncMock(),
        )
        config = SimpleNamespace(historical_indexing_enabled=True)
        indexer = HistoricalIndexer(config, db, bot)
        indexer.client = FakeClient()

        await indexer.scan_chat(-100123, 7)
        return db.set_index_job, db.save_movie, bot.edit_message_text

    set_job, save_movie, edit_progress = asyncio.run(run_scan())
    final_job = set_job.await_args_list[-1].args[1]
    assert final_job["status"] == "completed"
    assert final_job["files_total"] == 3
    assert final_job["files_processed"] == 3
    assert final_job["indexed"] == 2
    assert final_job["skipped"] == 1
    assert final_job["errors"] == 0
    assert [call.args[0]["media_type"] for call in save_movie.await_args_list] == [
        "document",
        "video",
    ]
    assert "Files processed: 3/3" in edit_progress.await_args.kwargs["text"]
    assert "Indexed/updated: 2" in edit_progress.await_args.kwargs["text"]
    assert final_job["completed_filters"] == ["documents", "videos"]


def test_resume_migrates_legacy_checkpoint_and_continues_after_last_document() -> None:
    async def run_scan() -> tuple[AsyncMock, AsyncMock, list[tuple[str, int]]]:
        documents = [
            SimpleNamespace(id=1, media=object(), file=SimpleNamespace(name="A.mkv", mime_type="video/mp4", size=1), message="A"),
            SimpleNamespace(id=2, media=object(), file=SimpleNamespace(name="B.mkv", mime_type="video/mp4", size=1), message="B"),
            SimpleNamespace(id=3, media=object(), file=SimpleNamespace(name="C.mkv", mime_type="video/mp4", size=1), message="C"),
        ]
        videos = [
            SimpleNamespace(id=10, media=object(), file=SimpleNamespace(name="D.mp4", mime_type="video/mp4", size=1), message="D"),
            SimpleNamespace(id=11, media=object(), file=SimpleNamespace(name="E.mp4", mime_type="video/mp4", size=1), message="E"),
        ]
        iterated: list[tuple[str, int]] = []

        class FakeClient:
            def is_connected(self) -> bool:
                return True

            async def get_entity(self, chat_id: int) -> str:
                return "entity"

            async def get_messages(self, entity: str, *, limit: int, filter: object) -> object:
                return SimpleNamespace(total=3 if isinstance(filter, InputMessagesFilterDocument) else 2)

            async def iter_messages(
                self,
                entity: str,
                *,
                reverse: bool,
                filter: object,
                min_id: int = 0,
            ):
                name = "documents" if isinstance(filter, InputMessagesFilterDocument) else "videos"
                selected = documents if name == "documents" else videos
                iterated.append((name, min_id))
                for message in selected:
                    if message.id > min_id:
                        yield message

        old_job = {
            "_id": "-100123:old-job",
            "chat_id": "-100123",
            "status": "running",
            "started_at": utc_now(),
            "files_total": 5,
            "files_processed": 2,
            "indexed": 2,
            "skipped": 0,
            "errors": 0,
            "last_message_id": 2,
        }
        db = SimpleNamespace(
            sources=SimpleNamespace(find_one=AsyncMock(return_value={"title": "Movies"})),
            get_latest_index_job=AsyncMock(return_value=old_job),
            set_index_job=AsyncMock(),
            save_movie=AsyncMock(return_value=True),
        )
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=77)),
            edit_message_text=AsyncMock(),
        )
        indexer = HistoricalIndexer(
            SimpleNamespace(historical_indexing_enabled=True),
            db,
            bot,
        )
        indexer.client = FakeClient()

        await indexer.scan_chat(-100123, 7, resume=True)
        final_job = db.set_index_job.await_args_list[-1].args[1]
        return db.set_index_job, final_job, iterated

    set_job, final_job, iterated = asyncio.run(run_scan())
    assert iterated == [("documents", 2), ("videos", 0)]
    assert set_job.await_args_list[0].args[0] == "-100123:old-job"
    assert final_job["status"] == "completed"
    assert final_job["files_processed"] == 5
    assert final_job["indexed"] == 5
    assert final_job["filter_cursors"] == {"documents": 3, "videos": 11}


def test_resume_legacy_video_phase_does_not_restart_documents() -> None:
    async def run_scan() -> tuple[AsyncMock, list[tuple[str, int]]]:
        iterated: list[tuple[str, int]] = []

        class FakeClient:
            def is_connected(self) -> bool:
                return True

            async def get_entity(self, chat_id: int) -> str:
                return "entity"

            async def get_messages(self, entity: str, *, limit: int, filter: object) -> object:
                return SimpleNamespace(total=2 if isinstance(filter, InputMessagesFilterDocument) else 2)

            async def iter_messages(
                self,
                entity: str,
                *,
                reverse: bool,
                filter: object,
                min_id: int = 0,
            ):
                name = "documents" if isinstance(filter, InputMessagesFilterDocument) else "videos"
                iterated.append((name, min_id))
                if name == "videos" and min_id < 21:
                    yield SimpleNamespace(
                        id=21,
                        media=object(),
                        file=SimpleNamespace(name="Next.mp4", mime_type="video/mp4", size=1),
                        message="Next",
                    )

        old_job = {
            "_id": "-100123:old-video-job",
            "chat_id": "-100123",
            "status": "running",
            "started_at": utc_now(),
            "files_total": 4,
            "files_processed": 3,
            "indexed": 3,
            "skipped": 0,
            "errors": 0,
            "last_message_id": 20,
        }
        db = SimpleNamespace(
            sources=SimpleNamespace(find_one=AsyncMock(return_value={"title": "Movies"})),
            get_latest_index_job=AsyncMock(return_value=old_job),
            set_index_job=AsyncMock(),
            save_movie=AsyncMock(return_value=True),
        )
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=77)),
            edit_message_text=AsyncMock(),
        )
        indexer = HistoricalIndexer(
            SimpleNamespace(historical_indexing_enabled=True),
            db,
            bot,
        )
        indexer.client = FakeClient()

        await indexer.scan_chat(-100123, 7, resume=True)
        return db.set_index_job.await_args_list[-1].args[1], iterated

    final_job, iterated = asyncio.run(run_scan())
    assert iterated == [("videos", 20)]
    assert final_job["status"] == "completed"
    assert final_job["files_processed"] == 4
    assert final_job["indexed"] == 4


def test_scan_skips_existing_file_records() -> None:
    async def run_scan() -> dict[str, Any]:
        class FakeClient:
            def is_connected(self) -> bool:
                return True

            async def get_entity(self, chat_id: int) -> str:
                return "entity"

            async def get_messages(self, entity: str, *, limit: int, filter: object) -> object:
                return SimpleNamespace(total=1)

            async def iter_messages(
                self,
                entity: str,
                *,
                reverse: bool,
                filter: object,
                min_id: int = 0,
            ):
                if isinstance(filter, InputMessagesFilterDocument):
                    yield SimpleNamespace(
                        id=1,
                        media=object(),
                        file=SimpleNamespace(name="Already.mkv", mime_type="video/mp4", size=1),
                        message="Already indexed",
                    )

        db = SimpleNamespace(
            sources=SimpleNamespace(find_one=AsyncMock(return_value={"title": "Movies"})),
            get_latest_index_job=AsyncMock(return_value=None),
            set_index_job=AsyncMock(),
            save_movie=AsyncMock(return_value=False),
        )
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=77)),
            edit_message_text=AsyncMock(),
        )
        indexer = HistoricalIndexer(
            SimpleNamespace(historical_indexing_enabled=True),
            db,
            bot,
        )
        indexer.client = FakeClient()

        await indexer.scan_chat(-100123, 7)
        return db.set_index_job.await_args_list[-1].args[1]

    final_job = asyncio.run(run_scan())
    assert final_job["indexed"] == 0
    assert final_job["skipped"] == 1
