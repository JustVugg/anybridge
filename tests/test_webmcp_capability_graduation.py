import unittest
from anybridge.webmcp_capability_graduation import CapabilityGraduationStore
from anybridge.webmcp_security_evolution import DefenseObservation

def obs(i, blocked=True, defense="boundary"):
    return DefenseObservation(str(i),"output_injection","playwright","https://x","schema",defense,blocked,1.0)

class GraduationTests(unittest.TestCase):
    def test_evidence_holdout_canary_graduation(self):
        s=CapabilityGraduationStore(graduation_min_attempts=3,holdout_min_attempts=2)
        for i in range(3): d=s.observe(obs(i),cohort="evidence")
        self.assertEqual(d.status,"holdout")
        for i in range(3,5): d=s.observe(obs(i),cohort="holdout")
        self.assertEqual(d.status,"canary")
        d=s.canary_result(d.capability_id,attack_class="output_injection",defense="boundary",observations=[obs(5),obs(6)])
        self.assertEqual(d.status,"graduated")
        self.assertEqual(s.active()[0].capability_id,d.capability_id)

    def test_canary_regression_rolls_back(self):
        s=CapabilityGraduationStore(graduation_min_attempts=1,holdout_min_attempts=1,rollback_rate=.75)
        s.observe(obs(1),cohort="evidence")
        s.observe(obs(2),cohort="holdout")
        d=s.canary_result("output_injection::boundary",attack_class="output_injection",defense="boundary",observations=[obs(3,False),obs(4,False)])
        self.assertEqual(d.status,"rolled_back")
        self.assertEqual(d.action,"quarantine")

if __name__=="__main__": unittest.main()
