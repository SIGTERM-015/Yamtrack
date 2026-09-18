"""Pure tests for the individual recommendation scoring engine.

These tests exercise :mod:`app.recommendations` with plain dictionaries only:
no database, no network, no Django models.  They mirror the sections of the
E8.1 design note (signal, affinity, ranking, exclusion, cold start).
"""

from django.test import SimpleTestCase

from app.recommendations import (
    Weights,
    build_affinity,
    contribution,
    genre_affinity,
    item_key,
    normalise_score,
    rank_candidates,
    rated_count,
    seen_keys,
    status_weight,
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
    media_type: str = MOVIE,
    season_number: int | None = None,
) -> dict:
    """Build a minimal history entry for a TMDB title."""
    entry = {
        "source": TMDB,
        "media_id": media_id,
        "media_type": media_type,
        "title": f"Movie {media_id}",
        "score": score,
        "status": status,
        "genres": genres if genres is not None else [ACTION],
    }
    if season_number is not None:
        entry["season_number"] = season_number
    return entry


def _candidate(
    media_id: str,
    genres: list[str] | None = None,
    provider_relevance: float | None = 0.5,
    quality: float | None = 0.5,
    penalty: float = 0.0,
    media_type: str = MOVIE,
    **extra: object,
) -> dict:
    """Build a minimal provider candidate for ranking."""
    candidate = {
        "source": TMDB,
        "media_id": media_id,
        "media_type": media_type,
        "title": f"Candidate {media_id}",
        "genres": genres if genres is not None else [ACTION],
        "provider_relevance": provider_relevance,
        "quality": quality,
        "penalty": penalty,
    }
    candidate.update(extra)
    return candidate


class SignalTests(SimpleTestCase):
    """§4 -- score normalisation, status weighting and contribution."""

    def test_normalise_score_centres_on_neutral_five(self):
        """A score of 5 is neutral and the 0-10 range maps to [-1, 1]."""
        self.assertEqual(normalise_score(5.0), 0.0)
        self.assertEqual(normalise_score(10.0), 1.0)
        self.assertEqual(normalise_score(0.0), -1.0)
        self.assertEqual(normalise_score(7.5), 0.5)

    def test_normalise_score_is_none_without_a_score(self):
        """A missing score yields no numeric signal."""
        self.assertIsNone(normalise_score(None))

    def test_normalise_score_clamps_out_of_range_values(self):
        """Values outside 0-10 are clamped instead of overflowing the range."""
        self.assertEqual(normalise_score(20.0), 1.0)
        self.assertEqual(normalise_score(-5.0), -1.0)

    def test_status_weight_matches_the_design_table(self):
        """Each known status has its documented profile weight."""
        self.assertEqual(status_weight("Completed"), 1.0)
        self.assertEqual(status_weight("Dropped"), -1.0)
        self.assertEqual(status_weight("Paused"), 0.3)
        self.assertEqual(status_weight("In progress"), 0.2)
        self.assertEqual(status_weight("Planning"), 0.0)

    def test_status_weight_is_zero_for_unknown_status(self):
        """An unrecognised status contributes nothing to the profile."""
        self.assertEqual(status_weight("Rewatching"), 0.0)

    def test_contribution_scales_score_by_status(self):
        """A completed title contributes its normalised score."""
        self.assertAlmostEqual(contribution(9.0, "Completed"), 0.8, places=6)

    def test_dropped_ignores_score_and_is_always_negative(self):
        """Abandoning a title is a negative signal even if it was scored high."""
        self.assertEqual(contribution(10.0, "Dropped"), -1.0)
        self.assertEqual(contribution(None, "Dropped"), -1.0)

    def test_completed_without_score_is_a_mild_positive(self):
        """Marking a title watched without rating it still nudges the profile."""
        self.assertAlmostEqual(contribution(None, "Completed"), 0.2, places=6)

    def test_planning_never_contributes(self):
        """Planning reflects intent, not taste, so it is neutral."""
        self.assertEqual(contribution(10.0, "Planning"), 0.0)
        self.assertEqual(contribution(None, "Planning"), 0.0)


