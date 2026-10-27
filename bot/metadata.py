from __future__ import annotations

import re
from typing import Any

_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_QUALITY = re.compile(r"\b(2160p|1440p|1080p|720p|480p|360p|4k|uhd|hd)\b", re.I)
_SEASON = re.compile(r"\bseason\s*[-._ ]?(\d{1,2})\b|\bS(\d{1,2})(?=E\d{1,3}\b|\b)", re.I)
_EPISODE = re.compile(r"\b(?:episode|ep|e)\s*[-._ ]?(\d{1,3})\b|\bS\d{1,2}E(\d{1,3})\b", re.I)
_LANGUAGES = (
    "Hindi", "English", "Tamil", "Telugu", "Malayalam", "Kannada", "Bengali",
    "Marathi", "Punjabi", "Korean", "Japanese", "Chinese", "Spanish", "French",
    "Dual Audio", "Multi Audio",
)


def _clean(value: str) -> str:
    value = re.sub(r"https?://\S+", " ", value, flags=re.I)
    value = re.sub(r"[@#][\w.-]+", " ", value)
    value = re.sub(r"[\[\](){}]", " ", value)
    value = re.sub(r"[._|]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -|•\n\t")


def parse_metadata(caption: str, filename: str = "") -> dict[str, Any]:
    """Extract searchable fields from a caption or filename without losing the source text."""
    raw = f"{caption}\n{filename}".strip()
    year_match = _YEAR.search(raw)
    quality_match = _QUALITY.search(raw)
    season_match = _SEASON.search(raw)
    episode_match = _EPISODE.search(raw)
    languages = [name for name in _LANGUAGES if re.search(rf"\b{re.escape(name)}\b", raw, re.I)]
    if not languages and re.search(r"\bdual\b", raw, re.I):
        languages.append("Dual Audio")

    title_source = caption.splitlines()[0] if caption.strip() else filename
    title_source = re.sub(r"\.[a-z0-9]{2,5}$", "", title_source, flags=re.I)
    title_source = re.sub(r"\b(2160p|1440p|1080p|720p|480p|360p|4k|uhd|hd)\b", " ", title_source, flags=re.I)
    title_source = _YEAR.sub(" ", title_source)
    title_source = re.sub(r"\bS\d{1,2}E\d{1,3}\b", " ", title_source, flags=re.I)
    title_source = _EPISODE.sub(" ", title_source)
    title_source = _SEASON.sub(" ", title_source)
    title_source = _clean(title_source)
    if not title_source:
        title_source = _clean(filename) or "Untitled file"

    return {
        "title": title_source[:180],
        "normalized_title": re.sub(r"[^a-z0-9]+", " ", title_source.lower()).strip(),
        "year": int(year_match.group(1)) if year_match else None,
        "quality": quality_match.group(1).upper() if quality_match else None,
        "languages": languages,
        "season": int(season_match.group(1) or season_match.group(2)) if season_match else None,
        "episode": int(episode_match.group(1) or episode_match.group(2))
        if episode_match and (episode_match.group(1) or episode_match.group(2))
        else None,
        "caption": caption[:3500],
        "filename": filename[:255],
    }


def media_filename(message: Any) -> str:
    media = (
        getattr(message, "document", None)
        or getattr(message, "video", None)
        or getattr(message, "audio", None)
        or getattr(message, "animation", None)
    )
    return getattr(media, "file_name", "") or ""


def telegram_media(message: Any) -> dict[str, str] | None:
    """Return a media kind and file identifiers for Bot API updates."""
    for kind in ("document", "video", "audio", "animation", "photo", "voice", "video_note"):
        value = getattr(message, kind, None)
        if not value:
            continue
        if kind == "photo":
            value = value[-1]
        return {
            "media_type": kind,
            "file_id": value.file_id,
            "file_unique_id": value.file_unique_id,
        }
    return None

