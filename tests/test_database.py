import asyncio
from types import SimpleNamespace

from bot.database import Database


class FakeCollection:
    def __init__(self, *, count: int = 0, deleted: int = 0, upserted_id=None):
        self.count = count
        self.deleted = deleted
        self.upserted_id = upserted_id
        self.update_calls = []
        self.delete_calls = []

    async def count_documents(self, query):
        return self.count

    async def delete_many(self, query):
        self.delete_calls.append(query)
        return SimpleNamespace(deleted_count=self.deleted)

    async def update_one(self, query, update, *, upsert):
        self.update_calls.append((query, update, upsert))
        return SimpleNamespace(upserted_id=self.upserted_id)


class FakeBulkCollection:
    def __init__(self, upserted_count: int):
        self.upserted_count = upserted_count
        self.operations = []
        self.ordered = None

    async def bulk_write(self, operations, *, ordered):
        self.operations = operations
        self.ordered = ordered
        return SimpleNamespace(upserted_count=self.upserted_count)


def test_save_movie_inserts_once_without_rewriting_existing_index_records() -> None:
    async def run_case():
        db = Database.__new__(Database)
        db.movies = FakeCollection(upserted_id="new-record")
        document = {
            "source_chat_id": "-100123",
            "source_message_id": 42,
            "filename": "Movie.mkv",
        }
        inserted = await db.save_movie(document)
        query, update, upsert = db.movies.update_calls[0]

        db.movies.upserted_id = None
        duplicate_inserted = await db.save_movie(document)
        return inserted, duplicate_inserted, query, update, upsert, db.movies.update_calls[1][1]

    inserted, duplicate_inserted, query, update, upsert, duplicate_update = asyncio.run(run_case())
    assert inserted is True
    assert duplicate_inserted is False
    assert query == {"source_chat_id": "-100123", "source_message_id": 42}
    assert upsert is True
    assert "$setOnInsert" in update
    assert "$set" not in update
    assert "$setOnInsert" in duplicate_update


def test_save_movies_bulk_reports_new_records_without_overwriting_existing() -> None:
    async def run_case():
        db = Database.__new__(Database)
        db.movies = FakeBulkCollection(upserted_count=1)
        documents = [
            {"source_chat_id": -100123, "source_message_id": 42, "filename": "Movie.mkv"},
            {"source_chat_id": "-100123", "source_message_id": 43, "filename": "Clip.mp4"},
        ]
        inserted = await db.save_movies_bulk(documents)
        return inserted, db.movies

    inserted, movies = asyncio.run(run_case())
    assert inserted == 1
    assert movies.ordered is False
    assert len(movies.operations) == 2
    assert all("$setOnInsert" in operation._doc for operation in movies.operations)
    assert all("$set" not in operation._doc for operation in movies.operations)
    assert movies.operations[0]._filter == {
        "source_chat_id": "-100123",
        "source_message_id": 42,
    }


def test_clear_index_data_only_removes_movie_index_and_resume_checkpoints() -> None:
    async def run_case():
        db = Database.__new__(Database)
        db.movies = FakeCollection(deleted=6000)
        db.index_jobs = FakeCollection(deleted=1)
        result = await db.clear_index_data()
        return result, db.movies.delete_calls, db.index_jobs.delete_calls

    result, movie_deletes, checkpoint_deletes = asyncio.run(run_case())
    assert result == {"files": 6000, "checkpoints": 1}
    assert movie_deletes == [{}]
    assert checkpoint_deletes == [{}]


def test_index_data_counts_report_only_movies_and_checkpoints() -> None:
    async def run_case():
        db = Database.__new__(Database)
        db.movies = FakeCollection(count=6000)
        db.index_jobs = FakeCollection(count=1)
        return await db.index_data_counts()

    assert asyncio.run(run_case()) == {"files": 6000, "checkpoints": 1}
