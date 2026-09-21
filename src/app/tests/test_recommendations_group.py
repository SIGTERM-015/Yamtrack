"""Pure tests for the group recommendation engine (E8.3).

These tests exercise :func:`app.recommendations.rank_group_candidates` and its
helpers with plain dictionaries only: no database, no network, no Django models.
They mirror section 8 of the E8.1 design note (least-misery, activity-weighted
aggregation, the negative-affinity cut-off and the "para mí" mode).
"""

from django.test import SimpleTestCase

from app.recommendations import (
    GROUP_MODE_MINE,
    MAX_ACTIVITY_WEIGHT,
    MIN_MEMBER_AFFINITY,
    build_affinity,
    build_group_affinity,
    member_weight,
    rank_candidates,
    rank_group_candidates,
)

ACTION = "Action"
DRAMA = "Drama"
HORROR = "Horror"
TMDB = "tmdb"
MOVIE = "movie"


def _movie(
    media_id: str,
    score: float | None = None,
    status: str = "Completed",
    genres: list[str] | None = None,
) -> dict:
    """Build a minimal history entry for a TMDB title."""
    return {
        "source": TMDB,
        "media_id": media_id,
        "media_type": MOVIE,
        "title": f"Movie {media_id}",
        "score": score,
        "status": status,
        "genres": genres if genres is not None else [ACTION],
    }


def _history(
    prefix: str,
    count: int,
    score: float | None,
    status: str = "Completed",
    genre: str = ACTION,
) -> list[dict]:
    """Build ``count`` rated titles in a single genre."""
    return [
        _movie(f"{prefix}{index}", score=score, status=status, genres=[genre])
        for index in range(count)
    ]


def _candidate(
    media_id: str,
    genres: list[str] | None = None,
    provider_relevance: float | None = 0.5,
    quality: float | None = 0.5,
    penalty: float = 0.0,
) -> dict:
    """Build a minimal provider candidate for ranking."""
    return {
        "source": TMDB,
        "media_id": media_id,
        "media_type": MOVIE,
        "title": f"Candidate {media_id}",
        "genres": genres if genres is not None else [ACTION],
        "provider_relevance": provider_relevance,
        "quality": quality,
        "penalty": penalty,
    }


def _member(name: str, history: list[dict]) -> dict:
    return {"name": name, "history": history}


class ActivityWeightTests(SimpleTestCase):
    """Member weight and the activity-weighted group affinity."""

    def test_member_weight_counts_scored_titles(self):
        """Only scored titles count towards a member's weight."""
        member = _member("a", [*_history("a", 3, 8), _movie("a-unrated")])
        self.assertEqual(member_weight(member), 3.0)

    def test_member_weight_is_capped_to_stop_one_rater_dominating(self):
        """A very active member's weight is capped."""
        member = _member("a", _history("a", MAX_ACTIVITY_WEIGHT + 10, 8))
        self.assertEqual(member_weight(member), float(MAX_ACTIVITY_WEIGHT))

    def test_group_affinity_is_the_activity_weighted_mean(self):
        """Group affinity is the weighted mean of member vectors."""
        heavy_history = _history("heavy", MAX_ACTIVITY_WEIGHT + 10, 10)
        light_history = _history("light", 2, 2)
        members = [
            _member("heavy", heavy_history),
            _member("light", light_history),
        ]
        heavy_affinity = build_affinity(heavy_history)[ACTION]
        light_affinity = build_affinity(light_history)[ACTION]
        expected = (
            float(MAX_ACTIVITY_WEIGHT) * heavy_affinity + 2.0 * light_affinity
        ) / (float(MAX_ACTIVITY_WEIGHT) + 2.0)

        self.assertAlmostEqual(
            build_group_affinity(members)[ACTION], expected, places=6
        )

    def test_members_without_ratings_do_not_shift_the_group_vector(self):
        """A member with no ratings contributes no vote."""
        sole_rater = _history("only", 5, 9, genre=ACTION)
        members = [
            _member("rater", sole_rater),
            _member("silent", []),
        ]
        self.assertAlmostEqual(
            build_group_affinity(members)[ACTION],
            build_affinity(sole_rater)[ACTION],
            places=6,
        )


