from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import Defect, Execution, Membership, Project, Report, TestCase, Version


class RegisterForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        fields = ("username", "email")


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "description"]


class VersionForm(forms.ModelForm):
    class Meta:
        model = Version
        fields = ["name", "description"]


class CaseForm(forms.ModelForm):
    class Meta:
        model = TestCase
        fields = ["title", "steps", "expected", "priority"]


class ExecutionForm(forms.ModelForm):
    class Meta:
        model = Execution
        fields = ["version", "status", "actual"]

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].queryset = project.versions.all()


class DefectForm(forms.ModelForm):
    class Meta:
        model = Defect
        fields = ["title", "description", "execution", "status", "assignee"]

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["execution"].queryset = Execution.objects.filter(case__project=project).select_related("case", "version")
        self.fields["execution"].label_from_instance = lambda execution: f"#{execution.pk} {execution.case.title} · {execution.version.name}"
        self.fields["assignee"].queryset = get_user_model().objects.filter(Q(pk=project.owner_id) | Q(memberships__project=project)).distinct()


class MemberForm(forms.Form):
    username = forms.CharField(label="成员用户名", max_length=150)
    role = forms.ChoiceField(label="项目角色", choices=Membership.Role.choices)

    def clean_username(self):
        username = self.cleaned_data["username"]
        if not get_user_model().objects.filter(username=username, is_active=True).exists():
            raise ValidationError("该用户尚未注册，或账号已停用。")
        return username


class ReportForm(forms.ModelForm):
    file = forms.FileField(label="报告文件", help_text="支持 PDF、Word、Excel、文本和 CSV，最大 10 MB。")

    class Meta:
        model = Report
        fields = ["title", "version"]

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].queryset = project.versions.all()

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.size or upload.size > settings.REPORT_MAX_BYTES:
            raise ValidationError("请选择非空文件，且文件大小不能超过 10 MB。")
        if Path(upload.name).suffix.lower() not in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".md", ".csv", ".json"}:
            raise ValidationError("此文件扩展名暂不支持。")
        return upload


class GenerateReportForm(forms.Form):
    title = forms.CharField(label="报告名称", max_length=160)
    version = forms.ModelChoiceField(label="项目版本", queryset=Version.objects.none())

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].queryset = project.versions.all()


class VerifyForm(forms.Form):
    file = forms.FileField(label="选择待验证文件")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.size or upload.size > settings.REPORT_MAX_BYTES:
            raise ValidationError("文件必须非空且不能超过 10 MB。")
        return upload


class CaseImportForm(forms.Form):
    file = forms.FileField(label="用例 CSV 文件", help_text="UTF-8 编码，最多 1000 行、1 MB。请先下载模板。")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if Path(upload.name).suffix.lower() != ".csv" or not 0 < upload.size <= 1024 * 1024:
            raise ValidationError("请选择非空 CSV 文件，大小不超过 1 MB。")
        return upload


class VersionCompareForm(forms.Form):
    baseline = forms.ModelChoiceField(label="基准版本", queryset=Version.objects.none())
    target = forms.ModelChoiceField(label="对比版本", queryset=Version.objects.none())

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.queryset = project.versions.all()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("baseline") and cleaned.get("baseline") == cleaned.get("target"):
            raise ValidationError("请选择两个不同的版本。")
        return cleaned
