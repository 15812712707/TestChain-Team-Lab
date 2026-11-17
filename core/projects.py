"""项目与版本。管理项目资料、版本、归档和成员。"""

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .accounts import accessible_projects, project_access
from .audit_log import audit
from .forms import MemberForm, ProjectForm, VersionForm
from .models import Membership, Project
from .reports import statistics
from .views import form_page


@login_required
@require_GET
def dashboard(request):
    projects = accessible_projects(request.user).select_related("owner")
    query = request.GET.get("q", "").strip()
    if query:
        projects = projects.filter(Q(name__icontains=query) | Q(description__icontains=query))
    page = Paginator(projects, 12).get_page(request.GET.get("page"))
    return render(request, "dashboard.html", {"page_obj": page, "query": query})


@login_required
@require_http_methods(["GET", "POST"])
def project_create(request):
    form = ProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            project = form.save(commit=False)
            project.owner = request.user
            project.save()
            audit(project, request.user, "创建项目")
        return redirect("project", project_id=project.pk)
    return form_page(request, form, "创建项目")


@project_access()
@require_GET
def project_detail(request, project):
    version_id = request.GET.get("version", "")
    version = get_object_or_404(project.versions, pk=version_id) if version_id.isdigit() else None
    return render(request, "project.html", {
        "project": project, "stats": statistics(project, version), "version": version,
        "versions": project.versions.all(), "recent": project.events.select_related("actor")[:10],
        "open_defects": project.defects.exclude(status="closed").count(),
        "report_count": project.reports.count(),
    })


@project_access(write=True, manager=True)
@require_http_methods(["GET", "POST"])
def project_edit(request, project):
    form = ProjectForm(request.POST or None, instance=project)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            audit(project, request.user, "修改项目信息")
        return redirect("project", project_id=project.pk)
    return form_page(request, form, "编辑项目", project)


@project_access(write=True, manager=True, allow_archived=True)
@require_POST
def project_archive(request, project):
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project.pk)
        project.archived = not project.archived
        project.save(update_fields=["archived"])
        audit(project, request.user, "归档项目" if project.archived else "恢复项目")
    return redirect("project", project_id=project.pk)


@project_access(write=True)
@require_http_methods(["GET", "POST"])
def version_create(request, project):
    form = VersionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if project.versions.filter(name=form.cleaned_data["name"]).exists():
            form.add_error("name", "当前项目已有同名版本。")
        else:
            with transaction.atomic():
                version = form.save(commit=False)
                version.project = project
                version.save()
                audit(project, request.user, f"新增版本 {version.name}")
            return redirect("project", project_id=project.pk)
    return form_page(request, form, "新增版本", project)


@project_access(write=True, manager=True)
@require_http_methods(["GET", "POST"])
def members(request, project):
    form = MemberForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = get_user_model().objects.get(username=form.cleaned_data["username"])
        if user.pk == project.owner_id:
            form.add_error("username", "项目负责人已拥有管理权限。")
        else:
            with transaction.atomic():
                Membership.objects.update_or_create(project=project, user=user, defaults={"role": form.cleaned_data["role"]})
                audit(project, request.user, f"设置成员 {user.username} 为 {form.cleaned_data['role']}")
            return redirect("members", project_id=project.pk)
    return render(request, "members.html", {
        "project": project, "form": form, "members": project.memberships.select_related("user"),
    })


@project_access(write=True, manager=True)
@require_POST
def member_remove(request, project, member_id):
    member = get_object_or_404(project.memberships, pk=member_id)
    username = member.user.username
    with transaction.atomic():
        member.delete()
        audit(project, request.user, f"移除成员 {username}")
    return redirect("members", project_id=project.pk)


@project_access()
@require_GET
def project_api(request, project):
    return JsonResponse({
        "id": project.pk, "name": project.name, "archived": project.archived,
        "statistics": statistics(project),
        "versions": list(project.versions.values("id", "name")),
    }, json_dumps_params={"ensure_ascii": False})
