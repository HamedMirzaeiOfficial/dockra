from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import revoke_sessions
from accounts.models import Membership, Organization
from databases.models import Database
from engine import dbadmin
from engine.models import Job
from engine.runner import close_idle_db_admins


@override_settings(API_DOMAIN="api.example.com", DOCKRA_TLS_RESOLVER="le")
class DbAdminTests(TestCase):
    def setUp(self):
        call_command("seed")
        self.client = APIClient()
        res = self.client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        made = self.client.post("/api/databases", {"name": "shopdb", "engine": "postgres"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.db = Database.objects.get(name="shopdb")

    def _open(self):
        res = self.client.post(f"/api/databases/{self.db.id}/admin", {}, format="json", HTTP_HOST="api.example.com", secure=True)
        self.assertIn(res.status_code, (200, 202), res.content)
        return res.data

    def test_open_login_and_forward_auth(self):
        data = self._open()
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["tool"], "pgAdmin")
        self.assertTrue(data["url"].startswith("https://api.example.com/api/dbadmin/open?t="))
        job = Job.objects.filter(kind=Job.Kind.DB_ADMIN).latest("id")
        self.assertEqual(job.payload["base"], "https://api.example.com")

        browser = Client()
        path = data["url"].split("api.example.com", 1)[1]
        res = browser.get(path, HTTP_HOST="api.example.com", secure=True)
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res["Location"], "/dbadmin/shopdb/")
        cookie = res.cookies[dbadmin.cookie_name(self.db.id)]
        self.assertEqual(cookie["path"], "/dbadmin/shopdb/")
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])

        self.assertEqual(browser.get(f"/api/dbadmin/auth/{self.db.id}").status_code, 200)
        # A cookie for one database does not work for another.
        other = Client()
        other.cookies[dbadmin.cookie_name(self.db.id + 1)] = cookie.value
        self.assertEqual(other.get(f"/api/dbadmin/auth/{self.db.id + 1}").status_code, 401)
        self.assertEqual(Client().get(f"/api/dbadmin/auth/{self.db.id}").status_code, 401)
        self.assertEqual(Client().get("/api/dbadmin/open?t=forged").status_code, 403)

        dbadmin._auth_cache.clear()
        revoke_sessions(get_user_model().objects.get(username="admin"))
        self.assertEqual(browser.get(f"/api/dbadmin/auth/{self.db.id}").status_code, 401)

    def test_viewer_cannot_open(self):
        viewer = get_user_model().objects.create_user("viewer", password="pass-1234-x")
        Membership.objects.create(user=viewer, organization=Organization.objects.first(), role=Membership.Role.VIEWER)
        client = APIClient()
        res = client.post("/api/auth/login", {"username": "viewer", "password": "pass-1234-x"}, format="json")
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        self.assertEqual(client.get(f"/api/databases/{self.db.id}/admin").status_code, 403)
        self.assertEqual(client.post(f"/api/databases/{self.db.id}/admin", {}, format="json").status_code, 403)

        ticket = dbadmin.make_ticket(viewer, self.db, 0)
        self.assertEqual(Client().get(f"/api/dbadmin/open?t={ticket}").status_code, 403)

    def test_close_and_idle_timeout(self):
        self._open()
        self.assertEqual(self.client.delete(f"/api/databases/{self.db.id}/admin").status_code, 204)
        self.assertEqual(self.client.get(f"/api/databases/{self.db.id}/admin").data["status"], "stopped")

        self._open()
        Database.objects.filter(pk=self.db.pk).update(admin_until=timezone.now() - timedelta(minutes=1))
        self.assertEqual(close_idle_db_admins(), 1)
        self.assertEqual(Job.objects.filter(kind=Job.Kind.DB_ADMIN_STOP).count(), 2)
        self.assertEqual(self.client.get(f"/api/databases/{self.db.id}/admin").data["status"], "stopped")

    def test_raw_api_port_uses_api_domain(self):
        res = self.client.post(f"/api/databases/{self.db.id}/admin", {}, format="json", HTTP_HOST="127.0.0.1:8000")
        self.assertTrue(res.data["url"].startswith("http://api.example.com/api/dbadmin/open"))

    def test_specs_and_labels(self):
        tool = dbadmin.spec(self.db, "https://api.example.com")
        self.assertEqual(tool["env"]["SCRIPT_NAME"], "/dbadmin/shopdb")
        self.assertEqual(tool["env"]["PGADMIN_CONFIG_SERVER_MODE"], "False")
        self.assertIn(f":{self.db.username}:", tool["env"]["DOCKRA_PGPASS"])
        labels = "\n".join(dbadmin.labels(self.db, "https://api.example.com", tool))
        self.assertIn("rule=Host(`api.example.com`) && PathPrefix(`/dbadmin/shopdb/`)", labels)
        self.assertIn("entrypoints=websecure", labels)
        self.assertIn(f"forwardauth.address=http://api:8000/api/dbadmin/auth/{self.db.id}", labels)
        self.assertIn("tls.certresolver=le", labels)
        self.assertNotIn("stripprefix", labels)

        mysql = Database(name="m", engine="mysql", host="dockra-db-m", port=3306, username="u", password="p:w")
        tool = dbadmin.spec(mysql, "http://api.example.com")
        self.assertEqual(tool["env"]["PMA_ABSOLUTE_URI"], "http://api.example.com/dbadmin/m/")
        labels = "\n".join(dbadmin.labels(mysql, "http://api.example.com", tool))
        self.assertIn("stripprefix.prefixes=/dbadmin/m", labels)
        self.assertIn("entrypoints=web\n", labels + "\n")
        self.assertNotIn("tls=true", labels)

        mongo = Database(name="g", engine="mongodb", host="dockra-db-g", port=27017, username="u", password="a/b@c")
        self.assertIn("u:a%2Fb%40c@dockra-db-g", dbadmin.spec(mongo, "http://x")["env"]["ME_CONFIG_MONGODB_URL"])

    def test_listening_parser(self):
        tcp = (
            "  sl  local_address rem_address   st\n"
            "   0: 00000000:0050 00000000:0000 0A 00000000:00000000\n"
            "   1: 0100007F:1F91 0100007F:9C40 01 00000000:00000000\n"
        )
        self.assertTrue(dbadmin.listening(tcp, 80))
        self.assertFalse(dbadmin.listening(tcp, 8081))
