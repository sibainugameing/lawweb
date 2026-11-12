import re
import tempfile
import unittest
from pathlib import Path

import app as lawweb


class LawWebSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        lawweb.DB_PATH = Path(self.temp_dir.name) / "lawweb-test.sqlite3"
        lawweb.init_database()
        lawweb.create_user_record("reader01", "correct horse battery staple 2026")
        self.client = lawweb.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pages_and_apis_require_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

        response = self.client.get("/api/rules")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json["ok"])

    def test_login_requires_csrf_and_authenticates(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        csrf = match.group(1)

        rejected = self.client.post("/login", data={
            "username": "reader01",
            "password": "correct horse battery staple 2026",
        }, headers={"Origin": "http://localhost"})
        self.assertEqual(rejected.status_code, 403)

        accepted = self.client.post("/login", data={
            "csrf_token": csrf,
            "username": "reader01",
            "password": "correct horse battery staple 2026",
        }, headers={"Origin": "http://localhost"})
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_authenticated_api_has_no_write_methods(self):
        response = self.client.get("/login")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)).group(1)
        self.client.post("/login", data={
            "csrf_token": csrf,
            "username": "reader01",
            "password": "correct horse battery staple 2026",
        }, headers={"Origin": "http://localhost"})

        for route in lawweb.app.url_map.iter_rules():
            if route.rule.startswith("/api/"):
                self.assertFalse({"POST", "PUT", "PATCH", "DELETE"} & route.methods, route.rule)

        self.assertEqual(self.client.post("/api/rules").status_code, 405)
        self.assertEqual(self.client.post("/api/git/commit").status_code, 405)


    def test_logout_revokes_the_server_side_session(self):
        response = self.client.get("/login")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)).group(1)
        self.client.post("/login", data={
            "csrf_token": csrf,
            "username": "reader01",
            "password": "correct horse battery staple 2026",
        }, headers={"Origin": "http://localhost"})
        with self.client.session_transaction() as browser_session:
            logout_csrf = browser_session["_csrf"]

        logged_out = self.client.post("/logout", data={"csrf_token": logout_csrf},
                                      headers={"Origin": "http://localhost"})
        self.assertEqual(logged_out.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_account_login_rate_limit_is_enforced(self):
        now = int(__import__("time").time())
        with lawweb.app.test_request_context(
            "/login", environ_base={"REMOTE_ADDR": "127.0.0.1"}
        ):
            keys = lawweb.login_keys("reader01")
            for _ in range(lawweb.ACCOUNT_LIMIT):
                lawweb.record_failure(keys[:1], now)
            self.assertTrue(lawweb.is_limited(keys, now))

    def test_ip_login_rate_limit_is_enforced(self):
        now = int(__import__("time").time())
        with lawweb.app.test_request_context(
            "/login", environ_base={"REMOTE_ADDR": "127.0.0.1"}
        ):
            keys = lawweb.login_keys("anotheruser")
            for _ in range(lawweb.IP_LIMIT):
                lawweb.record_failure(keys[1:], now)
            self.assertTrue(lawweb.is_limited(keys, now))

    def test_security_headers_are_present(self):
        response = self.client.get("/login")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("default-src 'self'", response.headers.get("Content-Security-Policy", ""))


if __name__ == "__main__":
    unittest.main()
