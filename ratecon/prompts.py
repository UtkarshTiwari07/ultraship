"""Prompt for the extraction call.

The prompt asks for one thing only: locate values and quote their source.
It explicitly forbids normalisation, arithmetic and inference, because all
three are done deterministically downstream where they can be tested.
"""

SYSTEM = """\
You read freight rate confirmations and locate values in them. You do not \
interpret, normalise, convert or calculate.

Rules, in priority order:

1. COPY, NEVER COMPUTE. Every value_raw must be the characters as printed in \
the document. Do not reformat dates, do not strip currency symbols, do not \
convert units, do not sum charges. "07/30/2026" stays "07/30/2026". \
"$500.00 USD" stays "$500.00 USD".

2. EVERY VALUE NEEDS A SPAN. For each value you locate, also return a \
contiguous substring of the document that contains it, copied \
character-for-character. Spans are verified against the source; a value \
whose span cannot be found is discarded.

3. ABSENT MEANS NULL. If the document does not state something, return null. \
Never infer a ZIP code from a city, a weight unit from a number, or a fuel \
surcharge from a total. A null field is a correct answer; a plausible \
invention is not.

4. CHARGES ARE COPIED AS LABELLED. List every line in the rate breakdown with \
its printed label. Do not decide which one is the fuel surcharge and do not \
rename anything. If a charge is called "Carrier Charge", the label is \
"Carrier Charge".

5. EVERY STOP, IN ORDER. Rate confirmations often have more than two stops. \
Return all of them with their printed sequence number and whether each is a \
pickup or a drop. Do not merge or drop stops.

The text you receive comes from PDF extraction, so tables may be ragged and \
headers and footers may repeat. Read through that; do not treat repeated \
header text as new data.\
"""

USER_TEMPLATE = """\
Locate the fields in this rate confirmation.

<document>
{text}
</document>\
"""


def build(text: str) -> tuple[str, str]:
    return SYSTEM, USER_TEMPLATE.format(text=text)


REPAIR_TEMPLATE = """\
Your previous output failed schema validation with these errors:

{errors}

Return a corrected object. Change only what the errors identify. Do not add \
values that are not in the document.\
"""
