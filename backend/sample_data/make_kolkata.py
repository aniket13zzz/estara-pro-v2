# ruff: noqa: E501  (data tables read better on one line)
"""Generate the FAKE demo inventory in backend/sample_data/kolkata (synthetic_demo_data).

    python sample_data/make_kolkata.py tests/fixtures/sample_inventory sample_data/kolkata

Every project is fictional: names end in "(Sample)", descriptions start with
"SAMPLE DATA (synthetic_demo_data)". The output is committed and loaded with `python -m app.seed`
(repeatable: a second run re-imports the same rows as unchanged). To use a developer's real
inventory instead, upload their CSVs in the dashboard's Data Center (same columns); this file
and the folder are then no longer needed.
"""

import csv
import sys
from pathlib import Path

SRC = Path(sys.argv[1])  # the small test fixture (kept as the first three projects, unchanged)
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

# code, name, locality, address, description, possession
PROJECTS = [
    (
        "SN",
        "Sunrise Nest (Sample)",
        "New Town Action Area I",
        "Sample Street 4, New Town, Kolkata",
        "SAMPLE DATA (synthetic_demo_data). Not a real development. Compact, ready-to-move homes near the metro.",
        "2026-06-30",
    ),
    (
        "SR",
        "Sunrise Riverside (Sample)",
        "Howrah",
        "Sample Street 5, Shibpur, Howrah",
        "SAMPLE DATA (synthetic_demo_data). Not a real development. Affordable river-view homes.",
        "2027-03-31",
    ),
    (
        "SS",
        "Sunrise Skyline (Sample)",
        "Salt Lake Sector V",
        "Sample Street 6, Salt Lake, Kolkata",
        "SAMPLE DATA (synthetic_demo_data). Not a real development. Luxury high-rise near the IT hub.",
        "2028-12-31",
    ),
    (
        "SV",
        "Sunrise Villas (Sample)",
        "Joka",
        "Sample Street 7, Joka, Kolkata",
        "SAMPLE DATA (synthetic_demo_data). Not a real development. Gated community of independent villas.",
        "2027-09-30",
    ),
    (
        "SM",
        "Sunrise Meadows (Sample)",
        "Garia",
        "Sample Street 8, Garia, Kolkata",
        "SAMPLE DATA (synthetic_demo_data). Not a real development. Green township near the metro.",
        "2026-12-31",
    ),
    (
        "SB",
        "Sunrise Bypass Residency (Sample)",
        "Kasba",
        "Sample Street 9, EM Bypass, Kolkata",
        "SAMPLE DATA (synthetic_demo_data). Not a real development. Premium homes on the EM Bypass.",
        "2027-06-30",
    ),
    (
        "SD",
        "Sunrise Dum Dum Plaza (Sample)",
        "Dum Dum",
        "Sample Street 10, Dum Dum, Kolkata",
        "SAMPLE DATA (synthetic_demo_data). Not a real development. Ready homes near the airport.",
        "2025-12-31",
    ),
]