class AffinityTests(SimpleTestCase):
    """§4.3/§5 -- per-genre affinity and cold-start smoothing."""

    def test_affinity_averages_signal_over_rated_titles(self):
        """Ten strong ratings in one genre yield total/ (count + K)."""
        history = [_movie(str(index), 10.0, genres=[ACTION]) for index in range(10)]
        affinity = build_affinity(history, media_type=MOVIE)
        self.assertAlmostEqual(affinity[ACTION], 10.0 / 15.0, places=6)

    def test_cold_start_applies_stronger_smoothing(self):
        """A single rating is shrunk far more than a mature profile."""
        one = build_affinity([_movie("1", 10.0, genres=[ACTION])], media_type=MOVIE)
        self.assertAlmostEqual(one[ACTION], 1.0 / 51.0, places=6)
        self.assertLess(one[ACTION], 10.0 / 15.0)

    def test_positive_and_negative_signals_mix(self):
        """A liked and a dropped title in the same genre partly cancel out."""
        history = [
            _movie("1", 10.0, genres=[ACTION]),
            _movie("2", 9.0, status="Dropped", genres=[ACTION]),
        ]
        affinity = build_affinity(history, media_type=MOVIE)
        self.assertAlmostEqual(affinity[ACTION], 0.0, places=6)

    def test_affinity_is_isolated_per_media_type(self):
        """Genres from different media types are not comparable."""
        history = [
            _movie("1", 10.0, genres=[ACTION], media_type=MOVIE),
            _movie("2", 10.0, genres=[DRAMA], media_type="game"),
        ]
        movies = build_affinity(history, media_type=MOVIE)
        games = build_affinity(history, media_type="game")
        self.assertIn(ACTION, movies)
        self.assertNotIn(DRAMA, movies)
        self.assertIn(DRAMA, games)
        self.assertNotIn(ACTION, games)

    def test_unrated_titles_do_not_seed_the_profile(self):
        """Planning titles never build affinity, whatever their genre."""
        history = [_movie("1", None, status="Planning", genres=[ACTION])]
        self.assertEqual(build_affinity(history, media_type=MOVIE), {})

    def test_rated_count_ignores_unscored_titles(self):
        """Only titles with a score count towards the cold-start threshold."""
        history = [_movie("1", 8.0), _movie("2", None), _movie("3", 7.0)]
        self.assertEqual(rated_count(history, media_type=MOVIE), 2)

    def test_genre_affinity_averages_known_genres(self):
        """Affinity is the mean over the genres the candidate declares."""
        affinity = {ACTION: 0.6, DRAMA: 0.2}
        self.assertAlmostEqual(genre_affinity([ACTION, DRAMA], affinity), 0.4, places=6)

    def test_genre_affinity_defaults_to_zero(self):
        """Unknown or absent genres score neutral."""
        self.assertEqual(genre_affinity([HORROR], {ACTION: 0.6}), 0.0)
        self.assertEqual(genre_affinity(None, {ACTION: 0.6}), 0.0)


class IdentityTests(SimpleTestCase):
    """§7 -- identity of a title for the "already seen" filter."""

    def test_provider_key_uses_source_id_and_type(self):
        """Provider titles are keyed by (source, media_id, media_type)."""
        self.assertEqual(item_key(_movie("42")), (TMDB, "42", MOVIE))

    def test_season_key_includes_the_season_number(self):
        """Two seasons of one show are different titles."""
        first = _movie("100", media_type="season", season_number=1)
        second = _movie("100", media_type="season", season_number=2)
        self.assertNotEqual(item_key(first), item_key(second))

    def test_manual_title_falls_back_to_normalised_title(self):
        """Manual entries without a provider id are matched by title."""
        first = {"source": "manual", "title": "Some  Show", "media_type": "tv"}
        second = {"source": "manual", "title": "Some Show", "media_type": "tv"}
        self.assertEqual(item_key(first), item_key(second))

    def test_seen_keys_cover_every_engaged_status(self):
        """Completed, in-progress, dropped and planning all count as seen."""
        history = [
            _movie("1", status="Completed"),
            _movie("2", status="In progress"),
            _movie("3", status="Dropped"),
            _movie("4", status="Planning"),
        ]
        self.assertEqual(len(seen_keys(history)), 4)
        self.assertIn((TMDB, "1", MOVIE), seen_keys(history))
        self.assertIn((TMDB, "4", MOVIE), seen_keys(history))


