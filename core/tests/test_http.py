import http.cookiejar
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase

from core.models import Block, ChainState, Execution, Project, Report, TestCase, Version


class HTTPWorkflowTests(LiveServerTestCase):
    def setUp(self):
        ChainState.objects.get_or_create(pk=1)
        get_user_model().objects.create_user("http-user", password="Actual-HTTP-901!")
        self.cookies = http.cookiejar.CookieJar()
        self.browser = build_opener(HTTPCookieProcessor(self.cookies))
        self.assertEqual(self.get("/login/").status, 200)
        self.assertEqual(self.post("/login/", {"username": "http-user", "password": "Actual-HTTP-901!"}).status, 200)

    def get(self, path):
        return self.browser.open(self.live_server_url + path, timeout=15)

    def post(self, path, data):
        data["csrfmiddlewaretoken"] = next(cookie.value for cookie in self.cookies if cookie.name == "csrftoken")
        request = Request(self.live_server_url + path, data=urlencode(data).encode(), headers={"Referer": self.live_server_url + path})
        return self.browser.open(request, timeout=30)

    def create_project_version(self, name):
        self.post("/projects/new/", {"name": name, "description": "端到端验证"})
        project = Project.objects.get(name=name)
        prefix = f"/projects/{project.pk}"
        self.post(prefix + "/versions/new/", {"name": "v1.0"})
        return prefix, Version.objects.get(project=project)

    def record_execution(self, prefix, version, title, status):
        self.post(prefix + "/cases/new/", {
            "title": title, "steps": "输入账号密码", "expected": "登录成功", "priority": "high",
        })
        case = TestCase.objects.get(project=version.project, title=title)
        self.post(prefix + f"/cases/{case.pk}/execute/", {
            "version": version.pk, "status": status, "actual": "已记录执行结果",
        })
        self.assertEqual(Execution.objects.get(case=case).status, status)
        return case

    def verify_file(self, report_url, data):
        boundary = "testchain-http-boundary"
        token = next(cookie.value for cookie in self.cookies if cookie.name == "csrftoken")
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="csrfmiddlewaretoken"\r\n\r\n{token}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="report.txt"\r\nContent-Type: text/plain\r\n\r\n'
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        request = Request(self.live_server_url + report_url + "/verify/", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with self.browser.open(request, timeout=15) as response:
            self.assertEqual(response.status, 200)
            return response.read().decode()

    def test_real_http_login_to_download_and_verification(self):
        prefix, version = self.create_project_version("真实 HTTP 项目")
        self.record_execution(prefix, version, "登录测试", "passed")
        self.post(prefix + "/reports/generate/", {"title": "验收报告", "version": version.pk})
        report = Report.objects.get(version=version)
        report_url = prefix + f"/reports/{report.pk}"
        self.post(report_url + "/anchor/", {})
        block_id = Block.objects.get(report=report).pk
        self.post(report_url + "/anchor/", {})
        self.assertEqual(Block.objects.get(report=report).pk, block_id)
        with self.get(report_url + "/download/") as response:
            self.assertEqual(response.status, 200)
            data = response.read()
        self.assertEqual(data, bytes(report.data))
        self.assertIn("文件与链上存证一致", self.verify_file(report_url, data))
        self.assertIn("文件与链上存证不一致", self.verify_file(report_url, data + b"!"))

    def test_generated_report_omits_archived_cases_but_keeps_execution_history(self):
        prefix, version = self.create_project_version("归档与报告测试")
        self.record_execution(prefix, version, "当前登录用例", "passed")
        archived = self.record_execution(prefix, version, "旧登录用例", "failed")
        self.post(prefix + f"/cases/{archived.pk}/archive/", {})
        self.post(prefix + "/reports/generate/", {"title": "归档后的报告", "version": version.pk})
        report = Report.objects.get(version=version)
        with self.get(prefix + f"/reports/{report.pk}/download/") as response:
            self.assertEqual(response.status, 200)
            content = response.read().decode()
        self.assertIn("有效用例：1 | 通过：1 | 失败：0", content)
        self.assertIn("当前登录用例", content)
        self.assertNotIn("旧登录用例", content)
        with self.get(prefix + "/executions/") as response:
            self.assertIn("旧登录用例", response.read().decode())

    def test_report_generation_rejects_version_from_another_project(self):
        prefix, version = self.create_project_version("报告所属项目")
        _, foreign_version = self.create_project_version("另一个项目")
        with self.post(prefix + "/reports/generate/", {
            "title": "版本归属校验", "version": foreign_version.pk,
        }) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("errorlist", response.read().decode())
        self.assertFalse(Report.objects.filter(project=version.project).exists())
        self.assertFalse(Report.objects.filter(version=foreign_version).exists())
