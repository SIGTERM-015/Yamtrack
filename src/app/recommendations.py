"""Pure scoring engine for individual media recommendations.

This module implements the individual-user half of the algorithm agreed in the
E8.1 design note (``docs/research/recomendaciones-algoritmo.md``): build a
per-genre taste profile from the user's history and use it to rank provider
candidates.  It is deliberately free of Django, database and network
dependencies -- callers pass plain dictionaries in and get a ranked list out --
which keeps it easy to test and lets the group engine (E8.3) reuse the same
scoring blocks.

Expected shapes (all keys optional unless stated):

``history`` entries
    ``media_type``, ``source``, ``media_id``, ``title``, ``season_number``,
    ``score`` (0-10 or ``None``), ``status`` and ``genres`` (list of strings).

``candidate`` entries
    Same identity keys as history plus ``genres``, ``provider_relevance``
    (0-1), ``quality`` (0-1, already normalised per provider) and ``penalty``
    (0-1, higher means worse).
"""

from __future__ import annotations

from dataclasses import dataclass

NEUTRAL_SCORE = 5.0
SCORE_RANGE = 5.0
COMPLETED_NO_SCORE_SIGNAL = 0.2

DEFAULT_K = 5.0
MIN_RATINGS = 10
DEFAULT_ALPHA = 0.3
DEFAULT_BETA = 0.5
DEFAULT_GAMMA = 0.2
DEFAULT_DELTA = 1.0

_STATUS_WEIGHTS = {
    "completed": 1.0,
    "paused": 0.3,
    "in progress": 0.2,
    "planning": 0.0,
    "dropped": -1.0,
}
_SEEN_STATUSES = frozenset({"completed", "in progress", "dropped", "planning"})
_FALLBACK_SOURCE = "manual"


@dataclass(frozen=True)
class Weights:
    """Scoring weights and smoothing constant for :func:`rank_candidates`."""

    alpha: float = DEFAULT_ALPHA
    beta: float = DEFAULT_BETA
    gamma: float = DEFAULT_GAMMA
    delta: float = DEFAULT_DELTA
    k: float = DEFAULT_K


def _status_key(status: object) -> str:
    """Normalise a status label to its lower-case, space-collapsed form."""
    return " ".join(str(status or "").strip().lower().split())


def _clamp_unit(value: object) -> float:
    """Clamp a numeric value to the ``[0, 1]`` range."""
    return max(0.0, min(1.0, float(value)))


def _matches_media_type(entry: dict, media_type: str | None) -> bool:
    """Return True when an entry belongs to the requested media type."""
    return media_type is None or entry.get("media_type") == media_type


def normalise_score(score: object) -> float | None:
    """Map a 0-10 score onto a ``[-1, 1]`` signal centred on neutral 5."""
    if score is None:
        return None
    return max(-1.0, min(1.0, (float(score) - NEUTRAL_SCORE) / SCORE_RANGE))


def status_weight(status: object) -> float:
    """Return the profile weight for a status label (0.0 when unknown)."""
    return _STATUS_WEIGHTS.get(_status_key(status), 0.0)


def contribution(score: object, status: object) -> float:
    """Return a title's signed contribution to the taste profile.

    ``Dropped`` is a blanket rejection regardless of score, a ``Completed``
    title without a score counts as a mild positive, and ``Planning`` never
    contributes.
    """
    key = _status_key(status)
    if key == "dropped":
        return -1.0
    ratio = normalise_score(score)
    if ratio is None:
        return COMPLETED_NO_SCORE_SIGNAL if key == "completed" else 0.0
    return status_weight(key) * ratio


def rated_count(history: list[dict], media_type: str | None = None) -> int:
    """Count scored titles, optionally restricted to one media type."""
    return sum(
        1
        for entry in history
        if _matches_media_type(entry, media_type) and entry.get("score") is not None
    )


def _effective_k(history: list[dict], media_type: str | None, k: float) -> float:
    """Return the smoothing constant, raised while the profile is immature."""
    count = rated_count(history, media_type)
    if count <= 0:
        return k
    return k * max(1.0, MIN_RATINGS / count)


def build_affinity(
    history: list[dict],
    media_type: str | None = None,
    k: float = DEFAULT_K,
) -> dict[str, float]:
    """Build the per-genre affinity vector from a user's history.

    Each genre accumulates the status-weighted signal of every title carrying
    it, shrunk towards zero by ``n_g + effective_k`` so a handful of ratings
    cannot dominate.  Below :data:`MIN_RATINGS` scores the effective smoothing
    grows, keeping the vector conservative.
    """
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for entry in history:
        if not _matches_media_type(entry, media_type):
            continue
        signal = contribution(entry.get("score"), entry.get("status"))
        if signal == 0.0 and entry.get("score") is None:
            continue
        for genre in entry.get("genres") or ():
            totals[genre] = totals.get(genre, 0.0) + signal
            counts[genre] = counts.get(genre, 0) + 1
    smoothing = _effective_k(history, media_type, k)
    return {
        genre: total / (counts[genre] + smoothing) for genre, total in totals.items()
    }


