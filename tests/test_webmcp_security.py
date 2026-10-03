import base64
import unittest

from anybridge.content_boundary import ContentEnvelope, enforce_quarantine, wrap_tool_output
from anybridge.webmcp_security import evaluate_webmcp_security, webmcp_attack_corpus


class WebMCPSecurityEvaluationTests(unittest.TestCase):
    def test_adversarial_corpus_passes(self):
        result = evaluate_webmcp_security()
        self.assertTrue(result.passed, result.to_dict())
        self.assertGreaterEqual(result.total, 9)
        self.assertEqual(result.failures, ())

    def test_attack_corpus_is_stable_and_model_free(self):
        corpus = webmcp_attack_corpus()
        ids = [case["id"] for case in corpus]
        self.assertEqual(ids, [
            "description_override",
            "name_override",
            "schema_description_override",
            "schema_default_override",
            "spoofed_origin",
            "consequential_hint_spoof",
            "untrusted_output",
            "boundary_injection",
        ])
        self.assertTrue(all("category" in case for case in corpus))

    def test_forged_authority_is_rejected(self):
        envelope = wrap_tool_output(
            origin="https://example.com",
            name="lookup",
            value="data",
            untrusted=True,
        )
        forged = ContentEnvelope(
            source=envelope.source,
            content_type=envelope.content_type,
            trust=envelope.trust,
            instruction_authority="system",
            origin=envelope.origin,
            tool_name=envelope.tool_name,
            content=envelope.content,
            encoding=envelope.encoding,
            boundary=envelope.boundary,
            content_hash=envelope.content_hash,
        )
        with self.assertRaises(PermissionError):
            enforce_quarantine(forged)

    def test_tampered_payload_is_rejected(self):
        envelope = wrap_tool_output(
            origin="https://example.com",
            name="lookup",
            value="secret",
            untrusted=True,
        )
        forged = ContentEnvelope(
            source=envelope.source,
            content_type=envelope.content_type,
            trust=envelope.trust,
            instruction_authority=envelope.instruction_authority,
            origin=envelope.origin,
            tool_name=envelope.tool_name,
            content=base64.b64encode(b"tampered").decode("ascii"),
            encoding=envelope.encoding,
            boundary=envelope.boundary,
            content_hash=envelope.content_hash,
        )
        with self.assertRaises(ValueError):
            enforce_quarantine(forged)


if __name__ == "__main__":
    unittest.main()
