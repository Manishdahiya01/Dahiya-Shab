import asyncio
import re
from types import SimpleNamespace

from bot.database import Database


class FakeSearchCursor:
    def __init__(self, records):
        self.records = records
        self.limit_count = None

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        self.limit_count = count
        return self

    async def to_list(self, length=None):
        count = length if length is not None else self.limit_count
        return self.records[:count]


def _matches_search_filter(record, query):
    for key, condition in query.items():
        if key == "$and":
            if not all(_matches_search_filter(record, part) for part in condition):
                return False
        elif key == "$or":
            if not any(_matches_search_filter(record, part) for part in condition):
                return False
        elif isinstance(condition, dict) and "$regex" in condition:
            flags = re.IGNORECASE if condition.get("$options") == "i" else 0
            if not re.search(condition["$regex"], str(record.get(key) or ""), flags):
                return False
        elif isinstance(condition, dict) and "$in" in condition:
            actual = record.get(key)
            actual_values = actual if isinstance(actual, list) else [actual]
            if not any(value in condition["$in"] for value in actual_values):
                return False
        elif isinstance(condition, dict) and "$all" in condition:
            actual = record.get(key) or []
            if isinstance(actual, str):
                actual = [actual]
            if not set(condition["$all"]) <= set(actual):
                return False
        elif record.get(key) != condition:
            return False
    return True


class FakeSearchCollection:
    def __init__(self, records):
        self.records = records

    def find(self, query=None, *_args, **_kwargs):
        query = query or {}
        return FakeSearchCursor(
            [record for record in self.records if _matches_search_filter(record, query)]
        )


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


def test_make_delivery_keeps_request_message_id_when_existing_timer_is_refreshed() -> None:
    async def run_case():
        db = Database.__new__(Database)
        db.deliveries = FakeCollection()
        await db.make_delivery(7, 200, 201, "expires", request_message_id=100)
        await db.make_delivery(7, 200, 201, "expires-again")
        return db.deliveries.update_calls

    calls = asyncio.run(run_case())
    assert calls[0][1]["$set"]["request_message_id"] == 100
    assert "request_message_id" not in calls[1][1]["$set"]


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


def test_search_does_not_match_a_single_query_word_from_title_or_caption() -> None:
    async def run_case():
        db = Database.__new__(Database)
        db.movies = FakeSearchCollection(
            [
                {
                    "title": "Pokemon Movie",
                    "normalized_title": "pokemon movie",
                    "caption": "Old film collection",
                },
                {
                    "title": "Old Classics",
                    "normalized_title": "old classics",
                    "caption": "Pokemon collection",
                },
            ]
        )
        return await db.search_movies("Pokemon old")

    assert asyncio.run(run_case()) == []


def test_search_accepts_symbols_and_small_spelling_errors_without_caption_hits() -> None:
    async def run_case():
        db = Database.__new__(Database)
        matching = {
            "title": "Pokemon Old",
            "normalized_title": "pokemon old",
            "caption": "Collection",
        }
        caption_only = {
            "title": "Old Classics",
            "normalized_title": "old classics",
            "caption": "Pokemon",
        }
        db.movies = FakeSearchCollection([matching, caption_only])
        separator_match = await db.search_movies("Poke-mon / Old")
        typo_match = await db.search_movies("Pokemom Odl")
        return separator_match, typo_match

    separator_match, typo_match = asyncio.run(run_case())
    assert [item["title"] for item in separator_match] == ["Pokemon Old"]
    assert [item["title"] for item in typo_match] == ["Pokemon Old"]


def test_fuzzy_setting_only_toggles_tolerant_matching() -> None:
    async def run_case():
        db = Database.__new__(Database)
        db.movies = FakeSearchCollection(
            [
                {
                    "title": "Pokemon Old",
                    "normalized_title": "pokemon old",
                    "caption": "",
                }
            ]
        )
        exact_match = await db.search_movies("Pokemon Old", fuzzy_enabled=False)
        typo_match = await db.search_movies("Pokemom Odl", fuzzy_enabled=False)
        return exact_match, typo_match

    exact_match, typo_match = asyncio.run(run_case())
    assert [item["title"] for item in exact_match] == ["Pokemon Old"]
    assert typo_match == []


def test_search_matches_season_metadata_and_split_title_spelling() -> None:
    async def run_case():
        db = Database.__new__(Database)
        db.movies = FakeSearchCollection(
            [
                {
                    "title": "Crya_yon shincha_an",
                    "normalized_title": "crya yon shincha an",
                    "caption": "crya_yon shincha_an S01",
                    "filename": "",
                    "season": None,
                },
                {
                    "title": "Shinchan Movie",
                    "normalized_title": "shinchan movie",
                    "caption": "Season 2",
                    "filename": "",
                    "season": None,
                },
            ]
        )
        return await db.search_movies("Shinchan season 1")

    assert [item["title"] for item in asyncio.run(run_case())] == [
        "Crya_yon shincha_an"
    ]
