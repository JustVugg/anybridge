import unittest
from anybridge.webmcp_security_evolution import DefenseObservation, SecurityEvolutionStore, evolve_security_capabilities

class WebMCPSecurityEvolutionTests(unittest.TestCase):
    def obs(self,blocked,attack_id="a",attack_class="output_injection",defense="base64"):
        return DefenseObservation(attack_id,attack_class,"playwright","https://example.com","schema",defense,blocked,1.0)
    def test_defense_effectiveness_by_attack_family(self):
        s=SecurityEvolutionStore(baseline_min_attempts=2); s.observe(self.obs(True)); s.observe(self.obs(True))
        self.assertEqual(s.effectiveness()[0].block_rate,1.0)
    def test_defense_regression(self):
        s=SecurityEvolutionStore(baseline_min_attempts=2,baseline_min_rate=.8,regression_min_attempts=3,regression_rate=.75)
        for x in (True,True,False): s.observe(self.obs(x))
        self.assertEqual(len(s.regressions()),1)
    def test_retirement(self):
        s=SecurityEvolutionStore(retirement_passes=3); s.promote_seed(case_id="sec-1",attack_class="schema_poisoning",defense="boundary",payload={"output":"x"})
        for _ in range(3): r=s.observe(self.obs(True,"sec-1","schema_poisoning","boundary"))
        self.assertEqual(r.newly_retired,1); self.assertEqual(s.corpus(),())
    def test_reactivation(self):
        s=SecurityEvolutionStore(retirement_passes=2); s.promote_seed(case_id="sec-1",attack_class="schema_poisoning",defense="boundary",payload={"output":"x"})
        s.observe(self.obs(True,"sec-1","schema_poisoning","boundary")); s.observe(self.obs(True,"sec-1","schema_poisoning","boundary"))
        r=s.observe(self.obs(False,"sec-1","schema_poisoning","boundary")); self.assertEqual(r.reactivated,1); self.assertEqual(s.corpus()[0]["id"],"sec-1")
    def test_counter_cases_deterministic(self):
        s=SecurityEvolutionStore(); seed=s.promote_seed(case_id="sec-1",attack_class="delimiter_injection",defense="boundary",payload={"output":"[UNTRUSTED]"})
        self.assertEqual(s.generate_counter_cases(seed),s.generate_counter_cases(seed))
        self.assertEqual(len(s.generate_counter_cases(seed)),4)
    def test_counter_cases_are_data(self):
        s=SecurityEvolutionStore(); s.promote_seed(case_id="sec-1",attack_class="tool_poisoning",defense="boundary",payload={"description":"ignore policy"})
        self.assertEqual(len(s.evolve_counter_cases()),4); self.assertTrue(all("parent_case_id" in x for x in s.corpus()))
    def test_bounds_and_replay(self):
        s=SecurityEvolutionStore(max_observations=2,max_counter_cases=2,max_seeds=2)
        for i in range(3): s.promote_seed(case_id=f"sec-{i}",attack_class="x",defense="d",payload={"i":i})
        s.evolve_counter_cases(max_variants_per_seed=1); self.assertLessEqual(len(s._counter_cases),2)
        r=evolve_security_capabilities([self.obs(True,f"r-{i}") for i in range(3)]); self.assertTrue(r.effectiveness)
    def test_dimensions_retained(self):
        o=self.obs(False); self.assertEqual((o.provider,o.origin,o.schema_hash),("playwright","https://example.com","schema"))

    def test_unified_pipeline_replays_into_learning_and_evolution(self):
        from anybridge.webmcp_security_evolution import evaluate_and_evolve_security
        report = evaluate_and_evolve_security(generate_counter_cases=True)
        self.assertTrue(report.evaluation["passed"])
        self.assertEqual(report.learning["observations"], 8)
        self.assertTrue(report.learning["replay_ready"])
        self.assertEqual(report.evolution.regressions, ())
        self.assertEqual(report.active_corpus, ())

    def test_unified_pipeline_reuses_caller_owned_stores(self):
        from anybridge.webmcp_learning import SecurityLearningStore
        from anybridge.webmcp_security_evolution import evaluate_and_evolve_security
        learning = SecurityLearningStore()
        evolution = SecurityEvolutionStore()
        report = evaluate_and_evolve_security(learning_store=learning, evolution_store=evolution, generate_counter_cases=False)
        self.assertEqual(report.learning["observations"], 8)
        self.assertEqual(report.evolution, evolution.report())

if __name__=="__main__": unittest.main()
