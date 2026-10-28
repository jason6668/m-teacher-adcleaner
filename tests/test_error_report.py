"""错误日志上报：内容脱敏、预览与发送一致、限频、安装编号、服务器回应处理。不联网。"""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]


def load_app():
    spec = importlib.util.spec_from_file_location("aac_report_test", ROOT / "gui/app.py")
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    return app


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.app = load_app()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.app.DATA = self.dir.name
        self.api = self.app.Api()
        home = str(Path.home())
        self.api.last_status = {"info": {"brand": "Xiaomi", "name": "Xiaomi 17", "model": "25113PN0EC",
                                         "os": "OS3.0.40.0", "build": "BP2A", "android": "16"}}
        self.api.job = {"serial": "TESTSERIAL01", "results": [
            {"title": "关闭营销通知", "rc": 1, "sec": 12},
            {"title": "收尾", "rc": 0, "sec": 1},
            {"title": "关闭各 App 里的广告开关", "rc": 3, "sec": 0, "skipped": True}],
            "lines": ["▶ 关闭营销通知", f"device 'TESTSERIAL01' 读取 {home}/AppData/x", "联系 a.b@qq.com 13900001111",
                      "adb connect 192.0.2.10:5555"]}

    def test_payload_scrubs_private_values(self):
        payload = self.api.report_payload()
        text = json.dumps(payload, ensure_ascii=False)
        for secret in ("TESTSERIAL01", str(Path.home()), "a.b@qq.com", "13900001111", "192.0.2.10"):
            self.assertNotIn(secret, text)
        for mark in ("<序列号>", "<用户目录>", "<邮箱>", "<手机号>", "<IP>"):
            self.assertIn(mark, payload["log"])
        self.assertEqual([s["result"] for s in payload["steps"]], ["失败", "成功", "跳过"])
        self.assertNotIn("install_id", payload)

    def test_payload_fits_server_limits(self):
        self.api.job["lines"] = ["x" * 500] * 200
        self.api.job["results"] = [{"title": "步骤" * 30, "rc": 0, "sec": 1}] * 20
        payload = self.api.report_payload()
        self.assertLessEqual(len(payload["log"]), 20000)
        self.assertEqual(len(payload["steps"]), 12)
        self.assertTrue(all(len(s["title"]) <= 40 for s in payload["steps"]))
        self.assertLess(len(json.dumps(payload, ensure_ascii=False).encode()), 64 * 1024)

    def test_send_disabled_without_server(self):
        payload = self.api.report_payload()
        with patch.object(self.app.urllib.request, "urlopen") as call:
            result = self.api.send_report(payload)
        self.assertIn("暂不提供远程上报", result["error"])
        self.assertIn("GitHub", result["error"])
        call.assert_not_called()

    def test_invalid_install_id_file_is_replaced(self):
        Path(self.dir.name, "install-id").write_text("serial-or-junk")
        value = self.api._install_id()
        self.assertNotEqual(value, "serial-or-junk")
        self.assertRegex(value, r"^[0-9a-f-]{36}$")

    def test_disabled_send_does_not_consume_quota(self):
        payload = self.api.report_payload()
        for _ in range(4):
            result = self.api.send_report(payload)
            self.assertIn("暂不提供远程上报", result["error"])
        self.assertEqual(self.api.report_times, [])

SERVER = ROOT / ".private/aac-report"      # 服务端代码只在维护者本机，不进仓库


@unittest.skipUnless((SERVER / "index.ts").exists(), "服务端代码不在仓库里")
class ServerContractTests(unittest.TestCase):
    """客户端字段上限与服务端一致。"""

    def test_limits_match_edge_function_and_schema(self):
        ts = (SERVER / "index.ts").read_text(encoding="utf-8")
        sql = (SERVER / "schema.sql").read_text(encoding="utf-8")
        for field, limit in (("client_version", 32), ("platform", 80), ("phone_brand", 40), ("phone_model", 80),
                             ("phone_system", 160), ("android", 16), ("log", 20000)):
            self.assertIn(f"{field}: {limit}", ts)
            self.assertRegex(sql, rf"{field}.*{limit}")
        self.assertIn("MAX_STEPS = 12", ts)
        self.assertIn("revoke all on aac.error_reports from public, anon, authenticated, service_role", sql)


if __name__ == "__main__":
    unittest.main()
