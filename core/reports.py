"""统计与报告。比较版本结果，生成、上传和下载测试报告。"""

import io
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .accounts import project_access
from .audit_log import audit
from .cases import latest_executions
from .crypto import fingerprint
from .forms import GenerateReportForm, ReportForm, VerifyForm, VersionCompareForm
from .models import Project, Report
from .views import form_page


@project_access()
@require_GET
def report_list(request, project):
    reports = project.reports.select_related("version", "creator", "block")
    return render(request, "reports.html", {
        "project": project, "page_obj": Paginator(reports, 20).get_page(request.GET.get("page")),
    })


@project_access(write=True)
@require_http_methods(["GET", "POST"])
def report_create(request, project, generate=False):
    form_class = GenerateReportForm if generate else ReportForm
    form = form_class(request.POST or None, request.FILES or None, project=project)
    if request.method == "POST" and form.is_valid():
        title = form.cleaned_data["title"]
        version = form.cleaned_data["version"]
        data = generated_report(project, version, title) if generate else form.cleaned_data["file"].read()
        filename = "test-report.txt" if generate else form.cleaned_data["file"].name
        try:
            report = save_report(project, version, title, filename, data, request.user, generate)
            return redirect("report", project_id=project.pk, report_id=report.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return form_page(request, form, "生成测试报告" if generate else "上传测试报告", project)


@project_access()
@require_GET
def report_detail(request, project, report_id):
    report = get_object_or_404(project.reports.select_related("version", "creator", "block"), pk=report_id)
    return render(request, "report.html", {"project": project, "report": report, "form": VerifyForm()})


@project_access()
@require_GET
def report_download(request, project, report_id):
    report = get_object_or_404(project.reports, pk=report_id)
    response = FileResponse(io.BytesIO(bytes(report.data)), as_attachment=True, filename=report.filename, content_type="application/octet-stream")
    response["Cache-Control"] = "private, no-store"
    return response


@project_access()
@require_GET
def version_compare(request, project):
    form = VersionCompareForm(request.GET or None, project=project)
    rows, counts = [], None
    if form.is_bound and form.is_valid():
        rows, counts = compare_versions(project, form.cleaned_data["baseline"], form.cleaned_data["target"])
    return render(request, "version_compare.html", {
        "project": project, "form": form, "counts": counts,
        "page_obj": Paginator(rows, 30).get_page(request.GET.get("page")),
    })


def statistics(project, version=None):
    latest = list(latest_executions(project, version))
    active_ids = set(project.cases.filter(archived=False).values_list("pk", flat=True))
    latest = [execution for execution in latest if execution.case_id in active_ids]
    total = len(active_ids)
    passed = sum(execution.status == "passed" for execution in latest)
    return {
        "total": total, "executed": len(latest), "passed": passed,
        "failed": sum(execution.status == "failed" for execution in latest),
        "blocked": sum(execution.status == "blocked" for execution in latest),
        "pending": total - len(latest), "rate": round(passed * 100 / total) if total else 0,
    }


def compare_versions(project, baseline, target):
    if baseline.project_id != project.pk or target.project_id != project.pk:
        raise ValidationError("版本不属于当前项目。")
    before = {execution.case_id: execution for execution in latest_executions(project, baseline)}
    after = {execution.case_id: execution for execution in latest_executions(project, target)}
    rows = []
    counts = {"regressed": 0, "improved": 0, "changed": 0, "unchanged": 0}
    labels = {"passed": "通过", "failed": "失败", "blocked": "阻塞", "pending": "未执行"}
    for case in project.cases.filter(archived=False).order_by("id"):
        old = before[case.pk].status if case.pk in before else "pending"
        new = after[case.pk].status if case.pk in after else "pending"
        if old == "passed" and new in {"failed", "blocked"}:
            category, label = "regressed", "回归风险"
        elif old in {"failed", "blocked"} and new == "passed":
            category, label = "improved", "已改善"
        elif old != new:
            category, label = "changed", "状态变化"
        else:
            category, label = "unchanged", "无变化"
        counts[category] += 1
        rows.append({"case": case, "before": labels[old], "after": labels[new], "category": category, "label": label})
    return rows, counts


def save_report(project, version, title, filename, data, actor, generated=False):
    if version.project_id != project.pk:
        raise ValidationError("报告版本不属于当前项目。")
    if not data or len(data) > settings.REPORT_MAX_BYTES:
        raise ValidationError("报告文件必须非空且不能超过 10 MB。")
    with transaction.atomic():
        locked_project = Project.objects.select_for_update().get(pk=project.pk)
        if locked_project.archived:
            raise ValidationError("项目已归档。")
        report = Report.objects.create(
            project=project, version=version, title=title,
            filename=Path(filename.replace("\\", "/")).name[:200], data=data,
            sha256=fingerprint(data), size=len(data), creator=actor, generated=generated,
        )
        audit(project, actor, f"{'生成' if generated else '上传'}报告 #{report.pk}：{title}")
        return report


def generated_report(project, version, title):
    stats = statistics(project, version)
    lines = [
        title, f"项目：{project.name}", f"版本：{version.name}",
        f"生成时间：{timezone.localtime().isoformat()}",
        f"有效用例：{stats['total']} | 通过：{stats['passed']} | 失败：{stats['failed']} | 阻塞：{stats['blocked']} | 未执行：{stats['pending']}",
        "", "执行记录（每个有效用例在该版本的最近一次执行）：",
    ]
    for execution in latest_executions(project, version).filter(case__archived=False).order_by("case_id"):
        snapshot = execution.case_snapshot
        lines.extend([
            "", f"用例 #{execution.case_id}：{snapshot.get('title', execution.case.title)}",
            f"步骤：{snapshot.get('steps', '')}", f"预期：{snapshot.get('expected', '')}",
            f"结果：{execution.get_status_display()}", f"实际：{execution.actual}",
            f"执行人：{execution.executor.username} | 时间：{timezone.localtime(execution.created_at).isoformat()}",
        ])
    lines.extend(["", "缺陷记录："])
    for defect in project.defects.filter(execution__version=version).select_related("assignee"):
        lines.append(f"#{defect.pk} {defect.title} | {defect.get_status_display()} | {defect.description}")
    return "\n".join(lines).encode("utf-8")
