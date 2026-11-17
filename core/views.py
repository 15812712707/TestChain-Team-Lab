"""通用页面辅助函数和运行状态检查。"""

from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET


def form_page(request, form, title, project=None):
    return render(request, "form.html", {"form": form, "title": title, "project": project})


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
