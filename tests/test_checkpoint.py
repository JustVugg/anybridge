import os
import tempfile
from unittest import TestCase
from anybridge.checkpoint import CheckpointStore, decide_resume, make_checkpoint


class CheckpointTests(TestCase):
    def test_resume_requires_revalidation_by_default(self):
        cp = make_checkpoint(
            execution_id="e1", checkpoint_id="c1", step_index=3,
            action="click", target="@save", url="https://example.test",
        )
        decision = decide_resume(cp, current_url="https://example.test", current_target_exists=True)
        self.assertEqual(decision.action, "revalidate_before_resume")

    def test_verified_checkpoint_can_resume(self):
        cp = make_checkpoint(
            execution_id="e1", checkpoint_id="c1", step_index=3,
            action="click", target="@save", url="https://example.test",
            safe_to_resume=True,
        )
        decision = decide_resume(
            cp, current_url="https://example.test",
            current_target_exists=True, recovery_confidence=0.95,
        )
        self.assertEqual(decision.action, "resume_from_checkpoint")
        self.assertEqual(decision.step_index, 3)

    def test_url_change_blocks_direct_resume(self):
        cp = make_checkpoint(
            execution_id="e1", checkpoint_id="c1", step_index=3,
            action="fill", target="@name", url="https://example.test",
            safe_to_resume=True,
        )
        decision = decide_resume(cp, current_url="https://other.test", current_target_exists=True, recovery_confidence=0.99)
        self.assertEqual(decision.action, "revalidate_before_resume")

    def test_atomic_store_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "checkpoint.json")
            store = CheckpointStore(path)
            cp = make_checkpoint(
                execution_id="e1", checkpoint_id="c1", step_index=2,
                action="navigate", target=None, url="https://example.test",
            )
            store.save(cp)
            loaded = store.load()
            self.assertEqual(loaded, cp)
            store.clear()
            self.assertIsNone(store.load())


if __name__ == "__main__":
    import unittest
    unittest.main()
