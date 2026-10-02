import logging
from pathlib import Path
from urllib.parse import urlencode

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required, login_required
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Prefetch, prefetch_related_objects
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app import config, helpers, history_processor
from app import discards as discard_service
from app import home as home_helpers
from app import recommendations as recommendation_engine
from app import statistics as stats
from app.forms import EpisodeForm, ManualItemForm, get_form_class
from app.models import (
    TV,
    BasicMedia,
    Episode,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
    UserMessage,
)
from app.providers import manual, services, tmdb
from app.templatetags import app_tags
from events.models import Event
from lists.models import CustomList
from lists.views import get_or_create_item
from users.models import (
    DateFormatChoices,
    HomeSortChoices,
    MediaSortChoices,
    MediaStatusChoices,
    User,
)

logger = logging.getLogger(__name__)


@require_GET
def home(request):
    """Home page with media items in progress and planning."""
    sort_by = request.user.update_preference("home_sort", request.GET.get("sort"))
    media_type_to_load = request.GET.get("load_media_type")
    section_to_load = request.GET.get("load_status", Status.IN_PROGRESS.value)
    hide_unreleased_param = request.GET.get("hide_unreleased")
    hide_unreleased = request.user.update_preference(
        "home_hide_unreleased",
        None if hide_unreleased_param is None else hide_unreleased_param == "true",
    )
    items_limit = 14

    # If this is an HTMX request to load more items for a specific media type
    if request.headers.get("HX-Request") and media_type_to_load:
        list_by_type = home_helpers.get_home_media_types(
            request,
            sort_by,
            section_to_load,
            items_limit,
            media_type_to_load,
            hide_unreleased=hide_unreleased,
        )
        return render(
            request,
            "app/components/home_grid.html",
            {
                "media_list": list_by_type.get(
                    media_type_to_load,
                    {"items": [], "total": 0},
                ),
                "home_status": section_to_load,
            },
        )

    home_sections = [
        home_helpers.build_home_section(
            section_key,
            home_helpers.get_home_media_types(
                request,
                sort_by,
                section_key,
                items_limit,
                hide_unreleased=hide_unreleased,
            ),
        )
        for section_key in (Status.IN_PROGRESS.value, Status.PLANNING.value)
    ]

    context = {
        "home_sections": home_sections,
        "current_sort": sort_by,
        "sort_choices": HomeSortChoices.choices,
        "hide_unreleased": hide_unreleased,
        "items_limit": items_limit,
    }
    return render(request, "app/home.html", context)


@require_POST
def progress_edit(request, media_type, instance_id):
    """Increase or decrease the progress of a media item from home page."""
    operation = request.POST["operation"]
    hide_unreleased = request.user.home_hide_unreleased
    home_status = request.POST.get("home_status")

    media = helpers.get_owned_media_or_404(
        request, media_type, instance_id, prefetch=True
    )

    if operation == "increase":
        media.increase_progress()
    elif operation == "decrease":
        media.decrease_progress()

    if media_type == MediaTypes.SEASON.value:
        # clear prefetch cache to get the updated episodes
        media.refresh_from_db()
        prefetch_related_objects(
            [media],
            Prefetch(
                "episodes",
                queryset=Episode.objects.select_related("item"),
            ),
            Prefetch(
                "item__event_set",
                queryset=Event.objects.all(),
                to_attr="prefetched_events",
            ),
        )

    if hide_unreleased and home_status == Status.IN_PROGRESS.value:
        if media_type == MediaTypes.SEASON.value:
            BasicMedia.objects.annotate_max_progress([media], media_type)
        BasicMedia.objects._annotate_next_event([media])

        if not home_helpers.is_active_in_progress_media(media):
            response = HttpResponse()
            response["HX-Retarget"] = f"#home-media-{media.item.media_type}-{media.id}"
            response["HX-Reswap"] = "delete"
            return response

    context = {
        "media": media,
        "home_status": home_status,
    }
    return render(
        request,
        "app/components/progress_changer.html",
        context,
    )


#: About six months: enough to read a rhythm without scrolling on a phone.
PROFILE_HEATMAP_WEEKS = 26


def _is_app_route(path):
    """Return whether ``path`` resolves to a real route, not the profile alias."""
    try:
        match = resolve(path)
    except Resolver404:
        return False
    return match.url_name != "profile_slash"


def _split_opinions(entries, *, show_comments, is_owner, opinions=4, others=8):
    """Split activity into entries with a visible score or comment, and the rest."""

    def has_opinion(entry):
        media = entry["media"]
        visible_note = bool(media.notes) and (
            is_owner or (show_comments and media.notes_public)
        )
        return media.score is not None or visible_note

    with_opinion = [entry for entry in entries if has_opinion(entry)]
    without = [entry for entry in entries if not has_opinion(entry)]
    return with_opinion[:opinions], without[:others]


