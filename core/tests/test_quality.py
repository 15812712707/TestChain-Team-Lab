import copy
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from core.crypto import verify_evidence
from core.models import Execution, Membership, Project, TestCase as Case, Version
from core.quality import compare_versions, evidence_bundle, import_cases
from core.services import anchor_report, save_report


class QualityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("quality-owner")
        cls.viewer = get_user_model().objects.create_user("quality-viewer")
        cls.outsider = get_user_model().objects.create_user("quality-outsider")
        cls.project = Project.objects.create(name="质量管理", owner=cls.owner)
        cls.other = Project.objects.create(name="其他项目", owner=cls.outsider)
        cls.before = Version.objects.create(project=cls.project, name="v1")
        cls.after = Version.objects.create(project=cls.project, name="v2")
        cls.foreign = Version.objects.create(project=cls.other, name="private")
        Membership.objects.create(project=cls.project, user=cls.viewer, role="viewer")

    def setUp(self):
        self.client.force_login(self.owner)

    def url(self, name, *args):
        return reverse(name, args=[self.project.pk, *args])

    def csv(self, title="导入用例"):
        return f'title,steps,expected,priority\r\n{title},"步骤一\n步骤二",成功,high\r\n'.encode("utf-8-sig")

    def make_report(self):
        report = save_report(self.project, self.before, "报告", "报告.txt", b"evidence-content", self.owner)
        anchor_report(report.pk, self.owner)
        return report

    def test_csv_bom_and_quoted_newlines(self):
        self.assertEqual(import_cases(self.project, self.owner, self.csv()), 1)
        self.assertEqual(self.project.cases.get().steps, "步骤一\n步骤二")
        self.assertTrue(self.project.events.filter(action__contains="批量导入 1").exists())

    def test_import_rejects_wrong_headers(self):
        with self.assertRaises(ValidationError):
            import_cases(self.project, self.owner, b"wrong,headers\nvalue,value")

    def test_import_invalid_later_row_saves_nothing(self):
        data = self.csv() + "第二条,步骤,预期,invalid\n".encode()
        with self.assertRaises(ValidationError):
            import_cases(self.project, self.owner, data)
        self.assertFalse(self.project.cases.exists())

    def test_import_rejects_existing_title(self):
        import_cases(self.project, self.owner, self.csv())
        with self.assertRaises(ValidationError):
            import_cases(self.project, self.owner, self.csv())
        self.assertEqual(self.project.cases.count(), 1)

    def test_import_rejects_duplicates_in_same_file(self):
        with self.assertRaises(ValidationError):
            import_cases(self.project, self.owner, self.csv("Alpha") + b"alpha,steps,expected,normal\n")

    def test_import_limits_rows(self):
        data = b"title,steps,expected,priority\n" + b"".join(f"case-{index},steps,expected,normal\n".encode() for index in range(1001))
        with self.assertRaises(ValidationError):
            import_cases(self.project, self.owner, data)
        self.assertFalse(self.project.cases.exists())

    def test_import_rejects_invalid_encoding(self):
        with self.assertRaises(ValidationError):
            import_cases(self.project, self.owner, b"\xff\xfeinvalid")

    def test_import_rejects_archived_project(self):
        self.project.archived = True
        self.project.save()
        with self.assertRaises(ValidationError):
            import_cases(self.project, self.owner, self.csv())

    def test_import_http_flow(self):
        response = self.client.post(self.url("case_import"), {"file": SimpleUploadedFile("cases.csv", self.csv())})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.project.cases.count(), 1)

    def test_viewer_cannot_import_but_can_download_template(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(self.url("case_import")).status_code, 403)
        self.assertEqual(self.client.get(self.url("case_template")).status_code, 200)

    def test_outsider_cannot_use_new_routes(self):
        report = self.make_report()
        self.client.force_login(self.outsider)
        for name, arguments in [("case_template", []), ("version_compare", []), ("report_evidence", [report.pk])]:
            self.assertEqual(self.client.get(self.url(name, *arguments)).status_code, 404)

    def test_compare_regression_improvement_pending_and_latest(self):
        for index, statuses in enumerate([("passed", "failed"), ("failed", "passed"), (None, "passed"), ("passed", None)]):
            case = Case.objects.create(project=self.project, title=str(index), steps="步骤", expected="结果", creator=self.owner)
            for version, status in zip([self.before, self.after], statuses):
                if status:
                    Execution.objects.create(case=case, version=version, status=status, actual="观察结果", executor=self.owner)
        rows, counts = compare_versions(self.project, self.before, self.after)
        self.assertEqual(counts, {"regressed": 1, "improved": 1, "changed": 2, "unchanged": 0})
        self.assertEqual(rows[-1]["after"], "未执行")
        Execution.objects.create(case=rows[0]["case"], version=self.after, status="passed", actual="修复", executor=self.owner)
        self.assertEqual(compare_versions(self.project, self.before, self.after)[1]["regressed"], 0)

    def test_compare_rejects_foreign_version(self):
        response = self.client.get(self.url("version_compare"), {"baseline": self.foreign.pk, "target": self.after.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["counts"])

    def test_compare_rejects_same_version(self):
        response = self.client.get(self.url("version_compare"), {"baseline": self.before.pk, "target": self.before.pk})
        self.assertContains(response, "两个不同的版本")

    def test_compare_page_renders_results(self):
        response = self.client.get(self.url("version_compare"), {"baseline": self.before.pk, "target": self.after.pk})
        self.assertContains(response, "回归风险")

    def test_evidence_download_and_offline_verification(self):
        report = self.make_report()
        response = self.client.get(self.url("report_evidence", report.pk))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(verify_evidence(response.json(), bytes(report.data))[0])
        self.assertFalse(verify_evidence(response.json(), b"changed")[0])
        self.assertNotIn("data", response.json())

    def test_evidence_rejects_modified_nonce_and_metadata(self):
        document = evidence_bundle(self.make_report())
        for field, value in [("nonce", document["block"]["nonce"] + 1), ("difficulty", 0), ("height", True)]:
            changed = copy.deepcopy(document)
            changed["block"][field] = value
            self.assertFalse(verify_evidence(changed, b"evidence-content")[0])
        self.assertFalse(verify_evidence({}, b"evidence-content")[0])

    def test_unanchored_report_has_no_evidence(self):
        report = save_report(self.project, self.before, "未存证", "report.txt", b"original", self.owner)
        self.assertEqual(self.client.get(self.url("report_evidence", report.pk)).status_code, 404)

    def test_evidence_export_rejects_corrupt_chain(self):
        report = self.make_report()
        report.block.__class__.objects.filter(report=report).update(block_hash="f" * 64)
        self.assertEqual(self.client.get(self.url("report_evidence", report.pk)).status_code, 302)

    def test_offline_script_works_without_django(self):
        document = evidence_bundle(self.make_report())
        with TemporaryDirectory() as directory:
            proof = Path(directory) / "proof.json"
            data = Path(directory) / "report.txt"
            proof.write_text(json.dumps(document))
            data.write_bytes(b"evidence-content")
            result = subprocess.run([sys.executable, "-S", str(settings.BASE_DIR / "scripts/verify_evidence.py"), str(proof), str(data)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
