"""操作日志。记录操作人、项目和操作内容，供项目成员查看。"""

from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .accounts import project_access
from .models import AuditEvent


def audit(project, actor, action):
    AuditEvent.objects.create(project=project, actor=actor, action=action)


@project_access()
@require_GET
def events(request, project):
    rows = project.events.select_related("actor")
    return render(request, "events.html", {"project": project, "page_obj": Paginator(rows, 30).get_page(request.GET.get("page"))})
