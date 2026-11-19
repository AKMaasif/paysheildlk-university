from app.risk import Signals, decide_policy


def test_sim_swap_and_new_payee_is_a_hold():
    result = decide_policy(
        Signals(
            sim_swap_age_hours=0.4,
            new_imei_age_hours=0.4,
            location_change_age_hours=0.3,
            new_payee=True,
            amount_lkr=200_000,
            swapped=True,
        )
    )
    assert result.decision == "hold"
    assert result.score >= 45


def test_port_out_combo_is_a_block():
    result = decide_policy(
        Signals(
            sim_swap_age_hours=2,
            port_out=True,
            new_payee=True,
            password_reset_age_hours=0.5,
            amount_lkr=800_000,
            swapped=True,
        )
    )
    assert result.decision == "block"


def test_routine_bill_is_a_release():
    result = decide_policy(Signals(amount_lkr=4850, new_payee=False, swapped=False))
    assert result.decision == "release"
    assert result.score < 45


def test_model_wording_cannot_change_the_rule():
    suspicious = Signals(sim_swap_age_hours=1, new_payee=True, amount_lkr=200_000, swapped=True)
    first = decide_policy(suspicious)
    second = decide_policy(suspicious)
    assert first.decision == second.decision == "hold"
    assert "release" not in first.reason.lower() or "held" in first.reason.lower()