# project, type code, name, property_type, bhk, carpet, super, base, max, units [(no, floor, price, availability, facing)]
TYPES = [
    (
        "SN",
        "1BHK-N",
        "1 BHK Smart",
        "apartment",
        1,
        480,
        640,
        3600000,
        3900000,
        [("N-104", 1, 3600000, "available", "North"), ("N-508", 5, 3850000, "available", "East")],
    ),
    (
        "SN",
        "2BHK-N",
        "2 BHK Smart",
        "apartment",
        2,
        740,
        970,
        5800000,
        6800000,
        [
            ("N-202", 2, 5800000, "available", "East"),
            ("N-604", 6, 6300000, "available", "South"),
            ("N-901", 9, 6800000, "available", "East"),
            ("N-305", 3, 6000000, "booked", "West"),
        ],
    ),
    (
        "SN",
        "3BHK-N",
        "3 BHK Smart",
        "apartment",
        3,
        1020,
        1330,
        8400000,
        9200000,
        [("N-702", 7, 8600000, "available", "North")],
    ),
    (
        "SR",
        "1BHK-R",
        "1 BHK River",
        "apartment",
        1,
        450,
        600,
        3200000,
        3500000,
        [("R-103", 1, 3200000, "available", "West"), ("R-407", 4, 3450000, "available", "West")],
    ),
    (
        "SR",
        "2BHK-R",
        "2 BHK River",
        "apartment",
        2,
        700,
        920,
        4800000,
        5800000,
        [("R-210", 2, 4800000, "available", "West"), ("R-811", 8, 5600000, "available", "West")],
    ),
    (
        "SS",
        "3BHK-S",
        "3 BHK Sky",
        "apartment",
        3,
        1500,
        1950,
        16000000,
        19500000,
        [
            ("S-1201", 12, 16500000, "available", "East"),
            ("S-2203", 22, 19000000, "available", "North"),
        ],
    ),
    (
        "SS",
        "4BHK-S",
        "4 BHK Sky",
        "apartment",
        4,
        2100,
        2730,
        24000000,
        28000000,
        [("S-1804", 18, 25000000, "available", "East")],
    ),
    (
        "SS",
        "PH-S",
        "Sky Penthouse",
        "penthouse",
        4,
        2800,
        3640,
        30000000,
        32000000,
        [("S-3001", 30, 31000000, "available", "East")],
    ),
    (
        "SV",
        "3BHK-V",
        "3 BHK Garden Villa",
        "villa",
        3,
        1650,
        2150,
        14000000,
        16500000,
        [("V-07", 0, 14000000, "available", "South"), ("V-12", 0, 15500000, "available", "East")],
    ),
    (
        "SV",
        "4BHK-V",
        "4 BHK Grand Villa",
        "villa",
        4,
        2300,
        3000,
        21000000,
        24000000,
        [("V-21", 0, 22000000, "available", "North")],
    ),
    (
        "SM",
        "2BHK-M",
        "2 BHK Meadow",
        "apartment",
        2,
        780,
        1020,
        4800000,
        6200000,
        [
            ("M-201", 2, 4800000, "available", "East"),
            ("M-509", 5, 5500000, "available", "North"),
            ("M-803", 8, 6100000, "available", "East"),
        ],
    ),
    (
        "SM",
        "3BHK-M",
        "3 BHK Meadow",
        "apartment",
        3,
        1080,
        1400,
        7800000,
        9000000,
        [("M-606", 6, 8200000, "available", "South"), ("M-1004", 10, 8900000, "available", "East")],
    ),
    (
        "SB",
        "2BHK-B",
        "2 BHK Bypass",
        "apartment",
        2,
        880,
        1150,
        8500000,
        9500000,
        [("B-402", 4, 8700000, "available", "West")],
    ),
    (
        "SB",
        "3BHK-B",
        "3 BHK Bypass",
        "apartment",
        3,
        1250,
        1630,
        12000000,
        14500000,
        [
            ("B-705", 7, 12500000, "available", "East"),
            ("B-1102", 11, 14000000, "available", "North"),
        ],
    ),
    (
        "SD",
        "1BHK-D",
        "1 BHK Plaza",
        "apartment",
        1,
        500,
        660,
        3800000,
        4200000,
        [("D-110", 1, 3800000, "available", "South")],
    ),
    (
        "SD",
        "2BHK-D",
        "2 BHK Plaza",
        "apartment",
        2,
        760,
        990,
        5600000,
        6200000,
        [("D-305", 3, 5600000, "available", "East"), ("D-502", 5, 6000000, "available", "North")],
    ),
]

