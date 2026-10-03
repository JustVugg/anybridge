from pathlib import Path
from anybridge.bdd import parse_feature

def test_parse_feature_and_scenario_outline():
    feature = parse_feature("""Feature: Login
  Background:
    Given I wait for text "Ready"
  Scenario Outline: login
    Given open the page https://example.test
    Then I should see "<text>"
    Examples:
      | text |
      | Ready |
      | Done |
""")
    assert feature.name == "Login"
    assert len(feature.background) == 1
    assert len(feature.scenarios) == 1
    assert feature.scenarios[0].name == "login"
    assert any(step.keyword == "Examples" for step in feature.scenarios[0].steps)


def test_bdd_step_evidence_is_collected():
    import asyncio
    from anybridge.bdd import BDDRunner

    class Bridge:
        current_url = "https://example.test"

        async def navigate(self, url):
            self.current_url = url

        async def collect_evidence(self, **kwargs):
            return kwargs

        async def snapshot(self, **kwargs):
            return "Ready"

    async def run():
        feature = parse_feature("""Feature: Evidence
  Scenario: smoke
    Given open the page https://example.test
    Then I should see "Ready"
""")
        result = await BDDRunner(Bridge()).run(feature)
        return result

    result = asyncio.run(run())
    evidence = result["scenarios"][0]["step_evidence"]
    assert len(evidence) == 2
    assert all(item["action"] == "bdd_step" for item in evidence)