class LeastMiseryTests(SimpleTestCase):
    """The least-misery ranking ("para el grupo" mode)."""

    def _polarised_group(self) -> list[dict]:
        # ``a`` likes Action and dislikes Horror; ``b`` is the mirror image.
        # Both dislikes are mild enough to stay above the discard threshold.
        return [
            _member(
                "a",
                _history("a-action", 10, 9, genre=ACTION)
                + _history("a-horror", 10, 3, genre=HORROR),
            ),
            _member(
                "b",
                _history("b-action", 10, 3, genre=ACTION)
                + _history("b-horror", 10, 9, genre=HORROR),
            ),
        ]

    def test_compromise_outranks_the_polarising_titles(self):
        """A neutral compromise beats titles one member hates."""
        candidates = [
            _candidate("action", genres=[ACTION]),
            _candidate("horror", genres=[HORROR]),
            _candidate("drama", genres=[DRAMA]),
        ]
        ranked = rank_group_candidates(candidates, self._polarised_group())

        self.assertEqual(
            [item["media_id"] for item in ranked], ["drama", "action", "horror"]
        )
        # The compromise keeps both members' (neutral) score, while each
        # polarising title is dragged down by the member who dislikes it.
        self.assertAlmostEqual(ranked[0]["score"], 0.25, places=6)
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])

    def test_group_of_one_matches_the_individual_ranking(self):
        """A group of one degenerates to the individual ranking."""
        member = _member("a", _history("a", 10, 9))
        candidates = [
            _candidate("action", genres=[ACTION]),
            _candidate("drama", genres=[DRAMA]),
        ]

        individual = rank_candidates(candidates, member["history"])
        grouped = rank_group_candidates(candidates, [member])

        self.assertEqual(
            [item["media_id"] for item in grouped],
            [item["media_id"] for item in individual],
        )
        for group_item, solo_item in zip(grouped, individual, strict=True):
            self.assertAlmostEqual(group_item["score"], solo_item["score"], places=6)
            self.assertAlmostEqual(
                group_item["affinity"], solo_item["affinity"], places=6
            )


class RejectionTests(SimpleTestCase):
    """The negative-affinity cut-off drops disliked candidates."""

    def test_candidate_is_discarded_below_the_negative_affinity_floor(self):
        """A strong dislike drops the candidate for everyone."""
        hater = _member(
            "hater",
            _history("hater", 6, 1, status="Dropped", genre=HORROR),
        )
        lover = _member("lover", _history("lover", 10, 9, genre=HORROR))
        self.assertLess(build_affinity(hater["history"])[HORROR], MIN_MEMBER_AFFINITY)

        candidates = [
            _candidate("horror", genres=[HORROR]),
            _candidate("action", genres=[ACTION]),
        ]
        ranked = rank_group_candidates(candidates, [hater, lover])

        self.assertEqual([item["media_id"] for item in ranked], ["action"])

    def test_mild_dislike_above_the_floor_is_kept(self):
        """A mild dislike above the floor keeps the candidate."""
        mild = _member(
            "mild",
            _history("mild", 1, 1, status="Dropped", genre=HORROR),
        )
        lover = _member("lover", _history("lover", 10, 9, genre=HORROR))
        self.assertGreater(build_affinity(mild["history"])[HORROR], MIN_MEMBER_AFFINITY)

        ranked = rank_group_candidates(
            [_candidate("horror", genres=[HORROR])], [mild, lover]
        )
        self.assertEqual([item["media_id"] for item in ranked], ["horror"])


class SeenFilterTests(SimpleTestCase):
    """Already-seen filtering for both modes."""

    def test_group_mode_excludes_titles_seen_by_any_member(self):
        """Group mode drops anything any member has seen."""
        members = [
            _member("a", [_movie("seen", score=8, status="Completed")]),
            _member("b", _history("b", 5, 8)),
        ]
        candidates = [_candidate("seen"), _candidate("fresh")]
        ranked = rank_group_candidates(candidates, members)

        self.assertEqual([item["media_id"] for item in ranked], ["fresh"])

    def test_mine_mode_filters_out_what_the_others_have_seen(self):
        """In "para mí" mode, hide titles the other members have seen."""
        viewer = _member("a", _history("a", 5, 8))
        other = _member("b", [_movie("taken", score=8)])
        candidates = [_candidate("taken"), _candidate("fresh")]

        ranked = rank_group_candidates(
            candidates, [viewer, other], mode=GROUP_MODE_MINE, viewer=viewer
        )

        self.assertEqual([item["media_id"] for item in ranked], ["fresh"])

    def test_mine_mode_keeps_a_title_the_others_have_not_seen(self):
        """In "para mí" mode, keep titles nobody else has seen."""
        viewer = _member("a", _history("a", 5, 8))
        other = _member("b", [])

        ranked = rank_group_candidates(
            [_candidate("fresh")], [viewer, other], mode=GROUP_MODE_MINE, viewer=viewer
        )

        self.assertEqual([item["media_id"] for item in ranked], ["fresh"])

    def test_mine_mode_requires_a_viewer(self):
        """In "para mí" mode, an explicit viewer is required."""
        with self.assertRaises(ValueError):
            rank_group_candidates([], [_member("a", [])], mode=GROUP_MODE_MINE)


class FallbackTests(SimpleTestCase):
    """Cold start: no member has ratings."""

    def test_group_without_ratings_falls_back_to_provider_and_quality(self):
        """With no ratings, rank by provider relevance and quality."""
        members = [_member("a", []), _member("b", [])]
        candidates = [
            _candidate("high", provider_relevance=0.9, quality=0.9),
            _candidate("low", provider_relevance=0.1, quality=0.1),
        ]
        ranked = rank_group_candidates(candidates, members)

        self.assertEqual([item["media_id"] for item in ranked], ["high", "low"])
        self.assertEqual([item["affinity"] for item in ranked], [0.0, 0.0])

    def test_unknown_mode_is_rejected(self):
        """An unknown mode is rejected."""
        with self.assertRaises(ValueError):
            rank_group_candidates([], [], mode="nonsense")
