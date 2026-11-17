import hashlib
import json


def fingerprint(data):
    return hashlib.sha256(data).hexdigest()


def canonical_hash(height, previous_hash, payload, timestamp, difficulty, nonce):
    document = {
        "height": height,
        "previous_hash": previous_hash,
        "payload": payload,
        "timestamp": timestamp,
        "difficulty": difficulty,
        "nonce": nonce,
    }
    serialized = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return fingerprint(serialized.encode("utf-8"))


def mine_block(height, previous_hash, payload, timestamp, difficulty):
    """返回满足前导零要求的第一个 nonce 及其区块哈希。"""
    if type(difficulty) is not int or not 0 <= difficulty <= 64:
        raise ValueError("难度必须是 0 到 64 之间的整数。")

    prefix = "0" * difficulty
    nonce = 0
    while True:
        block_hash = canonical_hash(
            height, previous_hash, payload, timestamp, difficulty, nonce
        )
        if block_hash.startswith(prefix):
            return nonce, block_hash
        nonce += 1


def verify_evidence(document, data):
    try:
        if document["schema"] != "testchain-evidence-v1":
            return False, "不支持的凭证格式"
        block = document["block"]
        for field in ["height", "nonce", "difficulty"]:
            if type(block[field]) is not int or block[field] < 0:
                return False, "区块数字字段无效"
        if block["difficulty"] != 3:
            return False, "工作量证明难度不符合当前协议"
        for digest in [block["previous_hash"], block["block_hash"], block["payload"]["sha256"]]:
            if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                return False, "哈希字段无效"
        expected = canonical_hash(block["height"], block["previous_hash"], block["payload"], block["timestamp"], block["difficulty"], block["nonce"])
        if expected != block["block_hash"] or not expected.startswith("0" * block["difficulty"]):
            return False, "凭证区块哈希或工作量证明不正确"
        if fingerprint(data) != block["payload"]["sha256"] or len(data) != block["payload"]["size"]:
            return False, "文件与凭证记录不一致"
        return True, "文件与凭证一致，区块哈希及 PoW 校验通过"
    except (KeyError, TypeError, ValueError):
        return False, "凭证字段缺失或格式错误"
