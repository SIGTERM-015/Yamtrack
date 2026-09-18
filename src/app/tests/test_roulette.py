"""Tests for the roulette random-selection engine (E7.2).

Pure tests: no database, no network.
"""

import random
from types import SimpleNamespace

from django.test import SimpleTestCase

from app.models import MediaTypes
from app.roulette import pick_random


def rng(seed=None):
    """Deterministic, non-cryptographic RNG for tests."""
    return random.Random(seed)  # noqa: S311


def item(media_type, genres=(), **extra):
    """Build a lightweight stand-in for an Item."""
    return SimpleNamespace(media_type=media_type, genres=list(genres), **extra)


class PickRandomTest(SimpleTestCase):
    """Test pick_random filters and edge cases."""

    def setUp(self):
        """Build a small mixed pool used by most tests."""
        self.pool = [
            item(MediaTypes.MOVIE.value, ["Action", "Drama"]),
            item(MediaTypes.MOVIE.value, ["Comedy"]),
            item(MediaTypes.TV.value, ["Drama", "Thriller"]),
            item(MediaTypes.ANIME.value, ["Action"]),
            item(MediaTypes.BOOK.value, []),
        ]

    def test_no_filters_returns_a_pool_member(self):
        """Without filters any item in the pool can be returned."""
        for _ in range(50):
            self.assertIn(pick_random(self.pool, rng=rng()), self.pool)

    def test_filters_by_media_type(self):
        """Every returned item matches the requested media type."""
        for _ in range(50):
            picked = pick_random(self.pool, media_type="movie", rng=rng())
            self.assertEqual(picked.media_type, "movie")

    def test_accepts_media_type_enum(self):
        """A MediaTypes member is accepted as a media type filter."""
        picked = pick_random(self.pool, media_type=MediaTypes.TV, rng=rng())
        self.assertEqual(picked.media_type, MediaTypes.TV.value)

    def test_filters_by_genre(self):
        """Every returned item carries the requested genre."""
        for _ in range(50):
            picked = pick_random(self.pool, genres=["action"], rng=rng())
            self.assertIn("Action", picked.genres)

    def test_genre_filter_is_case_insensitive(self):
        """Genre names are compared ignoring case."""
        picked = pick_random(self.pool, genres=["DRAMA"], rng=rng())
        self.assertTrue({"Drama"} <= set(picked.genres))

    def test_genre_filter_matches_any_selected_genre(self):
        """An item matches when it has at least one of the selected genres."""
        candidates = [
            item(MediaTypes.MOVIE.value, ["Horror"]),
            item(MediaTypes.MOVIE.value, ["Comedy", "Horror"]),
        ]
        for _ in range(50):
            picked = pick_random(candidates, genres=["horror", "comedy"])
            self.assertTrue(set(picked.genres) & {"Horror", "Comedy"})

    def test_combines_media_type_and_genre_filters(self):
        """Both filters apply at once."""
        picked = pick_random(self.pool, media_type="tv", genres=["drama"], rng=rng())
        self.assertEqual(picked.media_type, "tv")
        self.assertIn("Drama", picked.genres)

    def test_returns_none_when_no_item_matches(self):
        """No match returns None instead of raising."""
        self.assertIsNone(pick_random(self.pool, media_type="game"))
        self.assertIsNone(pick_random(self.pool, genres=["nonexistent"]))
        self.assertIsNone(pick_random(self.pool, media_type="game", genres=["action"]))

    def test_returns_none_for_empty_pool(self):
        """An empty pool returns None."""
        self.assertIsNone(pick_random([], media_type="movie"))

    def test_empty_genre_filter_means_any_genre(self):
        """An empty genre selection does not filter anything out."""
        for _ in range(30):
            picked = pick_random(self.pool, genres=[], rng=rng())
            self.assertIn(picked, self.pool)

    def test_rng_makes_selection_deterministic(self):
        """The same seed yields the same pick."""
        first = pick_random(self.pool, rng=rng(7))
        second = pick_random(self.pool, rng=rng(7))
        self.assertIs(first, second)

    def test_default_genre_getter_reads_m2m_like_manager(self):
        """The default accessor supports an ``.all()`` M2M-style manager."""

        class FakeGenre:
            def __init__(self, name):
                self.name = name

            def __str__(self):
                return self.name

        class FakeM2M:
            def __init__(self, names):
                self._names = names

            def all(self):
                return [FakeGenre(n) for n in self._names]

        candidate = SimpleNamespace(
            media_type="movie", genres=FakeM2M(["Action", "Adventure"])
        )
        picked = pick_random([candidate], genres=["action"], rng=rng())
        self.assertIs(picked, candidate)

    def test_custom_genre_getter_decouples_from_genre_model(self):
        """A custom accessor can read genres from anywhere."""
        pool = [
            SimpleNamespace(media_type="movie", tags=["Drama"]),
            SimpleNamespace(media_type="movie", tags=["Comedy"]),
        ]
        picked = pick_random(
            pool,
            genres=["drama"],
            genre_getter=lambda candidate: candidate.tags,
            rng=rng(),
        )
        self.assertIs(picked, pool[0])