@login_not_required
@require_GET
def profile(request, username):
    """Public profile: who someone is and what they have been consuming lately.

    One page for every media type: avatar, bio and social links, the latest
    finished items with their score and comment, the consumption heatmap, the
    featured shelves, and links into each media type's full list. Sections
    follow the same visibility rules as the per-type lists.
    """
    target_user = User.objects.filter(username=username).first()
    if target_user is None:
        # Let APPEND_SLASH still redirect e.g. /health to /health/.
        if settings.APPEND_SLASH and _is_app_route(f"{request.path}/"):
            return redirect(f"{request.path}/")
        msg = "User not found"
        raise Http404(msg)
    # ?as=visitor lets the owner preview exactly what the public sees.
    preview = request.user == target_user and request.GET.get("as") == "visitor"
    viewer = AnonymousUser() if preview else request.user
    is_owner = viewer == target_user
    if not is_owner and target_user.profile_private and not preview:
        msg = "User not found"
        raise Http404(msg)

    show_reviews = target_user.profile_section_visible(viewer, "profile_show_reviews")
    show_shelves = target_user.profile_section_visible(viewer, "profile_show_shelves")
    show_heatmap = target_user.profile_section_visible(viewer, "profile_show_heatmap")

    heatmap = (
        stats.get_media_heatmap(target_user, weeks=PROFILE_HEATMAP_WEEKS)
        if show_heatmap
        else None
    )

    recent_activity = stats.get_recent_activity(target_user, limit=12)
    latest_opinions, latest_others = _split_opinions(
        recent_activity, show_comments=show_reviews, is_owner=is_owner
    )

    context = {
        "target_user": target_user,
        "is_owner": is_owner,
        "public_view": not request.user.is_authenticated or preview,
        "preview": preview,
        "recent_activity": recent_activity,
        "latest_opinions": latest_opinions,
        "latest_others": latest_others,
        "show_comments": show_reviews,
        "library_counts": stats.get_library_counts(target_user),
        "featured_shelves": (
            CustomList.objects.get_featured_shelves(target_user)
            if show_shelves
            else CustomList.objects.none()
        ),
        "heatmap": heatmap,
        "heatmap_username": target_user.username,
        "now_year": timezone.localdate().year,
    }
    return render(request, "app/profile.html", context)


@login_not_required
@require_GET
def profile_timeline(request, username):
    """Everything someone watched, played or read, day by day, newest first."""
    target_user = get_object_or_404(User, username=username)
    preview = request.user == target_user and request.GET.get("as") == "visitor"
    viewer = AnonymousUser() if preview else request.user
    is_owner = viewer == target_user
    if not is_owner and target_user.profile_private and not preview:
        msg = "User not found"
        raise Http404(msg)

    before = parse_date(request.GET.get("before", "") or "")
    day_groups, next_before = stats.get_profile_timeline(target_user, before=before)
    context = {
        "target_user": target_user,
        "is_owner": is_owner,
        "public_view": not request.user.is_authenticated or preview,
        "preview": preview,
        "show_comments": target_user.profile_section_visible(
            viewer, "profile_show_reviews"
        ),
        "day_groups": day_groups,
        "next_before": next_before,
        "is_first_page": before is None,
    }
    return render(request, "app/profile_timeline.html", context)


@login_not_required
@require_GET
def profile_media(request, username, media_type, source, media_id):
    """One title as seen on someone's profile: their verdict comes first.

    Shows the title's poster, synopsis, genres, provider score and cast, but
    the protagonist is this person's own record: score, status, when they
    finished it and their comment. No tracking controls or recommendations.
    Same visibility rules as the profile; a private note is only shown to
    its owner.
    """
    target_user = get_object_or_404(User, username=username)
    preview = request.user == target_user and request.GET.get("as") == "visitor"
    viewer = AnonymousUser() if preview else request.user
    is_owner = viewer == target_user
    if not is_owner and target_user.profile_private and not preview:
        msg = "User not found"
        raise Http404(msg)
    if media_type in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value):
        msg = "Not found"
        raise Http404(msg)

    record = BasicMedia.objects.filter_media_prefetch(
        target_user, media_id, media_type, source
    ).first()
    on_shelf = CustomList.objects.get_featured_shelves(target_user).filter(
        items__media_type=media_type,
        items__source=source,
        items__media_id=media_id,
    )
    if record is None and not on_shelf.exists():
        msg = "Not in this library"
        raise Http404(msg)

    metadata = services.get_media_metadata(media_type, media_id, source)
    show_comment = (
        record is not None
        and bool(record.notes)
        and (
            is_owner
            or (
                record.notes_public
                and target_user.profile_section_visible(viewer, "profile_show_reviews")
            )
        )
    )
    finished_at = getattr(record, "end_date", None)
    if record is not None and finished_at is None and media_type == MediaTypes.TV.value:
        last_episode = (
            Episode.objects.filter(
                related_season__related_tv=record,
                end_date__isnull=False,
            )
            .order_by("-end_date")
            .first()
        )
        finished_at = last_episode.end_date if last_episode else None

    context = {
        "target_user": target_user,
        "is_owner": is_owner,
        "public_view": not request.user.is_authenticated or preview,
        "preview": preview,
        "media": metadata,
        "record": record,
        "finished_at": finished_at,
        "show_comment": show_comment,
        "media_type": media_type,
    }
    return render(request, "app/profile_media.html", context)


