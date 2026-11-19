from datetime import timedelta
from unittest import mock

from django.utils import timezone

from databases.models import Database
from engine.models import MetricSample
from engine.test_hardening import _Base


class DatabaseObserveTests(_Base):
    def _db(self):
        made = self.client.post("/api/databases", {"name": "shop-db", "engine": "postgres"}, format="json")
        assert made.status_code == 201, made.content
        return Database.objects.get(pk=made.data["id"])

    def test_metrics_list_and_series(self):
        db = self._db()
        MetricSample.objects.create(database=db, at=timezone.now() - timedelta(minutes=1), cpu=40, ram=55, network_mbps=1.5)
        listed = self.client.get("/api/databases").data[0]
        self.assertEqual((listed["metrics"]["cpu"], listed["metrics"]["ram"]), (40, 55))
        res = self.client.get(f"/api/databases/{db.id}/metrics?range=6h")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["ram"], 55)
        self.assertEqual(len(res.data["series"]), 1)
        self.assertEqual(self.client.get(f"/api/databases/{db.id}/metrics?range=2y").status_code, 400)
        # Database samples never leak into the app report.
        self.assertEqual(self.client.get("/api/apps/shop/metrics").data["series"], [])

    def test_logs_go_through_rpc(self):
        db = self._db()
        res = self.client.get(f"/api/databases/{db.id}/logs")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn("shop-db", res.data["text"])

    def test_sampler_records_running_databases(self):
        from engine.metrics import sample_all

        db = self._db()
        driver = mock.Mock()
        driver.usage_all.return_value = {f"db:{db.container_name}": {}}
        driver.database_usage.return_value = {"cpu": 12, "ram": 30, "network_mbps": 0.2}
        with mock.patch("engine.metrics.get_driver", return_value=driver), mock.patch("engine.traffic.scrape", return_value={}):
            sample_all()
        sample = MetricSample.objects.get(database=db)
        self.assertEqual((sample.cpu, sample.ram, sample.app_id), (12, 30, None))
