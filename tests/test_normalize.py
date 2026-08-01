from datetime import date

import pytest

from ratecon.normalize import (
    infer_date_locale,
    parse_date,
    parse_equipment,
    parse_money,
    parse_named_date,
    parse_place,
    parse_weight,
)

TODAY = date(2026, 7, 31)


@pytest.mark.parametrize("raw,expected", [
    ("$1,250.50 USD", 1250.50),
    ("50.00 USD", 50.0),
    ("$700.00", 700.0),
    ("2,225.50", 2225.50),
    ("-", None),
    ("", None),
    (None, None),
])
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("28-Jul-2026", date(2026, 7, 28)),
    ("Jul 28, 2026", date(2026, 7, 28)),
    ("2026-07-28", date(2026, 7, 28)),
    ("07/28/2026", None),  # numeric forms are not this function's job
])
def test_parse_named_date(raw, expected):
    assert parse_named_date(raw, TODAY) == expected


def test_locale_from_impossible_component():
    d = infer_date_locale("Shipping 07/30/2026", TODAY)
    assert (d.locale, d.basis) == ("MDY", "impossible_component")


def test_locale_from_impossible_component_dmy():
    d = infer_date_locale("Shipping 30/07/2026", TODAY)
    assert (d.locale, d.basis) == ("DMY", "impossible_component")


def test_locale_from_corroboration():
    """An unambiguous named date elsewhere in the document settles the order."""
    text = "Pickup Date 04-Mar-2026 ... Shipping Date & Time 3/4/26"
    d = infer_date_locale(text, TODAY)
    assert (d.locale, d.basis) == ("MDY", "corroboration")


def test_locale_falls_back_and_says_so():
    d = infer_date_locale("Shipping 3/4/26", TODAY)
    assert (d.locale, d.basis) == ("MDY", "assumed_us")


def test_ambiguity_is_reported_not_hidden():
    p = parse_date("3/4/26", "MDY", TODAY)
    assert p.value == date(2026, 3, 4)
    assert p.ambiguous is True
    assert p.alternate == date(2026, 4, 3)


def test_two_digit_year_clamped_to_near_future():
    assert parse_date("3/4/26", "MDY", TODAY).value.year == 2026


def test_place_anchors_on_state_not_first_token():
    """'Illinois State Police' must not be read as the city."""
    p = parse_place("Illinois State Police, 100 W Randolph St, Chicago, IL 60601, USA")
    assert (p.city, p.state, p.zip) == ("Chicago", "IL", "60601")


def test_place_strips_airport_codes_and_tolerates_missing_zip():
    p = parse_place(
        "Miami International Airport (MIA), Northwest 42nd Avenue, Miami, FL, USA")
    assert (p.city, p.state, p.zip) == ("Miami", "FL", None)


def test_place_multi_token_city():
    p = parse_place("Hertz Car Rental, Airport Boulevard, San Jose, CA, USA")
    assert (p.city, p.state) == ("San Jose", "CA")


def test_place_prefers_state_zip_cell_over_leading_ocr_noise():
    """OCR reflowed a stray 'il' to the front; the real state is GA by the ZIP."""
    p = parse_place(
        "il, Pickup 1234 Industrial Rd. es Plastic Components. 24,000\n"
        "Atlanta, GA 30336 Mate")
    assert (p.city, p.state, p.zip) == ("Atlanta", "GA", "30336")


def test_place_strips_ocr_pipe_prefix():
    """OCR cell-bleed 'TP | Kansas City' must resolve to Kansas City."""
    p = parse_place("115 Logistics St.\nTP | Kansas City, MO 64120")
    assert (p.city, p.state, p.zip) == ("Kansas City", "MO", "64120")


def test_place_reads_canadian_province():
    """Cross-border freight: 'Toronto, ON' parses as a province (no US ZIP)."""
    p = parse_place("200 Carrier Dr., Toronto, ON M9W 5R1, Canada")
    assert (p.city, p.state, p.zip) == ("Toronto", "ON", None)


def test_place_strips_leading_street_when_no_comma():
    """Street and city on one segment: '1234 W. Touhy Ave. Des Plaines' -> city."""
    p = parse_place("1234 W. Touhy Ave. Des Plaines, IL 60018")
    assert (p.city, p.state, p.zip) == ("Des Plaines", "IL", "60018")


def test_place_strips_stop_marker_that_bled_into_the_cell():
    assert parse_place("I Pickup Toronto, ON M9W 5R1, Canada").city == "Toronto"
    assert parse_place("2 Drop Detroit, MI 48226, USA").city == "Detroit"


def test_place_keeps_city_that_starts_with_a_street_word():
    """'St. Louis' must survive -- the street strip requires a leading number."""
    p = parse_place("400 Market St., St. Louis, MO 63102")
    assert (p.city, p.state) == ("St. Louis", "MO")


@pytest.mark.parametrize("raw,expected", [
    ("Flatbed", "flatbed"),
    ("53' Dry Van", "van"),
    ("Reefer", "reefer"),
    ("Refrigerated -10F", "reefer"),
    ("Step Deck", "other"),      # open-deck variant, not flatbed
    ("Conestoga", "other"),
    ("Power Only", "other"),
    (None, None),
])
def test_parse_equipment(raw, expected):
    assert parse_equipment(raw)[0] == expected


def test_weight_unit_assumption_is_info_not_a_risk():
    val, notes = parse_weight("38,200")
    assert val == 38200.0
    assert [s for s, _ in notes] == ["info"]


def test_implausible_weight_is_flagged_medium():
    val, notes = parse_weight("182")
    assert val == 182.0
    assert "medium" in [s for s, _ in notes]


def test_weight_kg_converted():
    val, notes = parse_weight("18000 kg")
    assert round(val) == 39683
    assert ("info", "converted from kg") in notes


@pytest.mark.parametrize("raw", ["-", "", None, "n/a"])
def test_weight_absent(raw):
    assert parse_weight(raw)[0] is None