@login_not_required
@require_GET
def media_list(request, username, media_type):
    """Return the media list page."""
    target_user = get_object_or_404(User, username=username)

    # if user is looking at own page then update preferences
    if request.user == target_user:
        layout = target_user.update_preference(
            f"{media_type}_layout",
            request.GET.get("layout"),
        )
        sort_filter = target_user.update_preference(
            f"{media_type}_sort",
            request.GET.get("sort"),
        )
        status_filter = target_user.update_preference(
            f"{media_type}_status",
            request.GET.get("status"),
        )
    else:
        # privacy check then media type check
        if target_user.profile_private:
            msg = "User not found"
            raise Http404(msg)

        enabled_media_types = target_user.get_enabled_media_types()
        if not enabled_media_types:
            msg = "User doesn't have any media types enabled"
            raise Http404(msg)

        if media_type not in enabled_media_types:
            return redirect(
                "medialist",
                username=target_user.username,
                media_type=enabled_media_types[0],
            )

        layout = "grid"
        sort_filter = target_user.get_valid_preference(
            f"{media_type}_sort",
            request.GET.get("sort"),
        )
        status_filter = target_user.get_valid_preference(
            f"{media_type}_status",
            request.GET.get("status"),
        )

    search_query = request.GET.get("search", "")
    page = request.GET.get("page", 1)

    # Prepare status filter for database query
    if not status_filter:
        status_filter = MediaStatusChoices.ALL

    # Get media list with filters applied
    media_queryset = BasicMedia.objects.get_media_list(
        user=target_user,
        media_type=media_type,
        status_filter=status_filter,
        sort_filter=sort_filter,
        search=search_query,
    )

    # Paginate results
    items_per_page = 32
    paginator = Paginator(media_queryset, items_per_page)
    media_page = paginator.get_page(page)

    BasicMedia.objects.annotate_max_progress(
        media_page.object_list,
        media_type,
    )

    context = {
        "media_type": media_type,
        "media_type_plural": app_tags.media_type_readable_plural(media_type).lower(),
        "media_list": media_page,
        "current_layout": layout,
        "layout_class": ".media-grid" if layout == "grid" else "tbody",
        "current_sort": sort_filter,
        "current_status": status_filter,
        "sort_choices": MediaSortChoices.choices,
        "status_choices": MediaStatusChoices.choices,
        "target_user": target_user,
        # Visitors see the person's verdicts as posters, not management cards.
        "public_grid": request.user != target_user,
        "show_comments": target_user.profile_section_visible(
            request.user, "profile_show_reviews"
        ),
        "public_view": not request.user.is_authenticated,
    }

    # Handle HTMX requests for partial updates. Soft-navigation requests (e.g.
    # after saving from an edit modal) need the full page for the body swap.
    if request.headers.get("HX-Request") and not request.headers.get(
        "X-Soft-Navigation"
    ):
        # Filtering from empty list
        if request.headers.get("HX-Target") == "empty_list":
            # If still empty, keep user in the same page
            if not media_page.object_list:
                return HttpResponse(status=204)
            response = HttpResponse()
            response["HX-Redirect"] = reverse(
                "medialist", args=[target_user.username, media_type]
            )
            return response
        if layout == "grid":
            template_name = "app/components/media_grid_items.html"
        else:
            template_name = "app/components/media_table_items.html"
    else:
        template_name = "app/media_list.html"

    return render(request, template_name, context)


@require_GET
def media_search(request):
    """Return the media search page."""
    media_type = request.user.update_preference(
        "last_search_type",
        request.GET["media_type"],
    )
    query = request.GET["q"]
    page = int(request.GET.get("page", 1))
    layout = request.GET.get("layout", "grid")

    # only receives source when searching with secondary source
    source = request.GET.get(
        "source",
        config.get_default_source_name(media_type).value,
    )

    data = services.search(media_type, query, page, source)

    # Enrich search results with user tracking data
    if data.get("results"):
        data["results"] = helpers.enrich_items_with_user_data(
            request, data["results"], "search"
        )

    context = {
        "data": data,
        "source": source,
        "media_type": media_type,
        "layout": layout,
    }

    return render(request, "app/search.html", context)


@require_GET
def media_details(request, source, media_type, media_id, title):  # noqa: ARG001 title for URL
    """Return the details page for a media item."""
    media_metadata = services.get_media_metadata(media_type, media_id, source)
    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        media_type,
        source,
    )
    current_instance = user_medias[0] if user_medias else None

    if current_instance is not None:
        helpers.refresh_item_image_if_missing(
            current_instance.item, media_metadata.get("image")
        )

    # Enrich related items with user tracking data
    if media_metadata.get("related"):
        for section_name, related_items in media_metadata["related"].items():
            if related_items:
                media_metadata["related"][section_name] = (
                    helpers.enrich_items_with_user_data(
                        request, related_items, section_name
                    )
                )

    if media_type in ["tv", "movie"]:
        watch_providers = tmdb.filter_providers(
            media_metadata.get("providers"), request.user.watch_provider_region
        )
    else:
        watch_providers = None

    context = {
        "media": media_metadata,
        "media_type": media_type,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "watch_providers": watch_providers,
        "watch_provider_region": request.user.watch_provider_region,
    }
    return render(request, "app/media_details.html", context)