# --- v1.9.0: more synthetic projects so the demo has 100+ homes across Kolkata -------------
# code, name, locality, address, description (furnishing, bathrooms, parking), possession
MORE_PROJECTS = [
    (
        "SA",
        "Sunrise Arcadia (Sample)",
        "New Town Action Area III",
        "Sample Street 11, New Town",
        "Semi-furnished 2 and 3 BHK homes, 2 to 3 bathrooms, one covered parking each.",
        "2027-09-30",
    ),
    (
        "SP",
        "Sunrise Prime (Sample)",
        "New Town Action Area I",
        "Sample Street 12, New Town",
        "Premium 3 and 4 BHK homes and a penthouse, 3 to 4 bathrooms, two covered parkings.",
        "2028-03-31",
    ),
    (
        "SJ",
        "Sunrise Junction (Sample)",
        "Rajarhat",
        "Sample Street 13, Chinar Park, Rajarhat",
        "Ready-to-move 1, 2 and 3 BHK homes, unfurnished, open and covered parking.",
        "2025-09-30",
    ),
    (
        "SC",
        "Sunrise Central (Sample)",
        "Salt Lake Sector I",
        "Sample Street 14, Salt Lake",
        "Fully furnished 3 and 4 BHK homes, 3 to 4 bathrooms, two covered parkings.",
        "2027-03-31",
    ),
    (
        "SW",
        "Sunrise Waterfront (Sample)",
        "Howrah",
        "Sample Street 15, Santragachi, Howrah",
        "Semi-furnished 2 and 3 BHK homes near the river, 2 bathrooms, covered parking.",
        "2026-12-31",
    ),
    (
        "SK",
        "Sunrise Ballygunge (Sample)",
        "Ballygunge",
        "Sample Street 16, Ballygunge, Kolkata",
        "Luxury fully furnished 3 and 4 BHK homes and a penthouse, two covered parkings.",
        "2027-12-31",
    ),
    (
        "ST",
        "Sunrise Tollygunge (Sample)",
        "Tollygunge",
        "Sample Street 17, Tollygunge, Kolkata",
        "Unfurnished 2 and 3 BHK homes near the metro, 2 bathrooms, open parking.",
        "2026-09-30",
    ),
    (
        "SE",
        "Sunrise Eco Villas (Sample)",
        "New Town Action Area II",
        "Sample Street 18, New Town",
        "Independent 3 and 4 BHK villas with private gardens, 3 to 4 bathrooms, garage.",
        "2028-06-30",
    ),
    (
        "SQ",
        "Sunrise Baguiati (Sample)",
        "Baguiati",
        "Sample Street 19, Baguiati, Kolkata",
        "Affordable unfurnished 1 and 2 BHK homes, 1 to 2 bathrooms, two-wheeler parking.",
        "2026-06-30",
    ),
]
PROJECTS += [
    (c, n, loc, addr, "SAMPLE DATA (synthetic_demo_data). Not a real development. " + d, pos)
    for c, n, loc, addr, d, pos in MORE_PROJECTS
]

