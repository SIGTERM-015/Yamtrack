import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

REPO_ROOT = Path(settings.BASE_DIR).parent


class MediaServingConfig(SimpleTestCase):
    """Uploads must survive container recreation and be served without DEBUG."""

    def test_media_root_is_inside_the_persisted_db_volume(self):
        """docker-compose only persists /yamtrack/db, so uploads must live there."""
        self.assertEqual(
            Path(settings.MEDIA_ROOT), Path(settings.BASE_DIR) / "db" / "media"
        )

    def test_nginx_serves_media_from_media_root(self):
        """The image's nginx maps /media/ onto MEDIA_ROOT, with nosniff."""
        nginx = (REPO_ROOT / "nginx.conf").read_text()
        block = re.search(r"location /media/ \{(.*?)\}", nginx, re.DOTALL)
        self.assertIsNotNone(block)
        self.assertIn("alias /yamtrack/db/media/;", block.group(1))
        self.assertIn('X-Content-Type-Options "nosniff"', block.group(1))