@require_GET
def season_details(request, source, media_id, title, season_number):  # noqa: ARG001 For URL
    """Return the details page for a season."""
    tv_with_seasons_metadata = services.get_media_metadata(
        "tv_with_seasons",
        media_id,
        source,
        [season_number],
    )
    season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        MediaTypes.SEASON.value,
        source,
        season_number=season_number,
    )

    current_instance = user_medias[0] if user_medias else None
    episodes_in_db = current_instance.episodes.all() if current_instance else []

    if current_instance is not None:
        helpers.refresh_item_image_if_missing(
            current_instance.item, season_metadata.get("image")
        )

    if source == Sources.MANUAL.value:
        season_metadata["episodes"] = manual.process_episodes(
            season_metadata,
            episodes_in_db,
        )
    else:
        season_metadata["episodes"] = tmdb.process_episodes(
            season_metadata,
            episodes_in_db,
        )

    # Enrich related items with user tracking data
    if season_metadata.get("related"):
        for section_name, related_items in season_metadata["related"].items():
            if related_items:
                season_metadata["related"][section_name] = (
                    helpers.enrich_items_with_user_data(
                        request,
                        related_items,
                        section_name,
                    )
                )

    context = {
        "media": season_metadata,
        "tv": tv_with_seasons_metadata,
        "media_type": MediaTypes.SEASON.value,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "watch_providers": tmdb.filter_providers(
            season_metadata.get("providers"), request.user.watch_provider_region
        ),
        "watch_provider_region": request.user.watch_provider_region,
    }
    return render(request, "app/media_details.html", context)


@require_POST
def update_media_score(request, media_type, instance_id):
    """Update the user's score for a media item."""
    media = helpers.get_owned_media_or_404(request, media_type, instance_id)

    score = float(request.POST.get("score"))
    media.score = score
    media.save()
    logger.info(
        "%s score updated to %s",
        media,
        score,
    )

    return JsonResponse(
        {
            "success": True,
            "score": score,
        },
    )


@require_POST
def sync_metadata(request, source, media_type, media_id, season_number=None):
    """Refresh the metadata for a media item."""
    if source == Sources.MANUAL.value:
        msg = "Manual items cannot be synced."
        messages.error(request, msg)
        return HttpResponse(
            msg,
            status=400,
            headers={"HX-Redirect": request.POST.get("next", "/")},
        )

    cache_key = f"{source}_{media_type}_{media_id}"
    if media_type == MediaTypes.SEASON.value:
        cache_key += f"_{season_number}"

    ttl = cache.ttl(cache_key)
    logger.debug("%s - Cache TTL for: %s", cache_key, ttl)

    if ttl is not None and ttl > (settings.CACHE_TIMEOUT - 3):
        msg = "The data was recently synced, please wait a few seconds."
        messages.error(request, msg)
        logger.error(msg)
    else:
        deleted = cache.delete(cache_key)
        logger.debug("%s - Old cache deleted: %s", cache_key, deleted)

        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        item, _ = Item.objects.update_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        title = metadata["title"]
        if season_number:
            title += f" - Season {season_number}"

        if media_type == MediaTypes.SEASON.value:
            metadata["episodes"] = tmdb.process_episodes(
                metadata,
                [],
            )

            # Create a dictionary of existing episodes keyed by episode number
            existing_episodes = {
                ep.episode_number: ep
                for ep in Item.objects.filter(
                    source=source,
                    media_type=MediaTypes.EPISODE.value,
                    media_id=media_id,
                    season_number=season_number,
                )
            }

            episodes_to_update = []
            episode_count = 0

            for episode_data in metadata["episodes"]:
                episode_number = episode_data["episode_number"]
                if episode_number in existing_episodes:
                    episode_item = existing_episodes[episode_number]
                    episode_item.title = metadata["title"]
                    episode_item.image = episode_data["image"]
                    episodes_to_update.append(episode_item)
                    episode_count += 1

            logger.info(
                "Found %s existing episodes to update for %s",
                episode_count,
                title,
            )

            if episodes_to_update:
                updated_count = Item.objects.bulk_update(
                    episodes_to_update,
                    ["title", "image"],
                    batch_size=100,
                )
                logger.info(
                    "Successfully updated %s episodes for %s",
                    updated_count,
                    title,
                )

        item.fetch_releases(delay=False)

        msg = f"{title} was synced to {Sources(source).label} successfully."
        messages.success(request, msg)

    if request.headers.get("HX-Request"):
        return HttpResponse(
            status=204,
            headers={
                "HX-Redirect": request.POST["next"],
            },
        )
    return helpers.redirect_back(request)


@require_GET
def track_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
):
    """Return the tracking form for a media item."""
    instance_id = request.GET.get("instance_id")
    if instance_id:
        media = BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
    elif request.GET.get("is_create"):
        media = None
    else:
        # no specific instance, try to find the first one
        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            media_type,
            source,
            season_number=season_number,
        )
        media = user_medias.first()
        if media:
            instance_id = media.id

    initial_data = {
        "media_id": media_id,
        "source": source,
        "media_type": media_type,
        "season_number": season_number,
        "instance_id": instance_id,
    }

    if media:
        title = media.item
        if media_type == MediaTypes.GAME.value:
            initial_data["progress"] = helpers.minutes_to_hhmm(media.progress)
    else:
        title = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )["title"]
        if media_type == MediaTypes.SEASON.value:
            title += f" S{season_number}"

    form = get_form_class(media_type)(instance=media, initial=initial_data)

    return render(
        request,
        "app/components/fill_track.html",
        {
            "title": title,
            "form": form,
            "media": media,
            "return_url": request.GET["return_url"],
        },
    )


