"""Random selection engine for the roulette feature (E7.2).

Pure logic: given a pool of items and optional filters, return one item at
random. No database access, no UI concerns. Genres are read through a
pluggable accessor so this module stays decoupled from the final ``Genre``
model introduced by E7.1.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = ["pick_random"]


def _media_type_value(media_type: Any) -> Any:
    """Accept raw strings as well as ``MediaTypes``-style enums."""
    return getattr(media_type, "value", media_type)


def _genre_names(item: Any) -> list[str]:
    """Best-effort read of an item's genres without importing the Genre model."""
    genres = getattr(item, "genres", ())
    # Works with the E7.1 M2M manager (``.all()``) and plain iterables.
    if callable(getattr(genres, "all", None)):
        genres = genres.all()
    return [str(genre).strip() for genre in genres if genre]


def _matches(
    item: Any,
    media_type: Any,
    genre_filter: set[str],
    genre_getter: Callable[[Any], Iterable[Any]],
) -> bool:
    """Return whether ``item`` satisfies the active filters."""
    if media_type is not None and getattr(item, "media_type", None) != media_type:
        return False
    if not genre_filter:
        return True
    item_genres = {
        str(genre).strip().casefold() for genre in genre_getter(item) if genre
    }
    return bool(item_genres & genre_filter)


def pick_random(
    items: Iterable[Any],
    media_type: Any = None,
    genres: Iterable[str] | None = None,
    *,
    genre_getter: Callable[[Any], Iterable[Any]] | None = None,
    rng: random.Random | None = None,
) -> Any | None:
    """Pick one item at random from those matching the given filters.

    Args:
        items: Candidate pool (list, queryset, ...).
        media_type: Optional media type filter; a string or a
            ``MediaTypes`` member.
        genres: Optional genre names. An item matches when it shares at
            least one genre (case-insensitive). Empty/``None`` = any genre.
        genre_getter: Optional callable returning an item's genre names.
            Defaults to reading ``item.genres`` (M2M manager or plain
            iterable), which keeps this module decoupled from the final
            ``Genre`` model.
        rng: Optional ``random.Random`` instance for deterministic calls.

    Returns:
        A matching item, or ``None`` when nothing matches.
    """
    media_type = _media_type_value(media_type)
    genre_filter = {str(genre).strip().casefold() for genre in (genres or ()) if genre}
    get_genres = genre_getter or _genre_names

    candidates = [
        item for item in items if _matches(item, media_type, genre_filter, get_genres)
    ]
    if not candidates:
        return None
    return (rng or random).choice(candidates)
