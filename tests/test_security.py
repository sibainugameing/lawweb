import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def csrf(self, path="/login"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return match.group(1)

    def login(self, username="reader01", password="correct horse battery staple 2026"):
        token = self.csrf("/login")
        return self.client.post(
            "/login",
            data={"csrf_token": token, "username": username, "password": password},
            headers={"Origin": "http://localhost"},
        )

    def test_reset_accounts_removes_all_auth_records(self):
        lawweb.create_user_record("reader02", "another secure passphrase 2026")
        with lawweb.connect_db() as db:
            db.execute(
                "INSERT INTO auth_sessions(sid_hash,user_id,created_at,last_seen_at,expires_at) "
                "VALUES(?,?,?,?,?)",
                ("session-digest", 1, 1, 1, 9999999999),
            )
            db.execute(
                "INSERT INTO login_limits(key_hash,window_started_at,failures,blocked_until) "
                "VALUES(?,?,?,?)",
                ("limit-digest", 1, 5, 9999999999),
            )
            db.commit()

        result = lawweb.app.test_cli_runner().invoke(
            args=["reset-accounts"], input="y\n"
        )
        self.assertEqual(result.exit_code, 0, result.output)
        with lawweb.connect_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM login_limits").fetchone()[0], 0)

    def test_local_origin_comparison_accepts_loopback_alias_and_checks_port(self):
        with lawweb.app.test_request_context("/", base_url="http://127.0.0.1:5000"):
            self.assertTrue(lawweb.same_request_origin("http://localhost:5000"))
            self.assertFalse(lawweb.same_request_origin("http://localhost:5001"))
            self.assertFalse(lawweb.same_request_origin("https://localhost:5000"))

    def test_lan_registration_accepts_null_origin_without_fetch_metadata(self):
        base_url = "http://192.168.1.16:5000"
        remote = {"REMOTE_ADDR": "192.168.1.25"}
        page = self.client.get("/register", base_url=base_url, environ_base=remote)
        self.assertEqual(page.status_code, 200)
        match = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))
        self.assertIsNotNone(match)
        response = self.client.post(
            "/register",
            base_url=base_url,
            environ_base=remote,
            data={
                "csrf_token": match.group(1),
                "username": "lanreader",
                "password": "another secure passphrase 2026",
                "password_confirm": "another secure passphrase 2026",
            },
            headers={"Origin": "null"},
        )
        self.assertEqual(response.status_code, 302)
        with lawweb.connect_db() as db:
            self.assertIsNotNone(db.execute("SELECT id FROM users WHERE username='lanreader'").fetchone())

    def test_lan_null_origin_still_requires_the_session_csrf_token(self):
        base_url = "http://192.168.1.16:5000"
        remote = {"REMOTE_ADDR": "192.168.1.25"}
        page = self.client.get("/register", base_url=base_url, environ_base=remote)
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            "/register",
            base_url=base_url,
            environ_base=remote,
            data={
                "csrf_token": "incorrect-token",
                "username": "wrongtoken",
                "password": "another secure passphrase 2026",
                "password_confirm": "another secure passphrase 2026",
            },
            headers={"Origin": "null"},
        )
        self.assertEqual(response.status_code, 403)

    def test_lan_registration_rejects_null_origin_marked_cross_site(self):
        base_url = "http://192.168.1.16:5000"
        remote = {"REMOTE_ADDR": "192.168.1.25"}
        page = self.client.get("/register", base_url=base_url, environ_base=remote)
        match = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))
        self.assertIsNotNone(match)
        response = self.client.post(
            "/register",
            base_url=base_url,
            environ_base=remote,
            data={
                "csrf_token": match.group(1),
                "username": "blockedlan",
                "password": "another secure passphrase 2026",
                "password_confirm": "another secure passphrase 2026",
            },
            headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(response.status_code, 403)

    def test_register_csrf_failure_keeps_registration_form_visible(self):
        token = self.csrf("/register")
        response = self.client.post(
            "/register",
            data={
                "csrf_token": token, "username": "reader03",
                "password": "another secure passphrase 2026",
                "password_confirm": "another secure passphrase 2026",
            },
            headers={"Origin": "http://attacker.example"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("新規登録", response.get_data(as_text=True))
        self.assertIn("ホスト名またはポートが一致しません", response.get_data(as_text=True))

    def test_account_password_can_be_changed_from_web(self):
        self.assertEqual(self.login().status_code, 302)
        token = self.csrf("/account")
        response = self.client.post(
            "/account/password",
            data={
                "csrf_token": token,
                "current_password": "correct horse battery staple 2026",
                "new_password": "a newer secure passphrase 2026",
                "password_confirm": "a newer secure passphrase 2026",
            },
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 302)
        with lawweb.connect_db() as db:
            row = db.execute("SELECT password_hash FROM users WHERE username=?", ("reader01",)).fetchone()
        self.assertTrue(lawweb.PASSWORDS.verify(row["password_hash"], "a newer secure passphrase 2026"))
        self.assertEqual(self.client.get("/account").status_code, 200)

    def test_account_delete_requires_password_and_confirmation(self):
        self.assertEqual(self.login().status_code, 302)
        token = self.csrf("/account")
        response = self.client.post(
            "/account/delete",
            data={
                "csrf_token": token,
                "password": "correct horse battery staple 2026",
                "confirmation": "DELETE",
            },
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 302)
        with lawweb.connect_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
        landing = self.client.get(response.headers["Location"])
        self.assertEqual(landing.status_code, 200)
        self.assertIn("アカウントを削除しました", landing.get_data(as_text=True))

    def test_public_home_is_available_without_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"LawWeb", response.data)
        self.assertIn(b"新規登録", response.data)

    def test_public_ui_has_desktop_and_mobile_navigation(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("class=\"sidebar\"", html)
        self.assertIn("class=\"mobile-nav\"", html)
        self.assertIn("id=\"view-root\"", html)
        self.assertIn("id=\"search-form\"", html)
        self.assertIn("data-view=\"laws\"", html)
        self.assertIn("viewport-fit=cover", html)
        self.assertNotIn("brand-mark", html)

    def test_authenticated_ui_exposes_all_reader_sections(self):
        self.assertEqual(self.login().status_code, 302)
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        for view in ("overview", "laws", "rules", "activity", "integrity"):
            self.assertIn(f'data-view="{view}"', html)
        self.assertIn("アカウント", html)

    def test_law_ui_exposes_markdown_xml_and_diff_controls(self):
        self.assertEqual(self.login().status_code, 302)
        law_uuid = "550e8400-e29b-41d4-a716-446655440000"
        xml = """<law><metadata><name>UI試験法</name><type>法律</type></metadata>
        <body><MainProvision><Article Num="1"><ArticleTitle>第一条</ArticleTitle>
        <Paragraph Num="1"><ParagraphSentence><Sentence>本文。</Sentence></ParagraphSentence></Paragraph>
        </Article></MainProvision></body></law>"""
        with patch.object(lawweb, "lawgit_request", return_value=({"law":{"uuid":law_uuid,"name":"UI試験法"}, "xml":xml}, None)):
            response = self.client.get("/api/laws/" + law_uuid)
        self.assertEqual(response.status_code, 200)
        self.assertIn("markdown", response.json["result"])
        js = self.client.get("/static/app.js").get_data(as_text=True)
        self.assertIn("law-display-mode", js)
        self.assertIn("compare-revisions", js)
        self.assertIn("diffToHtml", js)

    def test_rebuilt_ui_assets_are_served(self):
        css = self.client.get("/static/app.css")
        js = self.client.get("/static/app.js")
        self.assertEqual(css.status_code, 200)
        self.assertEqual(js.status_code, 200)
        self.assertIn(b".mobile-nav", css.data)
        self.assertIn(b"function renderLawList", js.data)
        self.assertIn(b"function renderRuleList", js.data)
        self.assertIn(b"compositionstart", js.data)
        self.assertIn(b"markdownToHtml", js.data)
        self.assertIn(b"compare-revisions", js.data)
        self.assertIn(b"diffToHtml", js.data)
        self.assertIn(b"splitMarkdownTableRow", js.data)
        self.assertIn(b"md-table-wrap", css.data)

    def test_logged_in_user_keeps_authenticated_navigation_on_public_home(self):
        self.assertEqual(self.login().status_code, 302)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"reader01", response.data)
        self.assertIn(b"アカウント管理", response.data)
        self.assertNotIn(b"LAWGit Workspace", response.data)

    def test_public_law_list_returns_metadata_only(self):
        mocked_laws = {
            "laws": [{
                "uuid": "u-1", "name": "Test Act", "type": "Law", "law_number": "No. 1",
                "e_gov_id": "123", "category": "Public",
                "path": "private/path.xml", "version": "private-version",
            }]
        }
        with patch.object(lawweb, "lawgit_request", return_value=(mocked_laws, None)):
            response = self.client.get("/api/public/laws")
        self.assertEqual(response.status_code, 200)
        law = response.json["result"]["laws"][0]
        self.assertEqual(law["name"], "Test Act")
        self.assertNotIn("path", law)
        self.assertNotIn("version", law)

    def test_public_law_detail_returns_markdown_without_raw_xml_or_private_fields(self):
        law_uuid = "550e8400-e29b-41d4-a716-446655440000"
        xml = """<law><metadata><name>公開試験法</name><law-number>令和元年法律第一号</law-number><type>法律</type></metadata>
        <body><MainProvision><Article Num="1"><ArticleTitle>第一条</ArticleTitle>
        <Paragraph Num="1"><ParagraphSentence><Sentence>公開された本文。</Sentence></ParagraphSentence></Paragraph>
        </Article></MainProvision></body></law>"""
        detail = {
            "law": {"uuid": law_uuid, "name": "公開試験法", "path": "secret.xml", "version": "private-version"},
            "xml": xml,
        }
        with patch.object(lawweb, "lawgit_request", return_value=(detail, None)):
            response = self.client.get("/api/public/laws/" + law_uuid)
        self.assertEqual(response.status_code, 200)
        result = response.json["result"]
        self.assertIn("公開された本文。", result["markdown"])
        self.assertNotIn("xml", result)
        self.assertNotIn("path", result["law"])
        self.assertNotIn("version", result["law"])

    def test_public_search_returns_only_law_hits_and_allowlisted_fields(self):
        law_uuid = "550e8400-e29b-41d4-a716-446655440000"
        rule_uuid = "550e8400-e29b-41d4-a716-446655440001"
        search_result = {
            "query": "契約",
            "hits": [
                {"kind": "law", "uuid": law_uuid, "name": "民法", "matched": "本文", "snippet": "契約に関する規定", "path": "private/law.xml"},
                {"kind": "rule", "uuid": rule_uuid, "name": "個人ルール", "matched": "本文", "snippet": "秘密のルール"},
            ],
            "truncated": False,
        }
        with patch.object(lawweb, "lawgit_request", return_value=(search_result, None)):
            response = self.client.get("/api/public/search?q=%E5%A5%91%E7%B4%84")
        self.assertEqual(response.status_code, 200)
        hits = response.json["result"]["hits"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["name"], "民法")
        self.assertNotIn("path", hits[0])
        self.assertEqual(hits[0]["kind"], "law")

    def test_public_search_is_read_only_and_rules_remain_private(self):
        self.assertEqual(self.client.post("/api/public/search", json={"q": "民法"}).status_code, 405)
        self.assertEqual(self.client.get("/api/rules").status_code, 401)

    def test_public_ui_advertises_full_text_search_and_public_law_reading(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("条文の言葉を検索", response.get_data(as_text=True))
        script = self.client.get("/static/app.js").get_data(as_text=True)
        self.assertIn("public/search", script)
        self.assertIn("公開されている本文の整形表示", script)
        self.assertIn("law-search-suggestions", self.client.get("/").get_data(as_text=True) + script)

    def test_public_search_rejects_empty_and_overlong_queries(self):
        self.assertEqual(self.client.get("/api/public/search").status_code, 400)
        long_query = "法" * 121
        self.assertEqual(self.client.get("/api/public/search?q=" + long_query).status_code, 400)

    def test_public_law_detail_rejects_invalid_uuid(self):
        response = self.client.get("/api/public/laws/not-a-uuid")
        self.assertEqual(response.status_code, 400)

    def test_law_detail_api_includes_markdown_and_preserves_xml(self):
        self.assertEqual(self.login().status_code, 302)
        law_uuid = "550e8400-e29b-41d4-a716-446655440000"
        xml = """<law><identifiers><uuid>550e8400-e29b-41d4-a716-446655440000</uuid></identifiers>
        <metadata><name>試験法</name><law-number>令和元年法律第一号</law-number><type>法律</type></metadata>
        <body><MainProvision><Chapter Num="1"><ChapterTitle>第一章 総則</ChapterTitle>
        <Article Num="1"><ArticleTitle>第一条</ArticleTitle><Paragraph Num="1">
        <ParagraphSentence><Sentence>本文の内容。</Sentence></ParagraphSentence></Paragraph></Article>
        </Chapter></MainProvision></body></law>"""
        with patch.object(lawweb, "lawgit_request", return_value=({"law":{"uuid":law_uuid,"name":"試験法"}, "xml":xml}, None)):
            response = self.client.get("/api/laws/" + law_uuid)
        self.assertEqual(response.status_code, 200)
        self.assertIn("# 試験法", response.json["result"]["markdown"])
        self.assertIn("本文の内容。", response.json["result"]["markdown"])
        self.assertEqual(response.json["result"]["xml"], xml)

    def test_law_revision_api_includes_markdown(self):
        self.assertEqual(self.login().status_code, 302)
        law_uuid = "550e8400-e29b-41d4-a716-446655440000"
        xml = """<law><metadata><name>過去法</name><type>法律</type></metadata>
        <body><MainProvision><Article Num="1"><ArticleTitle>第一条</ArticleTitle>
        <Paragraph Num="1"><ParagraphSentence><Sentence>過去の本文。</Sentence></ParagraphSentence></Paragraph>
        </Article></MainProvision></body></law>"""
        with patch.object(lawweb, "lawgit_request", return_value=({"rev":"abcd1234","xml":xml}, None)):
            response = self.client.get("/api/laws/" + law_uuid + "/revisions/abcd1234")
        self.assertEqual(response.status_code, 200)
        self.assertIn("過去の本文。", response.json["result"]["markdown"])

    def test_law_diff_api_compares_markdown_revisions(self):
        self.assertEqual(self.login().status_code, 302)
        law_uuid = "550e8400-e29b-41d4-a716-446655440000"
        base = """<law><metadata><name>試験法</name><type>法律</type></metadata>
        <body><MainProvision><Article Num="1"><ArticleTitle>第一条</ArticleTitle>
        <Paragraph Num="1"><ParagraphSentence><Sentence>旧本文。</Sentence></ParagraphSentence></Paragraph>
        </Article></MainProvision></body></law>"""
        head = base.replace("旧本文", "新しい本文")
        def fake_lawgit(path, params=None):
            return ({"xml": base if path.endswith("/1111") else head}, None)
        with patch.object(lawweb, "lawgit_request", side_effect=fake_lawgit):
            response = self.client.get("/api/laws/" + law_uuid + "/diff?older=1111&newer=2222")
        self.assertEqual(response.status_code, 200)
        result = response.json["result"]
        self.assertIn("-1. 旧本文。", result["diff"])
        self.assertIn("+1. 新しい本文。", result["diff"])
        self.assertGreater(result["additions"], 0)
        self.assertGreater(result["deletions"], 0)

    def test_law_diff_api_rejects_invalid_revision_ids(self):
        self.assertEqual(self.login().status_code, 302)
        response = self.client.get(
            "/api/laws/550e8400-e29b-41d4-a716-446655440000/diff?older=../../etc&newer=1234"
        )
        self.assertEqual(response.status_code, 400)

    def test_private_api_still_requires_login(self):
        response = self.client.get("/api/rules")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json["ok"])

    def test_login_rejects_missing_csrf(self):
        response = self.client.post(
            "/login",
            data={"username": "reader01", "password": "correct horse battery staple 2026"},
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 403)

    def test_login_accepts_valid_account_and_creates_server_session(self):
        response = self.login()
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as browser_session:
            self.assertTrue(browser_session.get("_sid"))
        with patch.object(lawweb, "lawgit_request", return_value=({"rules": []}, None)):
            private = self.client.get("/api/rules")
        self.assertEqual(private.status_code, 200)

    def test_web_registration_creates_account_and_logs_in(self):
        token = self.csrf("/register")
        response = self.client.post(
            "/register",
            data={
                "csrf_token": token,
                "username": "reader02",
                "password": "another secure passphrase 2026",
                "password_confirm": "another secure passphrase 2026",
            },
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)
        with self.client.session_transaction() as browser_session:
            self.assertTrue(browser_session.get("_sid"))
        with patch.object(lawweb, "lawgit_request", return_value=({"rules": []}, None)):
            self.assertEqual(self.client.get("/api/rules").status_code, 200)

    def test_registration_requires_csrf(self):
        response = self.client.post(
            "/register",
            data={
                "username": "reader03",
                "password": "another secure passphrase 2026",
                "password_confirm": "another secure passphrase 2026",
            },
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 403)

    def test_registration_checks_password_confirmation(self):
        token = self.csrf("/register")
        response = self.client.post(
            "/register",
            data={
                "csrf_token": token,
                "username": "reader03",
                "password": "another secure passphrase 2026",
                "password_confirm": "not the same passphrase",
            },
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("一致しません", response.get_data(as_text=True))

    def test_authenticated_api_has_no_write_methods(self):
        self.assertEqual(self.login().status_code, 302)
        for route in lawweb.app.url_map.iter_rules():
            if route.rule.startswith("/api/"):
                self.assertFalse({"POST", "PUT", "PATCH", "DELETE"} & route.methods, route.rule)
        self.assertEqual(self.client.post("/api/rules").status_code, 405)
        self.assertEqual(self.client.post("/api/git/commit").status_code, 405)

    def test_logout_revokes_server_side_session(self):
        self.assertEqual(self.login().status_code, 302)
        with self.client.session_transaction() as browser_session:
            token = browser_session["_csrf"]
        response = self.client.post(
            "/logout", data={"csrf_token": token}, headers={"Origin": "http://localhost"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/api/rules").status_code, 401)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_account_login_rate_limit_is_enforced(self):
        now = int(time.time())
        with lawweb.app.test_request_context("/login", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            keys = lawweb.login_keys("reader01")
            for _ in range(lawweb.ACCOUNT_LIMIT):
                lawweb.record_failure(keys[:1], now)
            self.assertTrue(lawweb.is_limited(keys, now))

    def test_ip_login_rate_limit_is_enforced(self):
        now = int(time.time())
        with lawweb.app.test_request_context("/login", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            keys = lawweb.login_keys("anotheruser")
            for _ in range(lawweb.IP_LIMIT):
                lawweb.record_failure(keys[1:], now)
            self.assertTrue(lawweb.is_limited(keys, now))

    def test_registration_attempts_are_rate_limited(self):
        for index in range(lawweb.REGISTRATION_LIMIT):
            token = self.csrf("/register")
            response = self.client.post(
                "/register",
                data={
                    "csrf_token": token,
                    "username": f"baduser{index}",
                    "password": "another secure passphrase 2026",
                    "password_confirm": "not the same password",
                },
                headers={"Origin": "http://localhost"},
            )
            self.assertEqual(response.status_code, 400)

        token = self.csrf("/register")
        blocked = self.client.post(
            "/register",
            data={
                "csrf_token": token,
                "username": "reader44",
                "password": "another secure passphrase 2026",
                "password_confirm": "another secure passphrase 2026",
            },
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(blocked.status_code, 429)

    def test_non_loopback_client_is_rejected(self):
        response = self.client.get(
            "/login", environ_base={"REMOTE_ADDR": "192.0.2.10"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("同じPCまたは同じプライベートネットワーク", response.get_data(as_text=True))
        self.assertNotIn("入力内容は送信されていません", response.get_data(as_text=True))

    def test_private_lan_client_can_open_login(self):
        response = self.client.get(
            "/login", base_url="http://192.168.1.20:5000",
            environ_base={"REMOTE_ADDR": "192.168.1.30"},
        )
        self.assertEqual(response.status_code, 200)

    def test_private_lan_local_hostname_is_accepted(self):
        response = self.client.get(
            "/login", base_url="http://lawweb.local:5000",
            environ_base={"REMOTE_ADDR": "192.168.1.30"},
        )
        self.assertEqual(response.status_code, 200)

    def test_private_lan_ip_and_ipv6_ula_are_allowed(self):
        self.assertTrue(lawweb.is_local_network_ip("127.0.0.1"))
        self.assertTrue(lawweb.is_local_network_ip("192.168.1.24"))
        self.assertTrue(lawweb.is_local_network_ip("10.20.30.40"))
        self.assertTrue(lawweb.is_local_network_ip("fd12:3456:789a::1"))
        self.assertFalse(lawweb.is_local_network_ip("8.8.8.8"))
        self.assertFalse(lawweb.is_local_network_ip("169.254.1.2"))
        self.assertFalse(lawweb.is_local_network_ip("192.0.2.10"))

    def test_registration_works_from_private_lan_origin(self):
        client = lawweb.app.test_client()
        page = client.get(
            "/register", base_url="http://192.168.1.20:5000",
            environ_base={"REMOTE_ADDR": "192.168.1.30"},
        )
        self.assertEqual(page.status_code, 200)
        match = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))
        self.assertIsNotNone(match)
        response = client.post(
            "/register",
            base_url="http://192.168.1.20:5000",
            environ_base={"REMOTE_ADDR": "192.168.1.30"},
            data={
                "csrf_token": match.group(1), "username": "lanreader",
                "password": "another secure passphrase 2026",
                "password_confirm": "another secure passphrase 2026",
            },
            headers={
                "Origin": "http://192.168.1.20:5000",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        self.assertEqual(response.status_code, 302)
        with lawweb.connect_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE username='lanreader'").fetchone()[0], 1)

    def test_public_client_is_rejected_even_with_private_host_header(self):
        response = self.client.get(
            "/login", base_url="http://192.168.1.20:5000",
            environ_base={"REMOTE_ADDR": "8.8.8.8"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("同じPCまたは同じプライベートネットワーク", response.get_data(as_text=True))

    def test_public_host_header_is_rejected_from_lan_client(self):
        response = self.client.get(
            "/login", base_url="http://example.com:5000",
            environ_base={"REMOTE_ADDR": "192.168.1.30"},
        )
        self.assertEqual(response.status_code, 403)

    def test_local_referer_with_form_path_is_accepted(self):
        with lawweb.app.test_request_context(
            "/register", base_url="http://127.0.0.1:5000"
        ):
            self.assertTrue(lawweb.same_request_origin(
                "http://localhost:5000/register?source=home", allow_path=True
            ))

    def test_registration_html_uses_server_side_username_validation(self):
        response = self.client.get("/register")
        html = response.get_data(as_text=True)
        self.assertNotIn("pattern=", html)
        self.assertIn("maxlength=\"32\"", html)

    def test_registration_missing_csrf_shows_specific_recovery_message(self):
        response = self.client.post(
            "/register",
            data={
                "username": "reader67",
                "password": "another secure passphrase 2026",
                "password_confirm": "another secure passphrase 2026",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRFトークンがフォームまたはセッションCookieから取得できませんでした", response.get_data(as_text=True))

    def test_registration_accepts_null_origin_with_same_origin_fetch_metadata(self):
        client = lawweb.app.test_client()
        page = client.get("/register", base_url="http://127.0.0.1:5000")
        match = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))
        self.assertIsNotNone(match)
        response = client.post(
            "/register",
            base_url="http://127.0.0.1:5000",
            data={
                "csrf_token": match.group(1), "username": "reader89",
                "password": "another secure passphrase 2026",
                "password_confirm": "another secure passphrase 2026",
            },
            headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"},
        )
        self.assertEqual(response.status_code, 302)
        with lawweb.connect_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE username='reader89'").fetchone()[0], 1)

    def test_null_origin_marked_cross_site_is_rejected(self):
        client = lawweb.app.test_client()
        page = client.get("/register", base_url="http://127.0.0.1:5000")
        match = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))
        self.assertIsNotNone(match)
        response = client.post(
            "/register",
            base_url="http://127.0.0.1:5000",
            data={
                "csrf_token": match.group(1), "username": "reader90",
                "password": "another secure passphrase 2026",
                "password_confirm": "another secure passphrase 2026",
            },
            headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("別サイトからの送信と判定", response.get_data(as_text=True))
        with lawweb.connect_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE username='reader90'").fetchone()[0], 0)

    def test_registration_accepts_equivalent_loopback_origin(self):
        client = lawweb.app.test_client()
        response = client.get("/register", base_url="http://127.0.0.1:5000")
        self.assertEqual(response.status_code, 200)
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        response = client.post(
            "/register",
            base_url="http://127.0.0.1:5000",
            data={
                "csrf_token": match.group(1), "username": "reader88",
                "password": "another secure passphrase 2026",
                "password_confirm": "another secure passphrase 2026",
            },
            headers={"Origin": "http://localhost:5000"},
        )
        self.assertEqual(response.status_code, 302)

    def test_account_delete_with_wrong_password_is_rejected_cleanly(self):
        self.assertEqual(self.login().status_code, 302)
        token = self.csrf("/account")
        response = self.client.post(
            "/account/delete",
            data={
                "csrf_token": token,
                "password": "incorrect password",
                "confirmation": "DELETE",
            },
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("パスワードが正しくありません", response.get_data(as_text=True))
        with lawweb.connect_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_security_headers_are_present(self):
        response = self.client.get("/login")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("default-src 'self'", response.headers.get("Content-Security-Policy", ""))


if __name__ == "__main__":
    unittest.main()