@require_POST
def media_save(request):
    """Save or update media data to the database."""
    media_id = request.POST["media_id"]
    source = request.POST["source"]
    media_type = request.POST["media_type"]
    season_number = request.POST.get("season_number")
    instance_id = request.POST.get("instance_id")

    if instance_id:
        instance = helpers.get_owned_media_or_404(request, media_type, instance_id)
        item = instance.item
        if not item.genres.exists():
            metadata = services.get_media_metadata(
                item.media_type,
                item.media_id,
                item.source,
                [item.season_number],
                item.episode_number,
            )
            item.set_genres(metadata.get("genres"))
    else:
        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        item.set_genres(metadata.get("genres"))
        model = apps.get_model(app_label="app", model_name=media_type)
        instance = model(item=item, user=request.user)

    # Validate the form and save the instance if it's valid
    form_class = get_form_class(media_type)
    form = form_class(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        logger.info("%s saved successfully.", form.instance)
    else:
        logger.error(form.errors.as_json())
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(
                    request,
                    f"{field.replace('_', ' ').title()}: {error}",
                )

    return helpers.redirect_back(request)


@require_POST
def media_delete(request):
    """Delete media data from the database."""
    instance_id = request.POST["instance_id"]
    media_type = request.POST["media_type"]
    media = helpers.get_owned_media_or_404(request, media_type, instance_id)
    media.delete()
    logger.info("%s deleted successfully.", media)

    return helpers.redirect_back(request)


@require_POST
def mark_user_messages_shown(request):
    """Mark all unseen persistent messages for the user as shown."""
    message_ids = [
        int(message_id)
        for message_id in request.POST.getlist("message_ids")
        if message_id.isdigit()
    ]
    if not message_ids:
        return HttpResponse(status=204)

    UserMessage.objects.filter(
        id__in=message_ids,
        user=request.user,
        shown_at__isnull=True,
    ).update(shown_at=timezone.now())
    return HttpResponse(status=204)


@require_POST
def episode_save(request):
    """Handle the creation, deletion, and updating of episodes for a season."""
    media_id = request.POST["media_id"]
    season_number = int(request.POST["season_number"])
    episode_number = int(request.POST["episode_number"])
    source = request.POST["source"]

    form = EpisodeForm(request.POST)
    if not form.is_valid():
        logger.error("Form validation failed: %s", form.errors)
        return HttpResponseBadRequest("Invalid form data")

    try:
        related_season = Season.objects.get(
            item__media_id=media_id,
            item__source=source,
            item__season_number=season_number,
            item__episode_number=None,
            user=request.user,
        )
    except Season.DoesNotExist:
        tv_with_seasons_metadata = services.get_media_metadata(
            "tv_with_seasons",
            media_id,
            source,
            [season_number],
        )
        season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": tv_with_seasons_metadata["title"],
                "image": season_metadata["image"],
            },
        )
        related_season = Season.objects.create(
            item=item,
            user=request.user,
            score=None,
            status=Status.IN_PROGRESS.value,
            notes="",
        )

        logger.info("%s did not exist, it was created successfully.", related_season)

    related_season.watch(episode_number, form.cleaned_data["end_date"])

    return helpers.redirect_back(request)


@require_http_methods(["GET", "POST"])
def create_entry(request):
    """Return the form for manually adding media items."""
    if request.method == "GET":
        media_types = MediaTypes.values
        return render(request, "app/create_entry.html", {"media_types": media_types})

    # Process the form submission
    form = ManualItemForm(request.POST, user=request.user)
    if not form.is_valid():
        # Handle form validation errors
        logger.error(form.errors.as_json())
        helpers.form_error_messages(form, request)
        return redirect("create_entry")

    # Try to save the item
    try:
        item = form.save()
    except IntegrityError:
        # Handle duplicate item
        media_name = form.cleaned_data["title"]
        if form.cleaned_data.get("season_number"):
            media_name += f" - Season {form.cleaned_data['season_number']}"
        if form.cleaned_data.get("episode_number"):
            media_name += f" - Episode {form.cleaned_data['episode_number']}"

        logger.exception("%s already exists in the database.", media_name)
        messages.error(request, f"{media_name} already exists in the database.")
        return redirect("create_entry")

    # Prepare and validate the media form
    updated_request = request.POST.copy()
    updated_request.update({"source": item.source, "media_id": item.media_id})
    media_form = get_form_class(item.media_type)(updated_request)

    if not media_form.is_valid():
        # Handle media form validation errors
        logger.error(media_form.errors.as_json())
        helpers.form_error_messages(media_form, request)

        # Delete the item since the media creation failed
        item.delete()
        logger.info("%s was deleted due to media form validation failure", item)
        return redirect("create_entry")

    # Save the media instance
    media_form.instance.user = request.user
    media_form.instance.item = item

    # Handle relationships based on media type
    if item.media_type == MediaTypes.SEASON.value:
        media_form.instance.related_tv = form.cleaned_data["parent_tv"]
    elif item.media_type == MediaTypes.EPISODE.value:
        media_form.instance.related_season = form.cleaned_data["parent_season"]

    media_form.save()

    # Success message
    msg = f"{item} added successfully."
    messages.success(request, msg)
    logger.info(msg)

    return redirect("create_entry")