class RankingTests(SimpleTestCase):
    """§6 -- candidate scoring, ordering and explanations."""

    def _action_history(self) -> list[dict]:
        return [_movie(f"h{index}", 9.0, genres=[ACTION]) for index in range(10)]

    def test_preferred_genre_outranks_an_unknown_one(self):
        """Two otherwise equal candidates: the liked genre wins."""
        ranked = rank_candidates(
            [
                _candidate("1", genres=[ACTION]),
                _candidate("2", genres=[HORROR]),
            ],
            self._action_history(),
            media_type=MOVIE,
        )
        self.assertEqual(ranked[0]["media_id"], "1")
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])

    def test_missing_provider_relevance_redistributes_weight(self):
        """A candidate with no provider signal is not penalised for it."""
        ranked = rank_candidates(
            [
                _candidate("1", provider_relevance=0.0),
                _candidate("2", provider_relevance=None),
            ],
            [],
            media_type=MOVIE,
        )
        self.assertEqual(ranked[0]["media_id"], "2")
        self.assertAlmostEqual(ranked[1]["score"], 0.1, places=6)
        self.assertAlmostEqual(ranked[0]["score"], 0.1 / 0.7, places=6)

    def test_penalty_is_subtracted_from_the_score(self):
        """A penalised candidate scores lower than an identical clean one."""
        ranked = rank_candidates(
            [
                _candidate("1", penalty=0.0),
                _candidate("2", penalty=0.4),
            ],
            self._action_history(),
            media_type=MOVIE,
        )
        self.assertEqual(ranked[0]["media_id"], "1")
        self.assertAlmostEqual(ranked[0]["score"] - ranked[1]["score"], 0.4, places=6)

    def test_custom_weights_change_the_balance(self):
        """Passing weights through alters the ranking as documented."""
        provider_only = Weights(alpha=1.0, beta=0.0, gamma=0.0)
        ranked = rank_candidates(
            [
                _candidate("1", genres=[HORROR], provider_relevance=0.9),
                _candidate("2", genres=[ACTION], provider_relevance=0.1),
            ],
            self._action_history(),
            media_type=MOVIE,
            weights=provider_only,
        )
        self.assertEqual(ranked[0]["media_id"], "1")

    def test_ties_break_on_title_for_deterministic_output(self):
        """Equal scores are ordered alphabetically, not by input order."""
        first = _candidate("1", genres=[HORROR], title="Zeta")
        second = _candidate("2", genres=[HORROR], title="Alpha")
        ranked = rank_candidates([first, second], [], media_type=MOVIE)
        self.assertEqual(ranked[0]["title"], "Alpha")

    def test_limit_truncates_the_ranking(self):
        """Only the requested number of candidates is returned."""
        candidates = [_candidate(str(index), genres=[HORROR]) for index in range(5)]
        ranked = rank_candidates(candidates, [], media_type=MOVIE, limit=2)
        self.assertEqual(len(ranked), 2)

    def test_top_genre_explains_the_recommendation(self):
        """The dominant positively-rated genre is reported for the UI."""
        ranked = rank_candidates(
            [_candidate("1", genres=[ACTION, DRAMA])],
            self._action_history(),
            media_type=MOVIE,
        )
        self.assertEqual(ranked[0]["top_genre"], ACTION)

    def test_top_genre_is_none_without_positive_affinity(self):
        """Nothing is claimed as the reason when every genre is neutral."""
        candidate = _candidate("1", genres=[HORROR])
        ranked = rank_candidates([candidate], [], media_type=MOVIE)
        self.assertIsNone(ranked[0]["top_genre"])

    def test_empty_history_falls_back_to_provider_and_quality(self):
        """Cold-start users are ranked on provider and community signal."""
        ranked = rank_candidates(
            [
                _candidate("1", provider_relevance=0.9, quality=0.1),
                _candidate("2", provider_relevance=0.1, quality=0.9),
            ],
            [],
            media_type=MOVIE,
        )
        self.assertEqual(ranked[0]["media_id"], "1")


class ExclusionTests(SimpleTestCase):
    """§7 -- never recommend something the user has already engaged with."""

    def test_watched_titles_are_never_recommended(self):
        """Every engaged status is filtered out of the candidate pool."""
        history = [
            _movie("1", status="Completed"),
            _movie("2", status="In progress"),
            _movie("3", status="Dropped"),
            _movie("4", status="Planning"),
        ]
        candidates = [_candidate(str(index)) for index in range(1, 5)]
        ranked = rank_candidates(candidates, history, media_type=MOVIE)
        self.assertEqual(ranked, [])

    def test_unseen_titles_survive_the_filter(self):
        """A title absent from the history is still recommended."""
        ranked = rank_candidates([_candidate("99")], [_movie("1")], media_type=MOVIE)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["media_id"], "99")

    def test_only_the_watched_season_is_excluded(self):
        """Holding season 1 does not block a recommendation of season 2."""
        history = [
            {
                "source": TMDB,
                "media_id": "100",
                "media_type": "season",
                "season_number": 1,
                "status": "Completed",
                "genres": [DRAMA],
            },
        ]
        season_two = _candidate("100", media_type="season", season_number=2)
        season_one = _candidate("100", media_type="season", season_number=1)
        ranked = rank_candidates([season_one, season_two], history, media_type="season")
        self.assertEqual([item["season_number"] for item in ranked], [2])

    def test_manual_titles_match_by_normalised_title(self):
        """Manual history without a provider id blocks the same title."""
        history = [
            {
                "source": "manual",
                "title": "Some  Show",
                "media_type": "tv",
                "status": "Completed",
                "genres": [DRAMA],
            },
        ]
        candidate = {
            "source": "manual",
            "title": "some show",
            "media_type": "tv",
            "genres": [DRAMA],
            "provider_relevance": 0.5,
            "quality": 0.5,
        }
        self.assertEqual(rank_candidates([candidate], history, media_type="tv"), [])

    def test_episodes_are_not_recommended(self):
        """Episodes are excluded from the pool by design."""
        candidate = _candidate("1", media_type="episode")
        self.assertEqual(rank_candidates([candidate], [], media_type="episode"), [])

    def test_explicitly_excluded_identities_are_dropped(self):
        """A caller-supplied exclusion set removes candidates too."""
        candidate = _candidate("7")
        ranked = rank_candidates(
            [candidate],
            [],
            media_type=MOVIE,
            excluded={item_key(candidate)},
        )
        self.assertEqual(ranked, [])

    def test_media_type_filter_ignores_other_types(self):
        """A movie ranking never leaks game candidates in."""
        ranked = rank_candidates(
            [_candidate("1", media_type="game"), _candidate("2", media_type=MOVIE)],
            [],
            media_type=MOVIE,
        )
        self.assertEqual([item["media_id"] for item in ranked], ["2"])
