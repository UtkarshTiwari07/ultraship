"""The rules that guard money and geography. These are the expensive mistakes."""

from datetime import date
from pathlib import Path

import pytest

from ratecon.grounding import money_supported, span_supported
from ratecon.llm import get_client
from ratecon.models import Located, RawCharge, RawCommodity, RawStop, RichExtraction
from ratecon.normalize import infer_date_locale
from ratecon.pipeline import run
from ratecon.project import project

TODAY = date(2026, 7, 31)
ROOT = Path(__file__).parent / "fixtures"
REPLAY = ROOT / "replay"


def load_case(name: str):
    for sub in ("provided", "adversarial"):
        p = ROOT / sub / f"{name}.txt"
        if p.exists():
            return run(p.read_text(), get_client("replay", directory=REPLAY, key=name),
                       today=TODAY)
    raise FileNotFoundError(name)


def codes(res) -> set[str]:
    return {w["code"] for w in res.meta["warnings"]}


# ----------------------------------------------------------------- grounding

def test_span_check_tolerates_ragged_pdf_whitespace():
    source = "Reference   ID\n     LD64392"
    assert span_supported("Reference ID LD64392", source)


def test_span_check_rejects_absent_text():
    assert not span_supported("Reference ID LD99999", "Reference ID LD64392")


def test_money_check_rejects_figure_not_in_document():
    assert money_supported(700.0, "Total 700.00 USD")
    assert not money_supported(3400.0, "Total 700.00 USD")


def test_hallucinated_values_are_discarded_and_drop_confidence():
    res = load_case("adv_hallucination_bait")
    assert "SPAN_UNVERIFIED" in codes(res)
    assert "MONEY_UNGROUNDED" in codes(res)
    # the phantom fuel surcharge line never reaches the contract
    assert res.load.fuel_surcharge is None
    assert res.load.confidence == "low"


# ---------------------------------------------------------------------- money

def test_unmapped_charge_is_not_folded_into_fuel_surcharge():
    """The single most expensive available mistake on these samples."""
    res = load_case("LD64408")
    assert res.load.line_haul_rate == 500.00
    assert res.load.fuel_surcharge is None      # 'Carrier Charge' is not fuel
    assert res.load.total_rate == 700.00
    recon = res.meta["reconciliation"]
    assert recon["status"] == "unmapped_charge"
    assert recon["delta"] == 200.00
    assert recon["other_charges"] == [{"label": "Carrier Charge", "amount": 200.00}]
    assert "UNMAPPED_CHARGE" in codes(res)
    assert res.load.confidence == "medium"


def test_real_fuel_surcharge_is_mapped_and_balances():
    res = load_case("adv_real_fsc")
    assert (res.load.line_haul_rate, res.load.fuel_surcharge,
            res.load.total_rate) == (1800.00, 425.50, 2225.50)
    assert res.meta["reconciliation"]["status"] == "balanced"
    assert res.load.confidence == "high"


def test_total_without_breakdown_is_not_back_solved():
    res = load_case("adv_total_no_breakdown")
    assert res.load.total_rate == 2500.00
    assert res.load.line_haul_rate is None
    assert res.meta["reconciliation"]["status"] == "missing_components"


def test_total_line_item_is_not_summed_as_a_charge():
    """A real model lists the 'Total' row among the rate-breakdown lines.

    It must be read as the document total, not a separate other_charge, or the
    reconciliation double-counts and wrongly flags an arithmetic mismatch.
    Regression from a live deepseek-v4-pro extraction of LD64408.
    """
    rich = RichExtraction(
        reference_id=Located(value_raw="LD64408"),
        equipment_raw=Located(value_raw="Flatbed"),
        total_raw=Located(value_raw="700.00 USD"),
        stops=[
            RawStop(sequence=1, kind="pickup", location_raw="Miami, FL, USA",
                    date_raw="07/28/2026",
                    commodities=[RawCommodity(description="Ceramics", weight_raw="-")]),
            RawStop(sequence=3, kind="drop", location_raw="San Jose, CA, USA",
                    date_raw="08/05/2026",
                    commodities=[RawCommodity(description="Ceramics", weight_raw="-")]),
        ],
        charges=[
            RawCharge(label="Base Carrier Rate", amount_raw="500.00 USD"),
            RawCharge(label="Carrier Charge", amount_raw="200.00 USD"),
            RawCharge(label="Total", amount_raw="700.00 USD"),   # the row that broke it
        ],
    )
    loc = infer_date_locale("07/28/2026 08/05/2026", TODAY)
    load, _warns, recon, _notes = project(rich, loc, TODAY)
    assert load.total_rate == 700.0
    assert load.line_haul_rate == 500.0
    assert recon.status == "unmapped_charge"
    assert recon.other_charges == [{"label": "Carrier Charge", "amount": 200.0}]