@require_GET
def search_parent_tv(request):
    """Return the search results for parent TV shows."""
    query = request.GET.get("q", "").strip()

    if len(query) <= 1:
        return render(request, "app/components/search_parent_tv.html")

    logger.debug(
        "%s - Searching for TV shows with query: %s",
        request.user.username,
        query,
    )

    parent_tvs = TV.objects.filter(
        user=request.user,
        item__source=Sources.MANUAL.value,
        item__media_type=MediaTypes.TV.value,
        item__title__icontains=query,
    )[:5]

    return render(
        request,
        "app/components/search_parent_tv.html",
        {"results": parent_tvs, "query": query},
    )


@require_GET
def search_parent_season(request):
    """Return the search results for parent seasons."""
    query = request.GET.get("q", "").strip()

    if len(query) <= 1:
        return render(request, "app/components/search_parent_tv.html")

    logger.debug(
        "%s - Searching for seasons with query: %s",
        request.user.username,
        query,
    )

    parent_seasons = Season.objects.filter(
        user=request.user,
        item__source=Sources.MANUAL.value,
        item__media_type=MediaTypes.SEASON.value,
        item__title__icontains=query,
    )[:5]

    return render(
        request,
        "app/components/search_parent_season.html",
        {"results": parent_seasons, "query": query},
    )


@require_GET
def history_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """Return the history page for a media item."""
    user_medias = BasicMedia.objects.filter_media(
        request.user,
        media_id,
        media_type,
        source,
        season_number=season_number,
        episode_number=episode_number,
    )

    total_medias = user_medias.count()
    timeline_entries = []
    for index, media in enumerate(user_medias, start=1):
        if history := media.history.all():
            media_entry_number = total_medias - index + 1
            timeline_entries.extend(
                history_processor.process_history_entries(
                    history,
                    media_type,
                    media_entry_number,
                    request.user,
                ),
            )
    return render(
        request,
        "app/components/fill_history.html",
        {
            "media_type": media_type,
            "timeline": timeline_entries,
            "total_medias": total_medias,
            "return_url": request.GET["return_url"],
        },
    )


@require_http_methods(["DELETE"])
def delete_history_record(request, media_type, history_id):
    """Delete a specific history record."""
    try:
        historical_model = apps.get_model(
            app_label="app",
            model_name=f"historical{media_type.lower()}",
        )

        historical_model.objects.get(
            history_id=history_id,
            history_user=request.user,
        ).delete()

        logger.info(
            "Deleted history record %s",
            str(history_id),
        )

        # Return empty 200 response - the element will be removed by HTMX
        return HttpResponse()

    except historical_model.DoesNotExist:
        logger.exception(
            "History record %s not found for user %s",
            str(history_id),
            str(request.user),
        )
        return HttpResponse("Record not found", status=404)


@require_GET
def statistics(request):
    """Return the statistics page."""
    start_date, end_date = stats.parse_activity_date_range(request)

    # Get all user media data in a single operation
    user_media, media_count = stats.get_user_media(
        request.user,
        start_date,
        end_date,
    )

    # Calculate all statistics from the retrieved data
    media_type_distribution = stats.get_media_type_distribution(
        media_count,
    )
    score_distribution, top_rated = stats.get_score_distribution(user_media)
    status_distribution = stats.get_status_distribution(user_media)
    status_pie_chart_data = stats.get_status_pie_chart_data(
        status_distribution,
    )
    consumption_stats = stats.get_consumption_stats(user_media, media_count)

    total = media_count["total"]
    in_progress_count = stats.get_status_total(
        status_distribution,
        Status.IN_PROGRESS.value,
    )
    rated_percent = (
        round(score_distribution["total_scored"] / total * 100) if total else None
    )

    try:
        heatmap_year = int(request.GET.get("heatmap-year"))
    except (TypeError, ValueError):
        heatmap_year = None

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "heatmap": stats.get_media_heatmap(request.user, heatmap_year),
        "heatmap_username": request.user.username,
        "now_year": timezone.localdate().year,
        "media_count": media_count,
        "media_type_distribution": media_type_distribution,
        "score_distribution": score_distribution,
        "top_rated": top_rated,
        "status_distribution": status_distribution,
        "status_pie_chart_data": status_pie_chart_data,
        "consumption_stats": consumption_stats,
        "in_progress_count": in_progress_count,
        "rated_percent": rated_percent,
        "date_format_values": DateFormatChoices.values,
    }

    return render(request, "app/statistics.html", context)


@login_not_required
@require_GET
def heatmap_day_detail(request):
    """Return the detail panel for a single heatmap day (progress/completions).

    Works both for a user's own statistics page and for the heatmap shown on
    someone else's public profile. The owner always gets their own detail;
    anyone else only gets it when the profile is public and the heatmap
    section hasn't been individually hidden (mirrors the check the profile
    page itself uses to decide whether to render the heatmap at all).
    """
    day = parse_date(request.GET.get("date", ""))
    if day is None:
        return HttpResponse(status=400)

    username = request.GET.get("username")
    if username:
        target_user = get_object_or_404(User, username=username)
        if not target_user.profile_section_visible(
            request.user,
            "profile_show_heatmap",
        ):
            msg = "User not found"
            raise Http404(msg)
    elif request.user.is_authenticated:
        target_user = request.user
    else:
        return HttpResponse(status=400)

    context = {
        "day": day,
        "entries": stats.get_day_detail(target_user, day),
    }
    return render(request, "app/components/heatmap_day_detail.html", context)


