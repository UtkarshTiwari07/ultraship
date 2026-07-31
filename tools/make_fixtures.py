"""Author the replay fixtures.

Replay fixtures are *expected model output*: the RichExtraction a correct
locate-and-cite call should produce for a given document. They exist so the
deterministic two-thirds of the pipeline (normalize / project / confidence)
can be tested in CI with no API key and no spend. They are not recordings of
a live call and the README says so.

Run: python tools/make_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
ADV = FIX / "adversarial"
REPLAY = FIX / "replay"


def loc(v, span=None):
    return {"value_raw": v, "span": span if span is not None else v}


def stop(seq, kind, location, date_raw, commodities, span=None):
    return {"sequence": seq, "kind": kind, "location_raw": location,
            "date_raw": date_raw, "commodities": commodities, "span": span}


def com(desc, weight=None, qty=None):
    return {"description": desc, "weight_raw": weight, "quantity_raw": qty}


CHICAGO = "Illinois State Police, 100 W Randolph St, Chicago, IL 60601, USA"
NYU = ("New York University Jeffrey S. Gould Welcome Center, "
       "50 W 4th Street, New York, NY 10012, USA")
MIA = "Miami International Airport (MIA), Northwest 42nd Avenue, Miami, FL, USA"
SJC = ("Hertz Car Rental - San Jose - San Jose Mineta International Airport "
       "(SJC), Airport Boulevard, San Jose, CA, USA")

REPLAYS: dict[str, dict] = {}

# ---------------------------------------------------------------- provided 1/3
REPLAYS["LD64392"] = {
    "reference_id": loc("LD64392", "Reference ID               LD64392"),
    "header_pickup_date": loc("30-Jul-2026", "Pickup Date                30-Jul-2026"),
    "agreed_amount": loc("$50.00 USD", "Flatbed           $50.00 USD"),
    "equipment_raw": loc("Flatbed", "Flatbed           $50.00 USD"),
    "stops": [
        stop(1, "pickup", CHICAGO, "07/30/2026", [com("Ceramics", "182", "07")]),
        stop(2, "drop", NYU, "08/01/2026", [com("Ceramics", "182", "07")]),
    ],
    "charges": [{"label": "Base Carrier Rate", "amount_raw": "50.00 USD",
                 "span": "Base Carrier Rate"}],
    "total_raw": loc("50.00 USD", "Total"),
}

REPLAYS["LD64407"] = {
    "reference_id": loc("LD64407", "Reference ID               LD64407"),
    "header_pickup_date": loc("31-Jul-2026", "Pickup Date                31-Jul-2026"),
    "agreed_amount": loc("$50.00 USD", "Flatbed           $50.00 USD"),
    "equipment_raw": loc("Flatbed", "Flatbed           $50.00 USD"),
    "stops": [
        stop(1, "pickup", CHICAGO, "07/31/2026", [com("Ceramics", "422", "10")]),
        stop(2, "drop", NYU, "08/02/2026", [com("Ceramics", "422", "10")]),
    ],
    "charges": [{"label": "Base Carrier Rate", "amount_raw": "50.00 USD",
                 "span": "Base Carrier Rate"}],
    "total_raw": loc("50.00 USD", "Total"),
}

# The three-stop, unmapped-charge, header-disagreement case.
REPLAYS["LD64408"] = {
    "reference_id": loc("LD64408", "Reference ID               LD64408"),
    "header_pickup_date": loc("03-Aug-2026", "Pickup Date                03-Aug-2026"),
    "agreed_amount": loc("$700.00 USD", "Flatbed            $700.00 USD"),
    "equipment_raw": loc("Flatbed", "Flatbed            $700.00 USD"),
    "stops": [
        stop(1, "pickup", MIA, "07/28/2026", [com("Ceramics", "-", "g")]),
        stop(2, "pickup", CHICAGO, "08/03/2026", [com("Commodity_t", "-", "55")]),
        stop(3, "drop", SJC, "08/05/2026",
             [com("Commodity_t", "-", "55"), com("Ceramics", "-", "g")]),
    ],
    "charges": [
        {"label": "Base Carrier Rate", "amount_raw": "500.00 USD",
         "span": "Base Carrier Rate"},
        {"label": "Carrier Charge", "amount_raw": "200.00 USD",
         "span": "Carrier Charge"},
    ],
    "total_raw": loc("700.00 USD", "Total"),
}

# ------------------------------------------------------------------ adversarial
ADVERSARIAL_DOCS: dict[str, str] = {}

ADVERSARIAL_DOCS["adv_ambiguous_date"] = """\
CARRIER RATE & LOAD CONFIRMATION
Reference ID  ADV-AMB-01
EQUIPMENT  Dry Van        AGREED AMOUNT (USD)  $1,900.00 USD
Stops
1  Pickup   Dallas Logistics Hub, 4800 Cleveland Rd, Dallas, TX 75212, USA
   Shipping Date & Time  3/4/26
   Commodity  Paper Goods   Weight 18,400 lbs   Quantity 22
2  Drop     Memphis Distribution, 3475 Tulane Rd, Memphis, TN 38116, USA
   Delivery Date & Time  3/9/26
   Commodity  Paper Goods   Weight 18,400 lbs   Quantity 22
