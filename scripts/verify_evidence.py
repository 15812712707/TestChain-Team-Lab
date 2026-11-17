import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.crypto import verify_evidence


def main():
    parser = argparse.ArgumentParser(description="离线核验 TestChain 凭证和报告文件，无需数据库或 Django")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("report", type=Path)
    arguments = parser.parse_args()
    try:
        valid, detail = verify_evidence(json.loads(arguments.evidence.read_text(encoding="utf-8")), arguments.report.read_bytes())
    except (OSError, ValueError) as error:
        print(f"读取失败：{error}", file=sys.stderr)
        return 2
    print(detail)
    if valid:
        print("请将区块哈希与独立可信记录核对；此结果不证明发行方身份或报告业务真实性。")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
