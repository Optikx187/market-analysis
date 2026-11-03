from datetime import datetime

from app.market_calendar import schedule_decision, schedule_definitions


def moment(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_crypto_schedule_is_continuous_on_weekends_and_holidays() -> None:
    for value in (
        "2026-07-03T15:00:00+00:00",
        "2026-07-04T03:15:00+00:00",
        "2026-12-25T20:00:00+00:00",
    ):
        decision = schedule_decision(moment(value), None)
        assert decision.eligible is True
        assert decision.next_eligible_at is None
        assert decision.calendar_name == "24/7"


def test_xnys_holiday_defers_to_next_session() -> None:
    decision = schedule_decision(
        moment("2026-07-03T15:00:00+00:00"),
        "XNYS",
    )

    assert decision.eligible is False
    assert decision.next_eligible_at == moment(
        "2026-07-06T13:30:00+00:00"
    )


def test_xnys_open_tracks_dst_transitions() -> None:
    assert schedule_decision(
        moment("2026-03-09T13:30:00+00:00"),
        "XNYS",
    ).eligible
    assert not schedule_decision(
        moment("2026-03-09T13:29:00+00:00"),
        "XNYS",
    ).eligible
    assert schedule_decision(
        moment("2026-11-02T14:30:00+00:00"),
        "XNYS",
    ).eligible
    assert not schedule_decision(
        moment("2026-11-02T14:29:00+00:00"),
        "XNYS",
    ).eligible


def test_schedule_definitions_separate_asset_classes() -> None:
    equity, crypto = schedule_definitions("XNYS")

    assert equity.asset_types == frozenset({"stock"})
    assert equity.calendar_name == "XNYS"
    assert crypto.asset_types == frozenset({"crypto"})
    assert crypto.calendar_name is None
