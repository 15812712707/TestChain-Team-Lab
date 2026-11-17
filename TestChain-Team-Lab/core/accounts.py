"""用户与权限。处理注册、登录、密码修改和项目访问权限。"""

from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from .crypto import fingerprint
from .forms import RegisterForm
from .models import LoginAttempt, Project
from .views import form_page


@require_http_methods(["GET", "POST"])
def sign_in(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST":
        key = fingerprint(request.POST.get("username", "").strip().encode())
        attempt, _ = LoginAttempt.objects.get_or_create(key=key)
        if attempt.updated_at < timezone.now() - timedelta(minutes=15):
            attempt.failures = 0
            attempt.save()
        if attempt.failures >= 5:
            form.add_error(None, "登录失败次数过多，请 15 分钟后重试。")
        elif form.is_valid():
            LoginAttempt.objects.filter(pk=attempt.pk).delete()
            login(request, form.get_user())
            destination = request.POST.get("next", "")
            if not url_has_allowed_host_and_scheme(destination, {request.get_host()}, require_https=request.is_secure()):
                destination = reverse("dashboard")
            return redirect(destination or "dashboard")
        else:
            LoginAttempt.objects.filter(pk=attempt.pk).update(failures=F("failures") + 1, updated_at=timezone.now())
    return render(request, "registration/login.html", {"form": form, "registration_open": settings.ALLOW_REGISTRATION})


@require_http_methods(["GET", "POST"])
def register(request):
    if not settings.ALLOW_REGISTRATION:
        raise PermissionDenied("当前未开放注册，请联系管理员。")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("dashboard")
    return form_page(request, form, "注册账号")


@login_required
@require_http_methods(["GET", "POST"])
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "密码已修改。")
        return redirect("dashboard")
    return form_page(request, form, "修改密码")


def accessible_projects(user):
    return Project.objects.filter(Q(owner=user) | Q(memberships__user=user)).distinct()


def project_access(write=False, manager=False, allow_archived=False):
    def decorate(view):
        @login_required
        @wraps(view)
        def wrapped(request, project_id, *args, **kwargs):
            project = get_object_or_404(accessible_projects(request.user), pk=project_id)
            membership = project.memberships.filter(user=request.user).first()
            role = "manager" if project.owner_id == request.user.pk else membership.role
            if manager and role != "manager" or write and role not in {"manager", "tester"}:
                raise PermissionDenied("当前账号没有操作权限。")
            if write and project.archived and not allow_archived:
                messages.error(request, "项目已归档，恢复项目后才能修改。")
                return redirect("project", project_id=project.pk)
            request.project_role = role
            return view(request, project, *args, **kwargs)
        return wrapped
    return decorate
