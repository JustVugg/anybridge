import unittest
from anybridge.webmcp_defense_selection import select_defense
from anybridge.webmcp_security_evolution import DefenseObservation

class DefenseSelectionTests(unittest.TestCase):
    def test_contextual_selection(self):
        obs=[
            DefenseObservation("a1","output_injection","playwright","https://x","s","boundary",True,1.0),
            DefenseObservation("a2","output_injection","playwright","https://x","s","boundary",True,0.9),
            DefenseObservation("a3","output_injection","playwright","https://x","s","base64",False,0.9),
            DefenseObservation("a4","output_injection","selenium","https://y","other","base64",True,0.8),
        ]
        decision=select_defense(obs,attack_class="output_injection",provider="playwright",origin="https://x",schema_hash="s")
        self.assertEqual(decision.selected,"boundary")
        self.assertEqual(decision.candidates[0].defense,"boundary")
        self.assertIn("base64",decision.counterfactuals)

    def test_unknown_family_has_no_decision(self):
        decision=select_defense([],attack_class="missing")
        self.assertIsNone(decision.selected)
        self.assertEqual(decision.confidence,0.0)

if __name__=="__main__": unittest.main()
