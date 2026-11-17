import argparse
import json
import platform
import statistics
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.crypto import canonical_hash, fingerprint


def mine(height, previous_hash, payload, timestamp, difficulty):
    nonce = 0
    while True:
        digest = canonical_hash(height, previous_hash, payload, timestamp, difficulty, nonce)
        if digest.startswith("0" * difficulty):
            return nonce, digest
        nonce += 1


def check_synthetic_chain(blocks):
    previous = "0" * 64
    for expected_height, block in enumerate(blocks):
        if block["height"] != expected_height or block["previous_hash"] != previous:
            return False
        digest = canonical_hash(block["height"], block["previous_hash"], block["payload"], block["timestamp"], block["difficulty"], block["nonce"])
        if digest != block["block_hash"] or not digest.startswith("0" * block["difficulty"]):
            return False
        previous = digest
    return True


def benchmark(repeats):
    timestamp = "2026-11-03T00:00:00+00:00"
    mining = []
    for difficulty in [1, 2, 3]:
        for sample in range(repeats):
            payload = {"sha256": fingerprint(f"synthetic-{sample}".encode()), "sample": sample}
            started = perf_counter()
            nonce, digest = mine(0, "0" * 64, payload, timestamp, difficulty)
            mining.append({"difficulty": difficulty, "sample": sample, "nonce": nonce, "hash": digest, "milliseconds": (perf_counter() - started) * 1000})
    hashing = []
    for size in [1024, 1024 * 1024, 10 * 1024 * 1024]:
        data = b"a" * size
        samples = []
        for _ in range(repeats):
            started = perf_counter()
            fingerprint(data)
            samples.append((perf_counter() - started) * 1000)
        hashing.append({"bytes": size, "mean_ms": statistics.mean(samples), "samples_ms": samples})
    blocks = []
    previous = "0" * 64
    for height in range(500):
        payload = {"sha256": fingerprint(f"synthetic-block-{height}".encode())}
        nonce, digest = mine(height, previous, payload, timestamp, 1)
        blocks.append({"height": height, "previous_hash": previous, "payload": payload, "timestamp": timestamp, "difficulty": 1, "nonce": nonce, "block_hash": digest})
        previous = digest
    verification = []
    for length in [10, 100, 500]:
        samples = []
        for _ in range(repeats):
            started = perf_counter()
            assert check_synthetic_chain(blocks[:length])
            samples.append((perf_counter() - started) * 1000)
        verification.append({"blocks": length, "mean_ms": statistics.mean(samples), "samples_ms": samples})
    blocks[10]["payload"]["sha256"] = "f" * 64
    return {
        "environment": {"python": platform.python_version(), "system": platform.system(), "machine": platform.machine()},
        "repeats": repeats, "pow_samples": mining, "file_hashing": hashing, "chain_verification": verification,
        "tamper_detected": not check_synthetic_chain(blocks),
        "scope": "合成数据的算法微基准；链长实验难度为 1，业务系统难度为 3；不写业务库，不代表 Web 并发吞吐或真实用户负载。",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TestChain 可复现算法实验，不写数据库")
    parser.add_argument("--repeats", type=int, default=10, choices=range(3, 51), metavar="3..50")
    parser.add_argument("--output", type=Path, default=Path("experiments/results/benchmark.json"))
    arguments = parser.parse_args()
    result = benchmark(arguments.repeats)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"实验完成：{arguments.output}；篡改检测={result['tamper_detected']}")
