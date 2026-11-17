from unittest import TestCase
from unittest.mock import patch

from core.crypto import canonical_hash, fingerprint, mine_block, verify_evidence


REPORT_BYTES = b"Test report\n"
FILE_HASH = "b299e22659a245c33c528e663768fa8f6ef0239e4b4ab886b3c4e8b208aeb0c5"
BLOCK_HASH = "000741be9265bc7e89eeba9f64e71e76172536c9575396f8908895be340c2fda"
NONCE_ZERO_HASH = "1f0464e12a9893efb94da3398f54068bc70e2de90def593d1cfea30713ad4e9e"


class CryptoTests(TestCase):
    def setUp(self):
        self.block_fields = {
            "height": 0,
            "previous_hash": "0" * 64,
            "payload": {"sha256": FILE_HASH, "size": 12},
            "timestamp": "2026-11-10T00:00:00+00:00",
            "difficulty": 3,
        }
        self.block = {**self.block_fields, "nonce": 1152, "block_hash": BLOCK_HASH}
        self.document = {"schema": "testchain-evidence-v1", "block": self.block}

    def test_fingerprint_matches_known_sha256(self):
        self.assertEqual(fingerprint(REPORT_BYTES), FILE_HASH)

    def test_block_hash_ignores_payload_key_order(self):
        reversed_payload = {"size": 12, "sha256": FILE_HASH}
        reordered = {**self.block_fields, "payload": reversed_payload}
        self.assertEqual(canonical_hash(**self.block_fields, nonce=1152), BLOCK_HASH)
        self.assertEqual(canonical_hash(**reordered, nonce=1152), BLOCK_HASH)

    def test_mine_block_finds_first_valid_nonce(self):
        self.assertEqual(mine_block(**self.block_fields), (1152, BLOCK_HASH))

    def test_mine_block_zero_difficulty_starts_at_zero(self):
        fields = {**self.block_fields, "difficulty": 0}
        nonce, digest = mine_block(**fields)
        self.assertEqual(nonce, 0)
        self.assertEqual(digest, canonical_hash(**fields, nonce=0))

    def test_mine_block_rejects_negative_difficulty(self):
        fields = {**self.block_fields, "difficulty": -1}
        with self.assertRaisesRegex(ValueError, "难度"):
            mine_block(**fields)

    def test_mine_block_rejects_invalid_difficulty_before_hashing(self):
        # 防止错误实现真的尝试寻找 65 个前导零。
        with patch("core.crypto.canonical_hash", side_effect=AssertionError("非法难度进入了计算")):
            for difficulty in (65, True, False, 1.5, "3", None):
                with self.subTest(difficulty=difficulty):
                    fields = {**self.block_fields, "difficulty": difficulty}
                    with self.assertRaisesRegex(ValueError, "难度"):
                        mine_block(**fields)

    def test_valid_evidence_matches_original_report(self):
        valid, detail = verify_evidence(self.document, REPORT_BYTES)
        self.assertTrue(valid)
        self.assertIn("文件与凭证一致", detail)

    def test_unsupported_evidence_version_is_rejected(self):
        self.document["schema"] = "testchain-evidence-v2"
        self.assertEqual(verify_evidence(self.document, REPORT_BYTES), (False, "不支持的凭证格式"))

    def test_invalid_block_numbers_are_rejected(self):
        for field, value in (("height", True), ("nonce", -1), ("difficulty", "3")):
            with self.subTest(field=field):
                block = {**self.block, field: value}
                document = {**self.document, "block": block}
                self.assertEqual(verify_evidence(document, REPORT_BYTES), (False, "区块数字字段无效"))

    def test_other_proof_difficulty_is_rejected(self):
        self.block["difficulty"] = 2
        valid, detail = verify_evidence(self.document, REPORT_BYTES)
        self.assertFalse(valid)
        self.assertIn("难度", detail)

    def test_invalid_hash_format_is_rejected(self):
        for invalid_hash in (None, "0" * 63, "g" * 64):
            with self.subTest(invalid_hash=invalid_hash):
                self.block["previous_hash"] = invalid_hash
                self.assertEqual(verify_evidence(self.document, REPORT_BYTES), (False, "哈希字段无效"))

    def test_block_hash_mismatch_is_rejected(self):
        self.block["nonce"] += 1
        self.assertEqual(
            verify_evidence(self.document, REPORT_BYTES), (False, "凭证区块哈希或工作量证明不正确")
        )

    def test_matching_hash_without_required_prefix_is_rejected(self):
        self.block.update(nonce=0, block_hash=NONCE_ZERO_HASH)
        self.assertEqual(
            verify_evidence(self.document, REPORT_BYTES), (False, "凭证区块哈希或工作量证明不正确")
        )

    def test_report_changed_by_one_byte_is_rejected(self):
        self.assertEqual(
            verify_evidence(self.document, b"Best report\n"), (False, "文件与凭证记录不一致")
        )

    def test_incorrect_report_size_is_rejected(self):
        self.block_fields["payload"]["size"] = 13
        self.block["nonce"], self.block["block_hash"] = mine_block(**self.block_fields)
        self.assertEqual(verify_evidence(self.document, REPORT_BYTES), (False, "文件与凭证记录不一致"))

    def test_missing_or_invalid_document_is_rejected(self):
        for document in ({}, None):
            with self.subTest(document=document):
                self.assertEqual(verify_evidence(document, REPORT_BYTES), (False, "凭证字段缺失或格式错误"))