Rate Breakdown
Line Haul                 1,900.00 USD
Total                     1,900.00 USD
"""

ADVERSARIAL_DOCS["adv_date_corroborated"] = """\
CARRIER RATE & LOAD CONFIRMATION
Reference ID  ADV-COR-01
Pickup Date   04-Mar-2026
EQUIPMENT  Reefer         AGREED AMOUNT (USD)  $2,650.00 USD
Stops
1  Pickup   Salinas Cold Storage, 900 Work St, Salinas, CA 93901, USA
   Shipping Date & Time  3/4/26
   Commodity  Lettuce   Weight 41,000 lbs   Quantity 24
2  Drop     Jessup Produce Terminal, 8100 Dorsey Run Rd, Jessup, MD 20794, USA
   Delivery Date & Time  3/9/26
   Commodity  Lettuce   Weight 41,000 lbs   Quantity 24
Rate Breakdown
Line Haul                 2,200.00 USD
Fuel Surcharge              450.00 USD
Total                     2,650.00 USD
"""

ADVERSARIAL_DOCS["adv_real_fsc"] = """\
CARRIER RATE & LOAD CONFIRMATION
Reference ID  ADV-FSC-01
Pickup Date   12-Aug-2026
EQUIPMENT  53' Dry Van    AGREED AMOUNT (USD)  $2,225.50 USD
Stops
1  Pickup   Ryder Yard, 2200 S Wolf Rd, Des Plaines, IL 60018, USA
   Shipping Date & Time  08/12/2026
   Commodity  Packaged Beverages   Weight 38,200 lbs   Quantity 26
2  Drop     Kroger DC, 4111 Executive Pkwy, Westerville, OH 43081, USA
   Delivery Date & Time  08/14/2026
   Commodity  Packaged Beverages   Weight 38,200 lbs   Quantity 26
Rate Breakdown
Line Haul                 1,800.00 USD
Fuel Surcharge              425.50 USD
Total                     2,225.50 USD
"""

ADVERSARIAL_DOCS["adv_total_no_breakdown"] = """\
CARRIER RATE & LOAD CONFIRMATION
Reference ID  ADV-NOB-01
Pickup Date   20-Aug-2026
EQUIPMENT  Flatbed        AGREED AMOUNT (USD)  $2,500.00 USD
Stops
1  Pickup   Nucor Steel, 4537 S Emerald Ave, Chicago, IL 60609, USA
   Shipping Date & Time  08/20/2026
   Commodity  Steel Coil   Weight 44,000 lbs   Quantity 4
2  Drop     Gerdau Yard, 8100 Jackson Rd, Houston, TX 77029, USA
   Delivery Date & Time  08/23/2026
   Commodity  Steel Coil   Weight 44,000 lbs   Quantity 4
Rate Breakdown
Total                     2,500.00 USD
"""

ADVERSARIAL_DOCS["adv_locale_flip"] = """\
CARRIER RATE & LOAD CONFIRMATION
Reference ID  ADV-FLIP-01
EQUIPMENT  Dry Van        AGREED AMOUNT (USD)  $1,450.00 USD
Stops
1  Pickup   Felixstowe Depot, Dock Road, Newark, NJ 07114, USA
   Shipping Date & Time  9/3/26
   Commodity  Machine Parts   Weight 12,000 lbs   Quantity 8
2  Drop     Harrisburg Yard, 3800 Industrial Rd, Harrisburg, PA 17110, USA
   Delivery Date & Time  4/9/26
   Commodity  Machine Parts   Weight 12,000 lbs   Quantity 8
Rate Breakdown
Line Haul                 1,450.00 USD
Total                     1,450.00 USD
"""

ADVERSARIAL_DOCS["adv_hallucination_bait"] = """\
CARRIER RATE & LOAD CONFIRMATION
Reference ID  ADV-HAL-01
EQUIPMENT  Step Deck
Stops
1  Pickup   Port of Savannah Gate 3, Savannah, GA, USA
   Shipping Date & Time  09/02/2026
   Commodity  Granite Slab   Weight 39,500 lbs   Quantity 12
2  Drop     Tampa Marine Terminal, Tampa, FL, USA
   Delivery Date & Time  09/04/2026
   Commodity  Granite Slab   Weight 39,500 lbs   Quantity 12
