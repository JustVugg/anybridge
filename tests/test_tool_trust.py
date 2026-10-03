import unittest

from anybridge.tool_trust import (
    ToolTrustRegistry,
    assess_tool_trust,
    fingerprint_tool,
)


def tool(origin="https://example.com", name="lookup", schema=None, **annotations):
    value = {
        "origin": origin,
        "name": name,
        "description": "Lookup account data",
        "inputSchema": schema or {"type": "object", "properties": {"id": {"type": "string"}}},
        "annotations": annotations,
    }
    return value


class ToolTrustTests(unittest.TestCase):
    def test_fingerprint_is_stable(self):
        a = fingerprint_tool(tool())
        b = fingerprint_tool(tool())
        self.assertEqual(a, b)
        self.assertEqual(len(a.schema_hash), 16)

    def test_schema_change_is_detected(self):
        registry = ToolTrustRegistry()
        first = registry.observe(tool(), trusted=True)
        second = registry.observe(
            tool(schema={"type": "object", "properties": {"account": {"type": "integer"}}})
        )
        self.assertEqual(first.state, "trusted")
        self.assertTrue(second.changed)
        self.assertEqual(second.state, "changed")
        self.assertTrue(second.requires_confirmation)

    def test_origin_is_part_of_identity(self):
        registry = ToolTrustRegistry()
        registry.observe(tool(origin="https://one.example"), trusted=True)
        other = registry.observe(tool(origin="https://two.example"))
        self.assertEqual(other.state, "new")
        self.assertIsNone(registry.get("https://two.example", "lookup"))
        self.assertIsNotNone(registry.get("https://one.example", "lookup"))

    def test_consequential_hint_requires_confirmation(self):
        assessment = assess_tool_trust(
            tool(name="book", consequentialHint=True)
        )
        self.assertTrue(assessment.requires_confirmation)
        self.assertEqual(assessment.risk.value, "consequential")
        self.assertEqual(assessment.retry_policy.value, "confirm")

    def test_untrusted_output_is_marked(self):
        assessment = assess_tool_trust(
            tool(untrustedContentHint=True)
        )
        self.assertTrue(assessment.output_untrusted)
        self.assertEqual(assessment.state, "untrusted_output")

    def test_outcomes_feed_back_without_authorizing_tool(self):
        registry = ToolTrustRegistry()
        registry.observe(tool(), trusted=True)
        record = registry.record_outcome("https://example.com", "lookup", True)
        self.assertEqual(record.outcomes, 1)
        self.assertEqual(record.successful_outcomes, 1)
        self.assertEqual(record.state, "trusted")

    def test_bounded_registry(self):
        registry = ToolTrustRegistry(max_records=2)
        for index in range(3):
            registry.observe(tool(origin=f"https://{index}.example", name="lookup"))
        self.assertEqual(len(registry.list()), 2)


if __name__ == "__main__":
    unittest.main()
