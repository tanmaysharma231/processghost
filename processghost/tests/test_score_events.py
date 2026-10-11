"""Unit tests for evals/score_events.py -- pure matching logic, no video."""
from __future__ import annotations

from evals.score_events import score_events


def _d(eid, t0, t1, typ="local"):
    return {"event_id": eid, "t_start": t0, "t_end": t1, "type": typ}


def _l(lid, t0, t1, typ=None):
    lab = {"label_id": lid, "t_start": t0, "t_end": t1}
    if typ:
        lab["type"] = typ
    return lab


def test_perfect_match():
    r = score_events([_d("e1", 1.0, 1.2), _d("e2", 5.0, 5.5)], [_l("L1", 1.0, 1.3), _l("L2", 5.1, 5.4)])
    assert r["precision"] == 1.0 and r["recall"] == 1.0
    assert r["missed_labels"] == [] and r["false_events"] == []


def test_missed_label_and_false_event():
    r = score_events([_d("e1", 1.0, 1.2), _d("e9", 20.0, 20.2)], [_l("L1", 1.0, 1.3), _l("L2", 8.0, 8.3)])
    assert r["recall"] == 0.5 and r["precision"] == 0.5
    assert r["missed_labels"] == ["L2"] and r["false_events"] == ["e9"]


def test_tolerance_widens_overlap():
    assert score_events([_d("e1", 1.0, 1.1)], [_l("L1", 1.4, 1.5)], tol=0.5)["recall"] == 1.0
    assert score_events([_d("e1", 1.0, 1.1)], [_l("L1", 1.4, 1.5)], tol=0.1)["recall"] == 0.0


def test_matching_is_one_to_one():
    r = score_events([_d("e1", 1.0, 1.2), _d("e2", 1.1, 1.3)], [_l("L1", 1.0, 1.3)])
    assert len(r["matched"]) == 1 and len(r["false_events"]) == 1


def test_type_agreement_only_counts_typed_labels():
    r = score_events(
        [_d("e1", 1.0, 1.2, "local"), _d("e2", 5.0, 5.5, "scene_cut")],
        [_l("L1", 1.0, 1.3, "local"), _l("L2", 5.0, 5.5, "local")],
    )
    assert r["type_agreement"] == 0.5
    assert score_events([_d("e1", 1.0, 1.2)], [_l("L1", 1.0, 1.3)])["type_agreement"] is None


def test_empty_inputs():
    r = score_events([], [])
    assert r["precision"] == 0.0 and r["recall"] == 0.0
