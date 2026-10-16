import json

from django.test import TestCase, override_settings


class PwaTests(TestCase):
    """The app must be installable and degrade to an offline page."""

    def test_manifest_is_public_and_installable(self):
        """Anonymous clients (the browser install check) can read the manifest."""
        response = self.client.get("/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        manifest = json.loads(response.content)
        self.assertEqual(manifest["short_name"], "Magpie")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/")
        sizes = {icon["sizes"] for icon in manifest["icons"]}
        self.assertEqual(sizes, {"192x192", "512x512"})
        self.assertEqual(
            manifest["icons"][0]["src"], "/static/favicon/android-chrome-192x192.png"
        )

    def test_service_worker_is_public_and_scoped_to_the_root(self):
        """The worker must load without login and may control the whole site."""
        response = self.client.get("/serviceworker.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertEqual(response["Cache-Control"], "no-cache")

    def test_offline_page_is_public(self):
        """The worker precaches this page, so it cannot redirect to login."""
        response = self.client.get("/offline/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You're offline")

    def test_login_page_links_manifest_and_registers_the_worker(self):
        """Every page advertises the manifest and registers the worker."""
        response = self.client.get("/accounts/login/")
        self.assertContains(response, 'rel="manifest" href="/manifest.webmanifest"')
        self.assertContains(response, "register('/serviceworker.js")
