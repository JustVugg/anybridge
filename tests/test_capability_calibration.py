from anybridge.capability_calibration import (
    CapabilityCalibrationStore, CalibrationObservation,
)


def test_confidence_calibration_reports_gap_and_corrects_it():
    s=CapabilityCalibrationStore(min_samples=3)
    for passed in (True, False, True):
        r=s.observe(CalibrationObservation("cap", .9, passed))
    assert r.status=="calibrated"
    assert r.empirical_rate == 2/3
    assert r.calibration_error > 0
    assert r.corrected_confidence < .9


def test_insufficient_holdout_is_not_called_calibrated():
    s=CapabilityCalibrationStore(min_samples=3)
    r=s.observe(CalibrationObservation("cap", 1.0, True))
    assert r.status=="insufficient_evidence"


def test_calibration_store_is_bounded_and_exportable():
    s=CapabilityCalibrationStore(max_observations=2)
    for i in range(4):
        s.observe(CalibrationObservation(str(i), .5, True))
    assert len(s.export()["observations"])==2
