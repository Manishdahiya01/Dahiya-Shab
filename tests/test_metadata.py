

def test_parse_caption_fields() -> None:
    result = parse_metadata("Dahiya Movie 2024 Hindi 1080p S02E03", "episode.mkv")
    assert result["year"] == 2024
    assert result["quality"] == "1080P"
    assert result["languages"] == ["Hindi"]
    assert result["season"] == 2
    assert result["episode"] == 3
    assert "dahiya movie" in result["normalized_title"]


def test_filename_fallback() -> None:
    result = parse_metadata("", "Sample.Film.2021.720p.mkv")
    assert result["year"] == 2021
    assert result["quality"] == "720P"
    assert "Sample Film" in result["title"]
