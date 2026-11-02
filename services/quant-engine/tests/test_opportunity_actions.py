import asyncio

import pytest
from fastapi import HTTPException

from app import main


class FakeScannerStore:
    def __init__(self) -> None:
        self.actions: dict[str, dict[str, object]] = {}

    def latest_completed(self) -> None:
        return None

    def get_action(self, opportunity_id: str) -> dict[str, object] | None:
        return self.actions.get(opportunity_id)

    def save_action(
        self,
        opportunity_id: str,
        _action: str,
        state: dict[str, object],
    ) -> None:
        self.actions[opportunity_id] = state


@pytest.fixture(autouse=True)
def scanner_store(monkeypatch: pytest.MonkeyPatch) -> FakeScannerStore:
    durable = FakeScannerStore()
    monkeypatch.setattr(main, "_get_scanner_store", lambda: durable)
    monkeypatch.setattr(main, "_get_schedule_stores", dict)
    return durable


def opportunity(eligible: bool = True) -> dict[str, object]:
    return {
        "id": "AAPL:BUY",
        "eligible": eligible,
        "user_decision": "pending",
        "snoozed_until": None,
        "trade_plan": {
            "entry_zone": {"low": 99.0, "high": 100.0},
            "stop_loss": 95.0,
            "targets": [{"price": 105.0, "exit_pct": 100, "label": "Target 1"}],
            "quantity": 10.0,
            "position_size_usd": 995.0,
            "maximum_planned_loss_usd": 45.0,
            "time_stop": "5 trading days",
        },
    }


def prepare(candidate: dict[str, object]) -> None:
    main._scan_state["last_scan_result"] = {
        "signals": [{"opportunity": candidate}],
    }


def test_approve_reject_and_snooze_update_latest_opportunity() -> None:
    candidate = opportunity()
    prepare(candidate)

    approved = asyncio.run(main.update_opportunity_action(
        "AAPL:BUY", main.OpportunityActionRequest(action="approve")
    ))
    assert approved["user_decision"] == "approved"

    snoozed = asyncio.run(main.update_opportunity_action(
        "AAPL:BUY", main.OpportunityActionRequest(action="snooze", snooze_minutes=30)
    ))
    assert snoozed["user_decision"] == "snoozed"
    assert snoozed["snoozed_until"] is not None

    rejected = asyncio.run(main.update_opportunity_action(
        "AAPL:BUY", main.OpportunityActionRequest(action="reject")
    ))
    assert rejected["user_decision"] == "rejected"
    assert rejected["snoozed_until"] is None


def test_edit_recalculates_position_size_and_maximum_loss() -> None:
    candidate = opportunity()
    prepare(candidate)

    updated = asyncio.run(main.update_opportunity_action(
        "AAPL:BUY",
        main.OpportunityActionRequest(
            action="edit",
            edit=main.TradePlanEdit(
                entry_zone_low=100,
                entry_zone_high=102,
                stop_loss=96,
                targets=[108, 112],
                quantity=12,
                time_stop="3 trading days",
            ),
        ),
    ))
    plan = updated["trade_plan"]

    assert updated["user_decision"] == "edited"
    assert plan["position_size_usd"] == 1_212
    assert plan["maximum_planned_loss_usd"] == 60
    assert [target["price"] for target in plan["targets"]] == [108, 112]

    approved = asyncio.run(
        main.update_opportunity_action(
            "AAPL:BUY",
            main.OpportunityActionRequest(action="approve"),
        )
    )
    assert approved["user_decision"] == "approved"
    assert approved["trade_plan"]["position_size_usd"] == 1_212


def test_ineligible_opportunity_cannot_be_approved() -> None:
    prepare(opportunity(eligible=False))

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.update_opportunity_action(
            "AAPL:BUY", main.OpportunityActionRequest(action="approve")
        ))

    assert error.value.status_code == 409
