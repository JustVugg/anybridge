import unittest
from anybridge.webmcp_learning import SecurityLearningStore
from anybridge.webmcp_replay import SecurityReplayCase, SecurityReplayOutcome, replay_security_corpus
from anybridge.webmcp_security_evolution import SecurityEvolutionStore

class ReplayTests(unittest.TestCase):
    def case(self):
        return SecurityReplayCase("c1","a1","output_injection","playwright","https://example.com","schema","base64",{"output":"x"})

    def test_round_trip_promotes_and_handoffs(self):
        learning=SecurityLearningStore(promotion_min_failures=1)
        evolution=SecurityEvolutionStore()
        report=replay_security_corpus([self.case()],[SecurityReplayOutcome("c1",False)],
            learning_store=learning,evolution_store=evolution,generate_counter_cases=False)
        self.assertEqual(report.outcomes,1)
        self.assertEqual(report.observations,1)
        self.assertTrue(report.learning["promoted"])
        self.assertTrue(report.active_corpus[0]["id"].startswith("sec-"))

    def test_unknown_case_is_ignored(self):
        report=replay_security_corpus([], [SecurityReplayOutcome("missing",False)], generate_counter_cases=False)
        self.assertEqual(report.ignored_outcomes,1)

if __name__ == "__main__":
    unittest.main()