# project, type code, name, property_type, bhk, carpet, super, base price, max price, unit count
MORE_TYPES = [
    ("SA", "2BHK-AR", "2 BHK Arcadia", "apartment", 2, 820, 1070, 6200000, 7200000, 6),
    ("SA", "3BHK-AR", "3 BHK Arcadia", "apartment", 3, 1150, 1500, 8800000, 10200000, 6),
    ("SP", "3BHK-PR", "3 BHK Prime", "apartment", 3, 1400, 1820, 13500000, 15500000, 5),
    ("SP", "4BHK-PR", "4 BHK Prime", "apartment", 4, 1900, 2470, 19500000, 22500000, 4),
    ("SP", "PH-PR", "Prime Penthouse", "penthouse", 4, 2600, 3380, 28000000, 30000000, 1),
    ("SJ", "1BHK-JN", "1 BHK Junction", "apartment", 1, 470, 620, 3400000, 3800000, 4),
    ("SJ", "2BHK-JN", "2 BHK Junction", "apartment", 2, 760, 990, 5200000, 6000000, 6),
    ("SJ", "3BHK-JN", "3 BHK Junction", "apartment", 3, 1060, 1380, 7600000, 8600000, 5),
    ("SC", "3BHK-CN", "3 BHK Central", "apartment", 3, 1500, 1950, 17000000, 19500000, 4),
    ("SC", "4BHK-CN", "4 BHK Central", "apartment", 4, 2000, 2600, 24000000, 27000000, 3),
    ("SW", "2BHK-WF", "2 BHK Waterfront", "apartment", 2, 740, 960, 4600000, 5400000, 6),
    ("SW", "3BHK-WF", "3 BHK Waterfront", "apartment", 3, 1050, 1370, 6800000, 7800000, 5),
    ("SK", "3BHK-BG", "3 BHK Ballygunge", "apartment", 3, 1650, 2150, 26000000, 30000000, 3),
    ("SK", "4BHK-BG", "4 BHK Ballygunge", "apartment", 4, 2300, 3000, 38000000, 44000000, 2),
    ("SK", "PH-BG", "Ballygunge Penthouse", "penthouse", 5, 3400, 4400, 62000000, 65000000, 1),
    ("ST", "2BHK-TG", "2 BHK Tollygunge", "apartment", 2, 780, 1010, 5600000, 6400000, 5),
    ("ST", "3BHK-TG", "3 BHK Tollygunge", "apartment", 3, 1100, 1430, 8200000, 9200000, 4),
    ("SE", "3BHK-EV", "3 BHK Eco Villa", "villa", 3, 1800, 2350, 16500000, 18500000, 3),
    ("SE", "4BHK-EV", "4 BHK Eco Villa", "villa", 4, 2400, 3100, 23000000, 26000000, 2),
    ("SQ", "1BHK-BQ", "1 BHK Baguiati", "apartment", 1, 430, 570, 2800000, 3200000, 5),
    ("SQ", "2BHK-BQ", "2 BHK Baguiati", "apartment", 2, 680, 880, 4200000, 4900000, 6),
]
# Mostly available, with some booked, sold and blocked homes, in a fixed repeating pattern.
STATUS_CYCLE = [
    "available",
    "available",
    "booked",
    "available",
    "sold",
    "available",
    "blocked",
    "available",
]
FACINGS = ["East", "North", "South", "West"]
_n = 0
for p, t, name, ptype, bhk, carpet, sup, base, top, count in MORE_TYPES:
    units = []
    for i in range(count):
        price = base + round((top - base) * i / max(count - 1, 1) / 10000) * 10000
        floor = 0 if ptype == "villa" else (2 + 3 * i) % 20 + 1
        number = f"{p[1]}{t[0]}-{100 * (floor or 1) + i + 1}"
        units.append(
            (number, floor, price, STATUS_CYCLE[_n % len(STATUS_CYCLE)], FACINGS[_n % len(FACINGS)])
        )
        _n += 1
    TYPES.append((p, t, name, ptype, bhk, carpet, sup, base, top, units))