def genre_affinity(genres: list[str] | None, affinity: dict[str, float]) -> float:
    """Return the mean affinity across a candidate's known genres."""
    scored = [affinity[genre] for genre in genres or () if genre in affinity]
    if not scored:
        return 0.0
    return sum(scored) / len(scored)


def _normalise_title(title: object) -> str:
    """Lower-case and collapse whitespace in a title for comparison."""
    return " ".join(str(title or "").strip().lower().split())


def item_key(entry: dict) -> tuple[str, ...]:
    """Return the identity used to tell whether a title is already held.

    Follows the E8.1 rule -- ``(source, media_id, media_type)`` plus the season
    number for seasons -- with a normalised-title fallback for manual entries
    that have no provider id.
    """
    source = str(entry.get("source") or "").strip().lower()
    media_type = str(entry.get("media_type") or "").strip().lower()
    if source == _FALLBACK_SOURCE or entry.get("media_id") in (None, ""):
        return (_FALLBACK_SOURCE, _normalise_title(entry.get("title")), media_type)
    key = (source, str(entry["media_id"]), media_type)
    if media_type == "season" and entry.get("season_number") is not None:
        return (*key, str(entry["season_number"]))
    return key


def seen_keys(history: list[dict]) -> set[tuple[str, ...]]:
    """Return the identities of every title the user has engaged with."""
    return {
        item_key(entry)
        for entry in history
        if _status_key(entry.get("status")) in _SEEN_STATUSES
    }


def _weighted_components(
    candidate: dict,
    affinity: float,
    weights: Weights,
) -> list[tuple[float, float]]:
    """Return the available ``(weight, value)`` pairs for a candidate.

    ``provider_relevance`` and ``quality`` are optional; when one is missing its
    weight is redistributed across the others by :func:`_combine`.
    """
    components = [(weights.beta, affinity)]
    if candidate.get("provider_relevance") is not None:
        components.append((weights.alpha, _clamp_unit(candidate["provider_relevance"])))
    if candidate.get("quality") is not None:
        components.append((weights.gamma, _clamp_unit(candidate["quality"])))
    return components


def _combine(
    components: list[tuple[float, float]],
    penalty: float,
    delta: float,
) -> float:
    """Combine weighted components and subtract the candidate penalty."""
    total_weight = sum(weight for weight, _ in components)
    if total_weight <= 0.0:
        base = 0.0
    else:
        base = sum(weight * value for weight, value in components) / total_weight
    return base - delta * penalty


def _top_genre(candidate: dict, affinity: dict[str, float]) -> str | None:
    """Return the candidate's strongest positively-rated genre, if any."""
    genres = [genre for genre in candidate.get("genres") or () if genre in affinity]
    if not genres:
        return None
    best = max(genres, key=lambda genre: affinity[genre])
    return best if affinity[best] > 0.0 else None


def rank_candidates(
    candidates: list[dict],
    history: list[dict],
    media_type: str | None = None,
    *,
    limit: int | None = None,
    weights: Weights | None = None,
    excluded: object = (),
) -> list[dict]:
    """Rank candidate titles for one user by predicted taste affinity.

    Already-seen titles, explicit ``excluded`` identities and episodes are
    filtered out before scoring.  Remaining candidates are scored with
    ``alpha*provider + beta*affinity + gamma*quality - delta*penalty`` and
    sorted descending, breaking ties on title for deterministic output.  Each
    result is a copy of the candidate with ``score``, ``affinity`` and
    ``top_genre`` added for the UI to explain.
    """
    weights = weights or Weights()
    affinity = build_affinity(history, media_type=media_type, k=weights.k)
    blocked = seen_keys(history) | set(excluded)
    ranked = []
    for candidate in candidates:
        if not _matches_media_type(candidate, media_type):
            continue
        if str(candidate.get("media_type") or "").strip().lower() == "episode":
            continue
        if item_key(candidate) in blocked:
            continue
        candidate_affinity = genre_affinity(candidate.get("genres"), affinity)
        components = _weighted_components(candidate, candidate_affinity, weights)
        penalty = float(candidate.get("penalty") or 0.0)
        ranked.append(
            {
                **candidate,
                "score": _combine(components, penalty, weights.delta),
                "affinity": candidate_affinity,
                "top_genre": _top_genre(candidate, affinity),
            },
        )
    ranked.sort(key=lambda item: (-item["score"], _normalise_title(item.get("title"))))
    if limit is not None:
        return ranked[:limit]
    return ranked