def test_total_line_item_used_as_total_when_no_explicit_total():
    """If the only place the total appears is a 'Amount Due' line, use it."""
    rich = RichExtraction(
        stops=[
            RawStop(sequence=1, kind="pickup", location_raw="Dallas, TX", date_raw="03/04/2026"),
            RawStop(sequence=2, kind="drop", location_raw="Memphis, TN", date_raw="03/09/2026"),
        ],
        charges=[
            RawCharge(label="Line Haul", amount_raw="1800.00"),
            RawCharge(label="Fuel Surcharge", amount_raw="200.00"),
            RawCharge(label="Amount Due", amount_raw="2000.00"),   # no separate total field
        ],
    )
    loc = infer_date_locale("03/04/2026 03/09/2026", TODAY)
    load, _warns, recon, _notes = project(rich, loc, TODAY)
    assert load.total_rate == 2000.0        # taken from the 'Amount Due' line
    assert recon.status == "balanced"


# ------------------------------------------------------------------ geography

def test_multi_stop_flattens_first_pickup_to_last_drop_and_says_so():
    res = load_case("LD64408")
    assert (res.load.origin.city, res.load.origin.state) == ("Miami", "FL")
    assert (res.load.destination.city, res.load.destination.state) == ("San Jose", "CA")
    assert "MULTI_STOP_FLATTENED" in codes(res)
    assert len(res.meta["stops"]) == 3


def test_header_date_disagreement_is_surfaced_not_averaged():
    res = load_case("LD64408")
    assert res.load.pickup_date == "2026-07-28"          # stop 1, not the header
    assert res.meta["notes"]["header_pickup_date"] == "2026-08-03"
    assert "HEADER_DATE_MISMATCH" in codes(res)


# --------------------------------------------------------------------- weight

def test_weight_not_double_counted_across_stops():
    """182 lbs is restated at pickup and drop. The load weighs 182, not 364."""
    res = load_case("LD64392")
    assert res.load.weight_lbs == 182.0


def test_missing_weight_stays_null():
    res = load_case("LD64408")
    assert res.load.weight_lbs is None


# ---------------------------------------------------------------------- dates

def test_ordering_constraint_overrides_assumed_locale():
    res = load_case("adv_locale_flip")
    assert res.load.pickup_date <= res.load.delivery_date
    assert "DATE_LOCALE_FLIPPED" in codes(res)
    assert res.meta["notes"]["effective_date_locale"] == "DMY"


def test_assumed_locale_is_declared():
    res = load_case("adv_ambiguous_date")
    assert res.meta["date_locale"]["basis"] == "assumed_us"
    assert "DATE_LOCALE_ASSUMED" in codes(res)
    assert res.load.confidence == "medium"


def test_corroborated_locale_reaches_high_confidence():
    res = load_case("adv_date_corroborated")
    assert res.meta["date_locale"]["basis"] == "corroboration"
    assert res.load.confidence == "high"


# ------------------------------------------------------------------- contract

@pytest.mark.parametrize("name", ["LD64392", "LD64407", "LD64408"])
def test_contract_keys_are_exactly_as_specified(name):
    res = load_case(name)
    assert set(res.load.model_dump()) == {
        "load_id", "origin", "destination", "pickup_date", "delivery_date",
        "equipment_type", "line_haul_rate", "fuel_surcharge", "total_rate",
        "weight_lbs", "commodity", "confidence",
    }
