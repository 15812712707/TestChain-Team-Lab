"""Start TestChain with an isolated, prefilled database for classroom rehearsal."""

import argparse
import os
from pathlib import Path
import secrets
import sys


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".classroom"


def prepare_example(password):
    from django.contrib.auth import get_user_model
    from django.db import transaction

    from core.models import Defect, Execution, Project, TestCase, Version
    from core.reports import generated_report, save_report

    with transaction.atomic():
        owner, _ = get_user_model().objects.get_or_create(username="classroom")
        owner.set_password(password)
        owner.save(update_fields=["password"])
        project, created = Project.objects.get_or_create(
            owner=owner, name="校园选课系统（课堂样例）",
            defaults={"description": "自建课程数据，用于演示版本测试和报告核验。"},
        )
        if created:
            baseline = Version.objects.create(project=project, name="v1.0")
            target = Version.objects.create(project=project, name="v1.1")
            snapshot = {
                "title": "查看已选课程", "steps": "登录后打开我的课程",
                "expected": "显示已选课程和上课时间", "priority": TestCase.Priority.HIGH,
            }
            case = TestCase.objects.create(project=project, creator=owner, **snapshot)
            Execution.objects.create(
                case=case, version=baseline, executor=owner, case_snapshot=snapshot,
                status=Execution.Status.PASSED, actual="课程和时间显示正常。",
            )
            failure = Execution.objects.create(
                case=case, version=target, executor=owner, case_snapshot=snapshot,
                status=Execution.Status.FAILED, actual="课程显示正常，上课时间为空。",
            )
            Defect.objects.create(
                project=project, execution=failure, creator=owner, assignee=owner,
                title="已选课程缺少上课时间", description="v1.1 打开我的课程后，上课时间一栏为空。",
            )
            title = "课堂样例报告"
            report = save_report(
                project, target, title, "classroom-report.txt",
                generated_report(project, target, title), owner, generated=True,
            )
        else:
            report = project.reports.get(filename="classroom-report.txt")
    return project, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    DATA.mkdir(exist_ok=True)
    sys.path.insert(0, str(ROOT))
    os.environ.update(
        DJANGO_SETTINGS_MODULE="config.settings",
        DB_ENGINE="sqlite",
        SQLITE_PATH=str(DATA / "db.sqlite3"),
        DEBUG="true",
        ALLOWED_HOSTS="127.0.0.1",
        SECRET_KEY=secrets.token_urlsafe(48),
        COOKIE_SECURE="false",
        SECURE_SSL_REDIRECT="false",
    )

    import django
    from django.conf import settings
    from django.core.management import call_command

    settings.SESSION_COOKIE_NAME = "testchain_classroom_session"
    settings.CSRF_COOKIE_NAME = "testchain_classroom_csrf"
    django.setup()
    call_command("migrate", interactive=False, verbosity=0)
    password = secrets.token_urlsafe(18)
    project, report = prepare_example(password)
    original = bytes(report.data)
    (DATA / "原报告.txt").write_bytes(original)
    (DATA / "改动后的报告.txt").write_bytes(original + "\n此处新增一行。\n".encode("utf-8"))
    site = f"http://127.0.0.1:{args.port}"
    print(f"课堂网站 {site}/  ·  账号 classroom  ·  本次密码 {password}", flush=True)
    print(f"样例报告 {site}/projects/{project.pk}/reports/{report.pk}/", flush=True)
    print(f"原件和修改件位于 {DATA}", flush=True)
    call_command("runserver", f"127.0.0.1:{args.port}", use_reloader=False)


if __name__ == "__main__":
    main()
