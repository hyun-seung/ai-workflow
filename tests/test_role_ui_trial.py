import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from role_ui_trial import TrialServer


class RoleUITrialTests(unittest.TestCase):
    def setUp(self):
        self.server = TrialServer(0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, value=None, origin=None):
        body = json.dumps(value).encode() if value is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        if origin:
            headers["Origin"] = origin
        request = Request(self.base + path, data=body, headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.code, response.headers, response.read()

    def test_real_failure_retry_and_query_agree(self):
        title = "가상 회의 준비"
        self.assertEqual(self.request("/api/tasks", {"title": title})[0], 503)
        code, _, payload = self.request("/api/tasks", {"title": title})
        self.assertEqual(code, 201)
        stored = json.loads(payload)
        code, _, payload = self.request("/api/tasks")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(payload), {"requests": 2, "tasks": [stored]})
        self.assertEqual(stored["title"], title)

    def test_title_contract_and_cross_origin_reset(self):
        for value in ({"title": ""}, {"title": " "}, {"title": "가" * 121}, {"title": "😀" * 61}, {"title": "\ud800"}, []):
            with self.subTest(value=repr(value)):
                self.assertEqual(self.request("/api/tasks", value)[0], 400)
        self.assertEqual(json.loads(self.request("/api/tasks")[2])["requests"], 0)
        # 60 supplementary-plane characters have 120 UTF-16 units, matching maxlength.
        self.assertEqual(self.request("/api/tasks", {"title": "😀" * 60})[0], 503)
        self.assertEqual(self.request("/api/reset", {}, origin="https://example.invalid")[0], 403)
        self.assertEqual(json.loads(self.request("/api/tasks")[2])["requests"], 1)

    def test_static_allowlist_does_not_serve_repository_files(self):
        code, headers, _ = self.request("/?revision=fixed&data=worst")
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers["Content-Type"])
        for path in ("/AGENTS.md", "/local/role-skills/registry.json", "/../AGENTS.md", "/%2e%2e/AGENTS.md"):
            self.assertEqual(self.request(path)[0], 404)


if __name__ == "__main__":
    unittest.main()