@require_GET
def journal(request):
    """Return the journal page: a global feed of the user's tracking activity."""
    start_date, end_date = stats.parse_activity_date_range(request)

    items_per_page = 20
    # Keyset pagination: the cursor points just past the previous page's last
    # row, so each request reads at most one page per media type regardless of
    # scroll depth (never re-scanning everything above the current page).
    cursor = history_processor.parse_journal_cursor(request)
    page_rows, has_next = history_processor.get_journal_page(
        request.user,
        start_date,
        end_date,
        limit=items_per_page,
        cursor=cursor,
    )
    entries = history_processor.build_journal_entries(page_rows, request.user)
    journal_days = history_processor.build_journal_days(entries, request.user)

    # Preserve the active date range when the feed paginates via HTMX.
    date_params = {
        key: request.GET[key]
        for key in ("start-date", "end-date")
        if key in request.GET
    }

    # Cursor for the next page: the last row rendered on this one.
    next_params = dict(date_params)
    if page_rows:
        last_date, last_type, last_id = page_rows[-1]
        next_params["cursor_date"] = last_date.isoformat()
        next_params["cursor_type"] = last_type
        next_params["cursor_id"] = last_id

    prev_day = request.GET.get("last_day", "")

    context = {
        "entries": entries,
        "journal_days": journal_days,
        # The previous page's last day, so a day split across pages isn't
        # relabelled; the last day on this page, forwarded to the next page.
        # Falls back to prev_day when this page rendered no days, so a day that
        # spans an all-filtered page isn't shown twice.
        "prev_day": prev_day,
        "last_day": journal_days[-1]["day_iso"] if journal_days else prev_day,
        "has_next": has_next,
        "next_query": urlencode(next_params),
        "filter_query": urlencode(date_params),
        "start_date": start_date,
        "end_date": end_date,
    }

    # The activity dashboard only appears on the full page, so skip its queries
    # on the HTMX partial requests that load additional feed pages. Soft
    # navigations (body swaps) still need the full page.
    if request.headers.get("HX-Request") and not request.headers.get(
        "X-Soft-Navigation"
    ):
        return render(request, "app/components/journal_items.html", context)

    context.update(
        {
            "activity_data": stats.get_activity_data(
                request.user,
                start_date,
                end_date,
            ),
            "activity_total": history_processor.get_journal_count(
                request.user,
                start_date,
                end_date,
            ),
            "date_format_values": DateFormatChoices.values,
        },
    )
    return render(request, "app/journal.html", context)


# --- Recommendation UI (E8.4) ----------------------------------------------

# Media types that can seed recommendations. Seasons and episodes are skipped:
# a season's suggestions duplicate its parent show's.
_RECOMMENDATION_MEDIA_TYPES = (
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
    MediaTypes.BOARDGAME.value,
)

# How many of the user's recent titles are asked for provider suggestions, and
# the maximum number of ranked suggestions rendered.
RECOMMENDATION_SOURCE_LIMIT = 8
RECOMMENDATION_LIMIT = 24


def _serialise_key(key):
    """Join an ``item_key`` tuple into a URL-safe ``|``-separated token."""
    return "|".join(key)


def _item_identity(item):
    """Return the ``item_key``-shaped dict for a persisted ``Item``."""
    return {
        "source": item.source,
        "media_id": item.media_id,
        "media_type": item.media_type,
        "season_number": item.season_number,
    }


def _discarded_keys(discards):
    """Return the ``item_key`` identities for an iterable of Discard-like rows."""
    return {
        recommendation_engine.item_key(_item_identity(discard.item))
        for discard in discards
    }


def _user_media(user):
    """Return every ``(media_type, media)`` pair for a user, newest first."""
    pairs = []
    for media_type in _RECOMMENDATION_MEDIA_TYPES:
        model = apps.get_model(app_label="app", model_name=media_type)
        queryset = model.objects.filter(user=user).select_related("item")
        pairs.extend((media_type, media) for media in queryset)
    pairs.sort(key=lambda pair: pair[1].created_at, reverse=True)
    return pairs


def _item_genre_names(item_or_dict):
    """Return an item's genre names, from a persisted Item or a plain dict."""
    if isinstance(item_or_dict, dict):
        return [str(g) for g in item_or_dict.get("genres") or () if g]
    return [str(genre) for genre in item_or_dict.genres.all()]


def _history_entry(media_type, media):
    """Build a scoring-engine history entry from a stored media row."""
    item = media.item
    return {
        "source": item.source,
        "media_id": item.media_id,
        "media_type": media_type,
        "title": item.title,
        "season_number": item.season_number,
        "score": float(media.score) if media.score is not None else None,
        "status": media.status,
        "genres": _item_genre_names(item),
    }


def _candidate_season_numbers(media_type, item):
    """Return the ``season_numbers`` argument for a metadata lookup, if any."""
    if media_type == MediaTypes.SEASON.value:
        return [item.season_number]
    return None


def _provider_candidates(pairs, limit):
    """Fetch provider "recommendations" seeded from the given history pairs."""
    candidates = {}
    for media_type, media in pairs[:limit]:
        item = media.item
        try:
            metadata = services.get_media_metadata(
                media_type,
                item.media_id,
                item.source,
                _candidate_season_numbers(media_type, item),
            )
        except Exception:  # a bad provider must not break the page
            logger.exception("Failed to load recommendation metadata for %s", item)
            continue
        related = metadata.get("related") or {}
        for candidate in related.get("recommendations") or []:
            candidates.setdefault(recommendation_engine.item_key(candidate), candidate)
    return candidates


