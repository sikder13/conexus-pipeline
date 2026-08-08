"""Indiana county drive-time estimates, measured from Muncie.

The pipeline filters prospects by whether an in-person visit is practical:
roughly 90 minutes from Muncie, Indiana. That decision needs a number for
every Indiana county, and it needs it offline, for free, and identically on
every run — so this module is a static lookup table, not a routing call.

HOW THE NUMBERS WERE PRODUCED
County interior points come from the US Census Bureau 2023 National
Counties Gazetteer (INTPTLAT / INTPTLONG), filtered to Indiana. Each is
converted to a drive-time estimate as:

    great_circle_miles(Muncie, county_centroid) * 1.22 / 58 mph * 60

The 1.22 circuity factor and 58 mph effective speed were fitted against
thirteen known Muncie-to-county-seat driving times; mean absolute error is
about 5 minutes, worst case about 17 (Tippecanoe, where a direct highway
beats the straight-line assumption).

WHAT THIS IS NOT
These are ESTIMATES, centroid to centroid, suitable only for a coarse
"is this inside a ~90 minute radius" filter. They are not route planning,
they are not door to door, and they must never be quoted to a prospect or
used to promise an arrival time. A company sitting near a county line can
easily be 20 minutes off its county's number.
"""

from __future__ import annotations

MUNCIE_LAT = 40.1934
MUNCIE_LNG = -85.3864

DRIVE_RADIUS_MINUTES = 90
"""The threshold the in_drive_radius scoring signal uses."""

# County name (as the Census writes it) -> estimated minutes from Muncie.
COUNTY_DRIVE_MINUTES: dict[str, int] = {
    'Adams': 57,
    'Allen': 81,
    'Bartholomew': 93,
    'Benton': 133,
    'Blackford': 25,
    'Boone': 73,
    'Brown': 104,
    'Carroll': 85,
    'Cass': 81,
    'Clark': 151,
    'Clay': 135,
    'Clinton': 73,
    'Crawford': 181,
    'Daviess': 173,
    'DeKalb': 108,
    'Dearborn': 96,
    'Decatur': 78,
    'Delaware': 3,
    'Dubois': 188,
    'Elkhart': 127,
    'Fayette': 50,
    'Floyd': 167,
    'Fountain': 123,
    'Franklin': 72,
    'Fulton': 95,
    'Gibson': 221,
    'Grant': 33,
    'Greene': 148,
    'Hamilton': 44,
    'Hancock': 41,
    'Harrison': 182,
    'Hendricks': 84,
    'Henry': 23,
    'Howard': 55,
    'Huntington': 56,
    'Jackson': 120,
    'Jasper': 135,
    'Jay': 33,
    'Jefferson': 123,
    'Jennings': 106,
    'Johnson': 77,
    'Knox': 190,
    'Kosciusko': 97,
    'LaGrange': 126,
    'LaPorte': 148,
    'Lake': 172,
    'Lawrence': 139,
    'Madison': 23,
    'Marion': 62,
    'Marshall': 115,
    'Martin': 161,
    'Miami': 67,
    'Monroe': 118,
    'Montgomery': 101,
    'Morgan': 94,
    'Newton': 149,
    'Noble': 106,
    'Ohio': 113,
    'Orange': 162,
    'Owen': 124,
    'Parke': 126,
    'Perry': 202,
    'Pike': 200,
    'Porter': 160,
    'Posey': 253,
    'Pulaski': 114,
    'Putnam': 108,
    'Randolph': 25,
    'Ripley': 96,
    'Rush': 50,
    'Scott': 134,
    'Shelby': 64,
    'Spencer': 220,
    'St. Joseph': 138,
    'Starke': 126,
    'Steuben': 129,
    'Sullivan': 167,
    'Switzerland': 122,
    'Tippecanoe': 102,
    'Tipton': 46,
    'Union': 59,
    'Vanderburgh': 241,
    'Vermillion': 142,
    'Vigo': 150,
    'Wabash': 63,
    'Warren': 133,
    'Warrick': 223,
    'Washington': 147,
    'Wayne': 38,
    'Wells': 49,
    'White': 109,
    'Whitley': 83,}

_NORMALIZED = {
    "".join(ch for ch in name.lower() if ch.isalnum()): name
    for name in COUNTY_DRIVE_MINUTES
}


def canonical_county(county: str | None) -> str | None:
    """Return the Census spelling of an Indiana county, or None if unrecognised.

    Accepts the slug forms the Conexus site uses ('st-joseph', 'dekalb') and
    the spelled-out forms ('St. Joseph County'), because the two source pages
    disagree with each other about both.
    """
    if not county:
        return None
    cleaned = county.strip().removesuffix(" County").removesuffix(" county")
    key = "".join(ch for ch in cleaned.lower() if ch.isalnum())
    return _NORMALIZED.get(key)


def drive_minutes_from_muncie(county: str | None) -> int | None:
    """Estimated drive time in minutes, or None when the county is unrecognised.

    None is a real answer here: an unrecognised county means we do not know
    where the company is, which is a reason for a human to look, not a reason
    to guess a number.
    """
    canonical = canonical_county(county)
    return COUNTY_DRIVE_MINUTES[canonical] if canonical else None


def within_drive_radius(county: str | None) -> bool:
    """True only when the county is recognised AND inside the radius."""
    minutes = drive_minutes_from_muncie(county)
    return minutes is not None and minutes <= DRIVE_RADIUS_MINUTES