OFFERS = [
    (
        "SN",
        "Ready-Home Offer",
        "Sample offer: no floor-rise charge on ready homes.",
        "100000",
        "",
        "2026-10-01",
        "2027-01-31",
    ),
    (
        "SM",
        "Metro Launch Offer",
        "Sample offer: modular kitchen included in 2 and 3 BHK homes.",
        "150000",
        "",
        "2026-10-15",
        "2027-03-31",
    ),
    (
        "SS",
        "Early Bird Offer",
        "Sample offer: discount for bookings in the launch phase.",
        "",
        "2",
        "2026-11-01",
        "2027-02-28",
    ),
    (
        "SA",
        "Arcadia Launch Offer",
        "Sample offer: no floor-rise charge in the launch phase.",
        "120000",
        "",
        "2026-11-01",
        "2027-03-31",
    ),
    (
        "SJ",
        "Ready Home Offer",
        "Sample offer: registration charges support on ready homes.",
        "150000",
        "",
        "2026-10-01",
        "2027-02-28",
    ),
]
PLANS = [
    ("SN", "Ready to Move Plan", "For completed homes.", "Booking:10; Registration:90"),
    (
        "SR",
        "Construction Linked Plan",
        "Pay as construction progresses.",
        "Booking:10; Agreement:15; Structure:45; Possession:30",
    ),
    (
        "SS",
        "Construction Linked Plan",
        "Pay as construction progresses.",
        "Booking:10; Agreement:10; Foundation:10; Structure:40; Possession:30",
    ),
    (
        "SS",
        "Flexi Plan",
        "Lower payments until possession.",
        "Booking:10; Agreement:20; Possession:70",
    ),
    (
        "SV",
        "Construction Linked Plan",
        "Pay as construction progresses.",
        "Booking:10; Agreement:15; Structure:45; Possession:30",
    ),
    (
        "SM",
        "Construction Linked Plan",
        "Pay as construction progresses.",
        "Booking:10; Agreement:15; Structure:45; Possession:30",
    ),
    (
        "SB",
        "Down Payment Plan",
        "Most of the amount at agreement.",
        "Booking:10; Agreement:85; Possession:5",
    ),
    ("SD", "Ready to Move Plan", "For completed homes.", "Booking:10; Registration:90"),
    (
        "SA",
        "Construction Linked Plan",
        "Pay as construction progresses.",
        "Booking:10; Agreement:15; Structure:45; Possession:30",
    ),
    (
        "SP",
        "Construction Linked Plan",
        "Pay as construction progresses.",
        "Booking:10; Agreement:10; Foundation:10; Structure:40; Possession:30",
    ),
    ("SJ", "Ready to Move Plan", "For completed homes.", "Booking:10; Registration:90"),
    (
        "SC",
        "Down Payment Plan",
        "Most of the amount at agreement.",
        "Booking:10; Agreement:85; Possession:5",
    ),
    (
        "SW",
        "Construction Linked Plan",
        "Pay as construction progresses.",
        "Booking:10; Agreement:15; Structure:45; Possession:30",
    ),
    (
        "SK",
        "Flexi Plan",
        "Lower payments until possession.",
        "Booking:10; Agreement:20; Possession:70",
    ),
    (
        "ST",
        "Construction Linked Plan",
        "Pay as construction progresses.",
        "Booking:10; Agreement:15; Structure:45; Possession:30",
    ),
    (
        "SE",
        "Construction Linked Plan",
        "Pay as construction progresses.",
        "Booking:10; Agreement:15; Structure:45; Possession:30",
    ),
    (
        "SQ",
        "Construction Linked Plan",
        "Pay as construction progresses.",
        "Booking:10; Agreement:15; Structure:45; Possession:30",
    ),
]
AMENITIES = {
    "SN": [
        ("Lift", "Utilities"),
        ("Power backup", "Utilities"),
        ("Gym", "Fitness"),
        ("Rooftop garden", "Leisure"),
    ],
    "SR": [("Riverside walkway", "Leisure"), ("Kids play area", "Family"), ("Lift", "Utilities")],
    "SS": [
        ("Infinity pool", "Leisure"),
        ("Sky lounge", "Leisure"),
        ("Gym", "Fitness"),
        ("Concierge", "Services"),
        ("EV charging", "Utilities"),
    ],
    "SV": [
        ("Private garden", "Leisure"),
        ("Clubhouse", "Leisure"),
        ("Swimming pool", "Leisure"),
        ("24x7 security", "Safety"),
    ],
    "SM": [
        ("Swimming pool", "Leisure"),
        ("Jogging track", "Fitness"),
        ("Kids play area", "Family"),
        ("Community hall", "Leisure"),
    ],
    "SB": [("Gym", "Fitness"), ("Swimming pool", "Leisure"), ("Covered parking", "Utilities")],
    "SD": [("Lift", "Utilities"), ("Power backup", "Utilities"), ("Kids play area", "Family")],
    "SA": [
        ("Lift", "Utilities"),
        ("Covered parking", "Utilities"),
        ("Gym", "Fitness"),
        ("Kids play area", "Family"),
    ],
    "SP": [
        ("Swimming pool", "Leisure"),
        ("Clubhouse", "Leisure"),
        ("Covered parking", "Utilities"),
        ("Concierge", "Services"),
    ],
    "SJ": [("Lift", "Utilities"), ("Power backup", "Utilities"), ("Covered parking", "Utilities")],
    "SC": [
        ("Swimming pool", "Leisure"),
        ("Gym", "Fitness"),
        ("Covered parking", "Utilities"),
        ("24x7 security", "Safety"),
    ],
    "SW": [
        ("Riverside walkway", "Leisure"),
        ("Covered parking", "Utilities"),
        ("Lift", "Utilities"),
    ],
    "SK": [
        ("Rooftop pool", "Leisure"),
        ("Sky lounge", "Leisure"),
        ("Covered parking", "Utilities"),
        ("Concierge", "Services"),
    ],
    "ST": [("Lift", "Utilities"), ("Open parking", "Utilities"), ("Community hall", "Leisure")],
    "SE": [
        ("Private garden", "Leisure"),
        ("Garage", "Utilities"),
        ("Clubhouse", "Leisure"),
        ("24x7 security", "Safety"),
    ],
    "SQ": [
        ("Lift", "Utilities"),
        ("Two-wheeler parking", "Utilities"),
        ("Kids play area", "Family"),
    ],
}