Rate Breakdown
Total                     3,100.00 USD
"""


def _simple(ref, pickup_date, equip, agreed, stops, charges, total,
            header_pickup=None):
    return {
        "reference_id": loc(ref),
        "header_pickup_date": loc(header_pickup, header_pickup) if header_pickup
        else {"value_raw": None, "span": None},
        "agreed_amount": loc(agreed) if agreed else {"value_raw": None, "span": None},
        "equipment_raw": loc(equip),
        "stops": stops,
        "charges": charges,
        "total_raw": loc(total),
    }


REPLAYS["adv_ambiguous_date"] = _simple(
    "ADV-AMB-01", None, "Dry Van", "$1,900.00 USD",
    [stop(1, "pickup", "Dallas Logistics Hub, 4800 Cleveland Rd, Dallas, TX 75212, USA",
          "3/4/26", [com("Paper Goods", "18,400 lbs", "22")]),
     stop(2, "drop", "Memphis Distribution, 3475 Tulane Rd, Memphis, TN 38116, USA",
          "3/9/26", [com("Paper Goods", "18,400 lbs", "22")])],
    [{"label": "Line Haul", "amount_raw": "1,900.00 USD", "span": "Line Haul"}],
    "1,900.00 USD")

REPLAYS["adv_date_corroborated"] = _simple(
    "ADV-COR-01", None, "Reefer", "$2,650.00 USD",
    [stop(1, "pickup", "Salinas Cold Storage, 900 Work St, Salinas, CA 93901, USA",
          "3/4/26", [com("Lettuce", "41,000 lbs", "24")]),
     stop(2, "drop",
          "Jessup Produce Terminal, 8100 Dorsey Run Rd, Jessup, MD 20794, USA",
          "3/9/26", [com("Lettuce", "41,000 lbs", "24")])],
    [{"label": "Line Haul", "amount_raw": "2,200.00 USD", "span": "Line Haul"},
     {"label": "Fuel Surcharge", "amount_raw": "450.00 USD", "span": "Fuel Surcharge"}],
    "2,650.00 USD", header_pickup="04-Mar-2026")

REPLAYS["adv_real_fsc"] = _simple(
    "ADV-FSC-01", None, "53' Dry Van", "$2,225.50 USD",
    [stop(1, "pickup", "Ryder Yard, 2200 S Wolf Rd, Des Plaines, IL 60018, USA",
          "08/12/2026", [com("Packaged Beverages", "38,200 lbs", "26")]),
     stop(2, "drop", "Kroger DC, 4111 Executive Pkwy, Westerville, OH 43081, USA",
          "08/14/2026", [com("Packaged Beverages", "38,200 lbs", "26")])],
    [{"label": "Line Haul", "amount_raw": "1,800.00 USD", "span": "Line Haul"},
     {"label": "Fuel Surcharge", "amount_raw": "425.50 USD", "span": "Fuel Surcharge"}],
    "2,225.50 USD", header_pickup="12-Aug-2026")

REPLAYS["adv_total_no_breakdown"] = _simple(
    "ADV-NOB-01", None, "Flatbed", "$2,500.00 USD",
    [stop(1, "pickup", "Nucor Steel, 4537 S Emerald Ave, Chicago, IL 60609, USA",
          "08/20/2026", [com("Steel Coil", "44,000 lbs", "4")]),
     stop(2, "drop", "Gerdau Yard, 8100 Jackson Rd, Houston, TX 77029, USA",
          "08/23/2026", [com("Steel Coil", "44,000 lbs", "4")])],
    [], "2,500.00 USD", header_pickup="20-Aug-2026")

REPLAYS["adv_locale_flip"] = _simple(
    "ADV-FLIP-01", None, "Dry Van", "$1,450.00 USD",
    [stop(1, "pickup", "Felixstowe Depot, Dock Road, Newark, NJ 07114, USA",
          "9/3/26", [com("Machine Parts", "12,000 lbs", "8")]),
     stop(2, "drop", "Harrisburg Yard, 3800 Industrial Rd, Harrisburg, PA 17110, USA",
          "4/9/26", [com("Machine Parts", "12,000 lbs", "8")])],
    [{"label": "Line Haul", "amount_raw": "1,450.00 USD", "span": "Line Haul"}],
    "1,450.00 USD")

# Deliberately poisoned: an agreed amount and a header date that are not in
# the document, plus a fuel surcharge line that was never printed. All three
# must be discarded by grounding.
REPLAYS["adv_hallucination_bait"] = {
    "reference_id": loc("ADV-HAL-01"),
    "header_pickup_date": loc("01-Sep-2026", "Pickup Date   01-Sep-2026"),
    "agreed_amount": loc("$3,400.00 USD", "AGREED AMOUNT (USD)  $3,400.00 USD"),
    "equipment_raw": loc("Step Deck"),
    "stops": [
        stop(1, "pickup", "Port of Savannah Gate 3, Savannah, GA, USA", "09/02/2026",
             [com("Granite Slab", "39,500 lbs", "12")]),
        stop(2, "drop", "Tampa Marine Terminal, Tampa, FL, USA", "09/04/2026",
             [com("Granite Slab", "39,500 lbs", "12")]),
    ],
    "charges": [
        {"label": "Fuel Surcharge", "amount_raw": "620.00 USD",
         "span": "Fuel Surcharge   620.00 USD"},
    ],
    "total_raw": loc("3,100.00 USD", "Total"),
}


def main() -> None:
    ADV.mkdir(parents=True, exist_ok=True)
    REPLAY.mkdir(parents=True, exist_ok=True)
    for name, text in ADVERSARIAL_DOCS.items():
        (ADV / f"{name}.txt").write_text(text)
    for name, payload in REPLAYS.items():
        (REPLAY / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {len(ADVERSARIAL_DOCS)} adversarial docs, {len(REPLAYS)} replays")


if __name__ == "__main__":
    main()
