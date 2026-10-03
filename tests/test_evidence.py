from anybridge.evidence import EvidenceEnvelope, evidence_status

def test_evidence_envelope_round_trips_to_dict():
    envelope = EvidenceEnvelope(
        test_id="t1",
        execution_id="e1",
        url="https://qa.example",
        action="bdd_scenario",
        expected={"text": "Ready"},
        observed="Ready",
        assertion="text present",
        status=evidence_status(True),
        provider="selenium",
        browser="chrome",
        confidence=1.0,
    )
    value = envelope.to_dict()
    assert value["test_id"] == "t1"
    assert value["provider"] == "selenium"
    assert value["status"] == "passed"
    assert value["confidence"] == 1.0
