"""缺陷管理。登记测试发现的问题，并更新处理人和状态。"""

from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from .accounts import project_access
from .audit_log import audit
from .forms import DefectForm
from .models import Defect
from .views import form_page


@project_access()
@require_GET
def defect_list(request, project):
    defects = project.defects.select_related("assignee", "execution")
    if request.GET.get("status"):
        defects = defects.filter(status=request.GET["status"])
    return render(request, "defects.html", {
        "project": project, "page_obj": Paginator(defects, 20).get_page(request.GET.get("page")),
        "statuses": Defect.Status.choices,
    })


@project_access(write=True)
@require_http_methods(["GET", "POST"])
def defect_edit(request, project, defect_id=None):
    defect = get_object_or_404(project.defects, pk=defect_id) if defect_id else None
    form = DefectForm(request.POST or None, instance=defect, project=project)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            updated = form.save(commit=False)
            updated.project = project
            if not defect:
                updated.creator = request.user
            updated.save()
            audit(project, request.user, f"更新缺陷 #{updated.pk}：{updated.get_status_display()}")
        return redirect("defects", project_id=project.pk)
    return form_page(request, form, "编辑缺陷" if defect else "登记缺陷", project)
