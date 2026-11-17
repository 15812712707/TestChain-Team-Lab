"""保留新增质量功能原有的页面导入路径。"""

from .attestation import report_evidence
from .cases import case_import, case_template
from .reports import version_compare

__all__ = ["case_import", "case_template", "report_evidence", "version_compare"]
