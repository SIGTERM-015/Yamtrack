"""Health checks shared by the HEALTHCHECK probe and the API health endpoint."""

from django.conf import settings
from redis.asyncio import Redis as RedisClient

# Mail, DNS and Storage are left out on purpose: the library enables them by
# default, but Magpie sends no mail unless SMTP is configured, so the Mail
# check would report the whole service as unavailable.
HEALTH_CHECKS = [
    "health_check.Cache",
    "health_check.Database",
    "health_check.contrib.celery.Ping",
    (
        "health_check.contrib.redis.Redis",
        {"client_factory": lambda: RedisClient.from_url(settings.REDIS_URL)},
    ),
]
