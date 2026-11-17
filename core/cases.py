"""测试用例与执行。维护用例、导入用例、保存和查询每次执行结果。"""

import csv
import io

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Max, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .accounts import project_access
from .audit_log import audit
from .forms import CaseForm, CaseImportForm, ExecutionForm
from .models import Execution, Project, TestCase
from .views import form_page


@project_access()
@require_GET
def case_list(request, project):
    cases = project.cases.all()
    query = request.GET.get("q", "").strip()
    if request.GET.get("archived") != "1":
        cases = cases.filter(archived=False)
    if query:
        cases = cases.filter(Q(title__icontains=query) | Q(steps__icontains=query))
    priority = request.GET.get("priority", "")
    if priority:
        cases = cases.filter(priority=priority)
    return render(request, "cases.html", {
        "project": project, "page_obj": Paginator(cases, 20).get_page(request.GET.get("page")),
        "query": query, "priorities": TestCase.Priority.choices,
    })


@project_access(write=True)
@require_http_methods(["GET", "POST"])
def case_edit(request, project, case_id=None):
    case = get_object_or_404(project.cases, pk=case_id) if case_id else None
    form = CaseForm(request.POST or None, instance=case)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            updated = form.save(commit=False)
            updated.project = project
            if not case:
                updated.creator = request.user
            updated.save()
            audit(project, request.user, f"{'修改' if case else '新增'}用例 #{updated.pk}")
        return redirect("cases", project_id=project.pk)
    return form_page(request, form, "编辑测试用例" if case else "新增测试用例", project)


@project_access(write=True)
@require_POST
def case_archive(request, project, case_id):
    case = get_object_or_404(project.cases, pk=case_id)
    with transaction.atomic():
        case.archived = not case.archived
        case.save(update_fields=["archived"])
        audit(project, request.user, f"{'归档' if case.archived else '恢复'}用例 #{case.pk}")
    return redirect("cases", project_id=project.pk)


@project_access(write=True)
@require_http_methods(["GET", "POST"])
def case_import(request, project):
    form = CaseImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            count = import_cases(project, request.user, form.cleaned_data["file"].read())
            messages.success(request, f"成功导入 {count} 条用例。")
            return redirect("cases", project_id=project.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return render(request, "case_import.html", {"project": project, "form": form})


@project_access()
@require_GET
def case_template(request, project):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["title", "steps", "expected", "priority"])
    writer.writerow(["正确密码登录", "输入注册账号和正确密码后点击登录", "进入项目列表", "high"])
    writer.writerow(["错误密码登录", "输入正确账号和错误密码后点击登录", "提示账号或密码错误", "normal"])
    response = HttpResponse("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="testchain-cases-template.csv"'
    return response


@project_access(write=True)
@require_http_methods(["GET", "POST"])
def execute_case(request, project, case_id):
    case = get_object_or_404(project.cases, pk=case_id, archived=False)
    form = ExecutionForm(request.POST or None, project=project)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            execution = form.save(commit=False)
            execution.case = case
            execution.executor = request.user
            execution.case_snapshot = {"title": case.title, "steps": case.steps, "expected": case.expected, "priority": case.priority}
            execution.save()
            audit(project, request.user, f"执行用例 #{case.pk}：{execution.get_status_display()}")
        messages.success(request, "执行记录已保存")
        return redirect("executions", project_id=project.pk)
    return render(request, "execute.html", {"project": project, "case": case, "form": form})


@project_access()
@require_GET
def execution_list(request, project):
    rows = Execution.objects.filter(case__project=project).select_related("case", "version", "executor")
    for param in ["status", "version_id"]:
        value = request.GET.get(param)
        if value and (param != "version_id" or value.isdigit()):
            rows = rows.filter(**{param: value})
    return render(request, "executions.html", {
        "project": project, "page_obj": Paginator(rows, 20).get_page(request.GET.get("page")),
        "statuses": Execution.Status.choices,
    })


@project_access()
@require_GET
def export_csv(request, project):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="testchain-executions.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["用例编号", "用例标题", "版本", "结果", "实际结果", "执行人", "执行时间"])
    rows = Execution.objects.filter(case__project=project).select_related("case", "version", "executor")
    for row in rows.iterator():
        values = [row.case_id, row.case_snapshot.get("title", row.case.title), row.version.name, row.get_status_display(), row.actual, row.executor.username, row.created_at.isoformat()]
        writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value for value in values])
    return response


def import_cases(project, actor, data):
    if not 0 < len(data) <= 1024 * 1024:
        raise ValidationError("CSV 文件必须非空且不超过 1 MB。")
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")), strict=True)
        if reader.fieldnames != ["title", "steps", "expected", "priority"]:
            raise ValidationError("表头必须依次为 title,steps,expected,priority。")
        parsed = []
        titles = set()
        for line_number, row in enumerate(reader, 2):
            if line_number > 1001:
                raise ValidationError("每次最多导入 1000 条用例。")
            if None in row or any(value is None for value in row.values()):
                raise ValidationError(f"第 {line_number} 行列数不正确。")
            fields = {key: value.strip() for key, value in row.items()}
            if not all(fields.values()):
                raise ValidationError(f"第 {line_number} 行存在空字段。")
            if len(fields["title"]) > 200 or len(fields["steps"]) > 10000 or len(fields["expected"]) > 10000:
                raise ValidationError(f"第 {line_number} 行过长：标题最多 200 字，步骤和预期各最多 10000 字。")
            if fields["priority"] not in TestCase.Priority.values:
                raise ValidationError(f"第 {line_number} 行优先级应为 high、normal 或 low。")
            folded = fields["title"].casefold()
            if folded in titles:
                raise ValidationError(f"第 {line_number} 行用例标题重复。")
            titles.add(folded)
            parsed.append(fields)
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValidationError("无法读取 CSV，请使用 UTF-8 编码并检查引号。") from error
    if not parsed:
        raise ValidationError("CSV 只有表头，没有可导入的用例。")
    with transaction.atomic():
        locked = Project.objects.select_for_update().get(pk=project.pk)
        if locked.archived:
            raise ValidationError("项目已归档。")
        existing = {title.casefold() for title in project.cases.values_list("title", flat=True)}
        if existing & titles:
            raise ValidationError("文件中包含当前项目已有的用例标题，未导入任何数据。")
        TestCase.objects.bulk_create([TestCase(project=project, creator=actor, **fields) for fields in parsed])
        audit(project, actor, f"通过 CSV 批量导入 {len(parsed)} 条用例")
    return len(parsed)


def latest_executions(project, version=None):
    rows = Execution.objects.filter(case__project=project)
    if version:
        rows = rows.filter(version=version)
    latest_ids = rows.values("case_id").annotate(latest_id=Max("id")).values("latest_id")
    return Execution.objects.filter(id__in=latest_ids).select_related("case", "version", "executor")
