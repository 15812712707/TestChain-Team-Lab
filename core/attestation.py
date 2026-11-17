"""实验6核心模块：报告存证与核验。保存存证、比较文件并导出凭证。"""

import time

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .accounts import project_access
from .audit_log import audit
from .crypto import canonical_hash, fingerprint, mine_block
from .forms import VerifyForm
from .models import Block, ChainState, Report


@project_access(write=True)
@require_POST
def report_anchor(request, project, report_id):
    report = get_object_or_404(project.reports, pk=report_id)
    try:
        block = anchor_report(report.pk, request.user)
        messages.success(request, f"存证完成，区块高度 {block.height}。")
    except ValidationError as error:
        messages.error(request, "；".join(error.messages))
    return redirect("report", project_id=project.pk, report_id=report.pk)


@project_access()
@require_POST
def report_verify(request, project, report_id):
    report = get_object_or_404(project.reports.select_related("version", "block"), pk=report_id)
    form = VerifyForm(request.POST, request.FILES)
    result = None
    if form.is_valid():
        digest = fingerprint(form.cleaned_data["file"].read())
        block = Block.objects.filter(report=report).first()
        if block:
            valid, detail = verify_chain()
            if not valid:
                result = {"ok": False, "message": f"链完整性验证失败：{detail}", "hash": digest}
            else:
                match = digest == block.payload["sha256"]
                result = {"ok": match, "message": "文件与链上存证一致" if match else "文件与链上存证不一致", "hash": digest}
        else:
            match = digest == report.sha256
            result = {"ok": match, "message": "文件与上传版本一致（尚未存证）" if match else "文件与上传版本不一致", "hash": digest}
        audit(project, request.user, f"验证报告 #{report.pk}：{result['message']}")
    return render(request, "report.html", {"project": project, "report": report, "form": form, "result": result})


@project_access()
@require_GET
def report_evidence(request, project, report_id):
    report = get_object_or_404(project.reports.select_related("block"), pk=report_id, block__isnull=False)
    try:
        document = evidence_bundle(report)
    except ValidationError as error:
        messages.error(request, "；".join(error.messages))
        return redirect("report", project_id=project.pk, report_id=report.pk)
    audit(project, request.user, f"导出报告 #{report.pk} 的存证凭证")
    response = JsonResponse(document, json_dumps_params={"ensure_ascii": False, "indent": 2})
    response["Content-Disposition"] = f'attachment; filename="testchain-evidence-{report.pk}.json"'
    response["Cache-Control"] = "private, no-store"
    return response


@project_access()
@require_GET
def chain_browser(request, project):
    blocks = Block.objects.filter(report__project=project).select_related("report").order_by("-height")
    return render(request, "chain.html", {"project": project, "page_obj": Paginator(blocks, 10).get_page(request.GET.get("page"))})


@project_access()
@require_POST
def chain_check(request, project):
    valid, detail = verify_chain()
    audit(project, request.user, f"验证链完整性：{'通过' if valid else '失败'}")
    (messages.success if valid else messages.error)(request, detail)
    return redirect("chain", project_id=project.pk)


def anchor_report(report_id, actor):
    for attempt in range(5):
        try:
            with transaction.atomic():
                state = ChainState.objects.select_for_update().get(pk=1)
                ChainState.objects.filter(pk=1).update(next_height=F("next_height") + 0)
                report = Report.objects.select_for_update().select_related("project").get(pk=report_id)
                if report.project.archived:
                    raise ValidationError("项目已归档，不能继续存证。")
                existing = Block.objects.filter(report=report).first()
                if existing:
                    return existing
                valid, detail = verify_chain()
                if not valid:
                    raise ValidationError(f"存证中止：{detail}")
                if fingerprint(bytes(report.data)) != report.sha256:
                    raise ValidationError("报告文件与保存的哈希不一致，不能存证。")
                payload = report_payload(report)
                timestamp = timezone.now().isoformat()
                difficulty = settings.CHAIN_DIFFICULTY
                nonce, block_hash = mine_block(
                    state.next_height, state.tip_hash, payload, timestamp, difficulty
                )
                block = Block.objects.create(
                    height=state.next_height, report=report, payload=payload,
                    previous_hash=state.tip_hash, block_hash=block_hash, timestamp=timestamp,
                    difficulty=difficulty, nonce=nonce,
                )
                state.tip_hash = block_hash
                state.next_height += 1
                state.save(update_fields=["tip_hash", "next_height"])
                audit(report.project, actor, f"报告 #{report.pk} 完成存证，区块 #{block.height}")
                return block
        except OperationalError as error:
            if not any(marker in str(error).lower() for marker in ["locked", "deadlock", "1213", "1205"]):
                raise
            if attempt == 4:
                raise ValidationError("当前存证请求较多，请稍后重试。") from error
            time.sleep(0.05 * (attempt + 1))


@transaction.atomic
def verify_chain():
    state = ChainState.objects.select_for_update().get(pk=1)
    previous_hash = "0" * 64
    count = 0
    for expected_height, block in enumerate(Block.objects.select_related("report").order_by("height")):
        count += 1
        if block.height != expected_height or block.previous_hash != previous_hash:
            return False, f"区块 #{block.height} 的顺序或前序哈希不正确"
        if block.difficulty != settings.CHAIN_DIFFICULTY:
            return False, f"区块 #{block.height} 的难度不正确"
        actual = canonical_hash(block.height, block.previous_hash, block.payload, block.timestamp, block.difficulty, block.nonce)
        if actual != block.block_hash or not actual.startswith("0" * block.difficulty):
            return False, f"区块 #{block.height} 的哈希或工作量证明不正确"
        if block.payload != report_payload(block.report):
            return False, f"区块 #{block.height} 的报告元数据不匹配"
        if fingerprint(bytes(block.report.data)) != block.payload["sha256"]:
            return False, f"区块 #{block.height} 对应的报告文件已改变"
        previous_hash = block.block_hash
    if state.tip_hash != previous_hash or state.next_height != count:
        return False, "链头记录与实际区块不一致"
    return True, f"验证通过，共 {count} 个区块"


def report_payload(report):
    return {
        "report_id": report.pk, "project_id": report.project_id, "version_id": report.version_id,
        "sha256": report.sha256, "size": report.size, "filename": report.filename,
        "creator_id": report.creator_id, "created_at": report.created_at.isoformat(timespec="milliseconds"),
    }


@transaction.atomic
def evidence_bundle(report):
    valid, detail = verify_chain()
    if not valid:
        raise ValidationError(f"凭证导出中止：{detail}")
    block = report.block
    return {
        "schema": "testchain-evidence-v1", "exported_at": timezone.now().isoformat(),
        "report_title": report.title,
        "block": {field: getattr(block, field) for field in [
            "height", "previous_hash", "payload", "timestamp", "difficulty", "nonce", "block_hash",
        ]},
        "notice": "仅用于文件与单区块一致性校验；须与独立可信记录核对区块哈希，不提供发行方签名或整链共识证明。",
    }
