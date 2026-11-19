"""API messages follow the panel language (Accept-Language), and every message has a Persian entry."""

from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import translation
from rest_framework.test import APIClient

from config.i18n import tr


class TranslateTests(SimpleTestCase):
    def test_english_is_the_source_text(self):
        with translation.override("en"):
            self.assertEqual(tr("Invalid role"), "Invalid role")

    def test_persian_entry_and_placeholders(self):
        with translation.override("fa"):
            self.assertEqual(tr("Invalid key: {key}", key="1BAD"), "کلید نامعتبر: 1BAD")

    def test_missing_entry_falls_back_to_english(self):
        with translation.override("fa"):
            self.assertEqual(tr("Not in the dictionary {n}", n=3), "Not in the dictionary 3")

    def test_unknown_placeholder_is_left_visible(self):
        self.assertEqual(tr("Hello {name}"), "Hello {name}")

    def test_every_message_is_translated(self):
        out = StringIO()
        call_command("i18n_check", stdout=out)
        self.assertIn("all translated", out.getvalue())


class ResponseLanguageTests(TestCase):
    def setUp(self):
        call_command("seed")
        self.client = APIClient()

    def _login(self, **headers):
        return self.client.post("/api/auth/login", {"username": "admin", "password": "wrong"}, format="json", **headers)

    def test_default_language_is_english(self):
        res = self._login()
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["detail"], "Incorrect username or password")

    def test_persian_when_the_panel_asks_for_it(self):
        res = self._login(HTTP_ACCEPT_LANGUAGE="fa")
        self.assertEqual(res.data["detail"], "نام کاربری یا رمز اشتباه است")
        self.assertEqual(res["Content-Language"], "fa")

    def test_status_labels_are_translated(self):
        ok = self.client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {ok.data['access']}")
        self.client.post("/api/apps", {"name": "Shop", "slug": "shop"}, format="json")
        app = self.client.get("/api/apps/shop", HTTP_ACCEPT_LANGUAGE="fa").data
        self.assertEqual(app["region_label"], "همین سرور")
        self.assertNotEqual(app["status_label"], self.client.get("/api/apps/shop").data["status_label"])


class CliPageLanguageTests(TestCase):
    def test_browser_language_sets_text_and_direction(self):
        page = self.client.get("/cli?code=missing", HTTP_ACCEPT_LANGUAGE="fa-IR,fa;q=0.9,en;q=0.8")
        html = page.content.decode()
        self.assertIn('lang="fa" dir="rtl"', html)
        self.assertIn("این لینک منقضی شده است", html)

    def test_english_page_by_default(self):
        html = self.client.get("/cli?code=missing").content.decode()
        self.assertIn('lang="en" dir="ltr"', html)
        self.assertIn("This link has expired", html)