def copy_rows(name: str) -> tuple[list[str], list[list[str]]]:
    with (SRC / name).open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


def write(name: str, header: list[str], rows: list[list[str]]) -> None:
    with (OUT / name).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    print(f"{name}: {len(rows)} rows")


h, rows = copy_rows("projects.csv")
for row in rows:  # the fixture's first three projects get the same synthetic label
    row[5] = row[5].replace("SAMPLE DATA for testing.", "SAMPLE DATA (synthetic_demo_data).")
for code, name, locality, address, desc, possession in PROJECTS:
    rows.append(
        [
            code,
            name,
            "Kolkata",
            locality,
            address,
            desc,
            f"SAMPLE-RERA-{len(rows) + 1:04d}",
            possession,
            "",
            "",
        ]
    )
write("projects.csv", h, rows)

h, rows = copy_rows("unit_types.csv")
for p, t, name, ptype, bhk, carpet, sup, *_ in TYPES:
    rows.append([p, t, name, ptype, str(bhk), str(carpet), str(sup), "", ""])
write("unit_types.csv", h, rows)

h, rows = copy_rows("units.csv")
for p, t, _, ptype, bhk, carpet, _, base, top, units in TYPES:
    for no, floor, price, avail, facing in units:
        assert base <= price <= top, (no, price)
        rows.append(
            [p, no, t, str(floor), str(bhk), ptype, str(carpet), str(price), avail, facing, "", ""]
        )
write("units.csv", h, rows)

h, rows = copy_rows("price_lists.csv")
for p, t, _, _, _, carpet, _, base, top, _ in TYPES:
    rows.append(
        [
            p,
            t,
            "",
            str(base),
            str(top),
            str(round(base / carpet / 100) * 100),
            "Sample price list",
            "",
            "",
        ]
    )
write("price_lists.csv", h, rows)

h, rows = copy_rows("offers.csv")
rows += [list(o) for o in OFFERS]
write("offers.csv", h, rows)

h, rows = copy_rows("payment_plans.csv")
rows += [[*p, "", ""] for p in PLANS]
write("payment_plans.csv", h, rows)

h, rows = copy_rows("amenities.csv")
for p, items in AMENITIES.items():
    rows += [[p, n, c, "", ""] for n, c in items]
write("amenities.csv", h, rows)