# Below this many ranked results, the UI explains that candidates are scarce
# instead of silently showing a short list (acuerdo 35).
RECOMMENDATION_SCARCE_THRESHOLD = 6


def _mine_recommendations(request):
    """Render the individual ranking (default tab)."""
    pairs = _user_media(request.user)
    history = [_history_entry(media_type, media) for media_type, media in pairs]
    discarded = _discarded_keys(discard_service.discarded_items(request.user))
    candidates = _provider_candidates(pairs, RECOMMENDATION_SOURCE_LIMIT)

    ranked = recommendation_engine.rank_candidates(
        list(candidates.values()),
        history,
        limit=RECOMMENDATION_LIMIT,
        excluded=discarded,
    )
    return render(
        request,
        "app/recommendations.html",
        {"mode": "mine", "candidates": ranked},
    )


def _discarded_recommendations(request):
    """Render the "Discarded" tab: the user's own reversible discards."""
    return render(
        request,
        "app/recommendations.html",
        {
            "mode": "discarded",
            "discarded": discard_service.discarded_items(request.user),
        },
    )


def _group_recommendations(request):
    """Render the group ranking: least-misery, shared-affinity candidates."""
    from groups import discards as group_discard_service  # noqa: PLC0415

    groups = list(request.user.joined_groups.all())
    group_id = request.GET.get("group")
    group = (
        next((g for g in groups if str(g.id) == group_id), None) if group_id else None
    )
    if group is None and groups:
        group = groups[0]

    context = {"mode": "group", "groups": groups, "group": group, "candidates": []}
    if group is None:
        return render(request, "app/recommendations.html", context)

    allow_known = request.GET.get("allow_known") == "1"
    members = list(group.members.all())
    member_pairs = {member.id: _user_media(member) for member in members}
    member_dicts = [
        {
            "history": [
                _history_entry(media_type, media)
                for media_type, media in member_pairs[member.id]
            ],
        }
        for member in members
    ]

    per_member_limit = max(2, RECOMMENDATION_SOURCE_LIMIT // max(len(members), 1))
    candidates = {}
    for pairs in member_pairs.values():
        candidates.update(_provider_candidates(pairs, per_member_limit))

    own_library_ids = set(group.group_items.values_list("item_id", flat=True))
    own_library_keys = {
        recommendation_engine.item_key(_item_identity(item))
        for item in Item.objects.filter(id__in=own_library_ids)
    }
    excluded = own_library_keys | _discarded_keys(
        group_discard_service.group_discarded_items(group),
    )

    ranked = recommendation_engine.rank_group_candidates(
        list(candidates.values()),
        member_dicts,
        limit=RECOMMENDATION_LIMIT,
        excluded=excluded,
        allow_known=allow_known,
    )
    context.update(
        {
            "candidates": ranked,
            "allow_known": allow_known,
            "scarce": len(ranked) < RECOMMENDATION_SCARCE_THRESHOLD,
        },
    )
    return render(request, "app/recommendations.html", context)


@login_required
@require_GET
def recommendations(request):
    """Show ranked suggestions with one-click "to watch" and discard actions.

    ``?mode=mine`` (default) is the individual ranking, ``?mode=group`` the
    group ranking (``?group=<id>`` selects which of the user's groups) and
    ``?mode=discarded`` lists the user's own reversible discards.
    """
    mode = request.GET.get("mode") or "mine"
    if mode == "group":
        return _group_recommendations(request)
    if mode == "discarded":
        return _discarded_recommendations(request)
    return _mine_recommendations(request)


def _safe_next(request, default):
    """Return ``?next=`` if it is a same-site relative path, else ``default``."""
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and next_url.startswith("/"):
        return next_url
    return default


@login_required
@require_POST
def discard_item_view(request):
    """Mark an item as personally "not interesting" (reversible, E9)."""
    item = (
        get_object_or_404(Item, id=request.POST["item_id"])
        if request.POST.get("item_id")
        else get_or_create_item(
            request.POST.get("media_type"),
            request.POST.get("media_id"),
            request.POST.get("source"),
            int(request.POST["season_number"])
            if request.POST.get("season_number")
            else None,
        )
    )
    discard_service.discard_item(request.user, item)
    next_url = _safe_next(request, reverse("recommendations"))
    return redirect(
        f"{next_url}{'&' if '?' in next_url else '?'}"
        f"{urlencode({'discarded_item': item.id, 'discarded_title': item.title})}",
    )


@login_required
@require_POST
def restore_item_view(request, item_id):
    """Undo a personal discard."""
    item = get_object_or_404(Item, id=item_id)
    discard_service.restore_item(request.user, item)
    messages.success(request, f'"{item.title}" is back in your recommendations.')
    return redirect(_safe_next(request, f"{reverse('recommendations')}?mode=discarded"))


@require_GET
def service_worker():
    """Serve the service worker file."""
    sw_path = Path(settings.STATICFILES_DIRS[0]) / "js" / "serviceworker.js"
    with sw_path.open() as f:
        response = HttpResponse(f.read(), content_type="application/javascript")
        response["Service-Worker-Allowed"] = "/"
        return response
