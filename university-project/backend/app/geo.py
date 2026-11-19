"""Sri Lanka's 25 districts and 9 provinces.

The weights are approximate 2012 census populations in thousands. They only
shape how many simulated payments each district gets, so the demonstration
book is busiest where more people live. They are not fraud statistics.
"""

DISTRICTS: dict[str, tuple[str, int]] = {
    # district: (province, approximate population in thousands)
    "Colombo": ("Western", 2324),
    "Gampaha": ("Western", 2304),
    "Kalutara": ("Western", 1222),
    "Kandy": ("Central", 1375),
    "Matale": ("Central", 484),
    "Nuwara Eliya": ("Central", 711),
    "Galle": ("Southern", 1059),
    "Matara": ("Southern", 809),
    "Hambantota": ("Southern", 599),
    "Jaffna": ("Northern", 584),
    "Kilinochchi": ("Northern", 113),
    "Mannar": ("Northern", 100),
    "Vavuniya": ("Northern", 172),
    "Mullaitivu": ("Northern", 92),
    "Batticaloa": ("Eastern", 526),
    "Ampara": ("Eastern", 649),
    "Trincomalee": ("Eastern", 379),
    "Kurunegala": ("North Western", 1618),
    "Puttalam": ("North Western", 762),
    "Anuradhapura": ("North Central", 856),
    "Polonnaruwa": ("North Central", 403),
    "Badulla": ("Uva", 815),
    "Monaragala": ("Uva", 451),
    "Ratnapura": ("Sabaragamuwa", 1083),
    "Kegalle": ("Sabaragamuwa", 841),
}

PROVINCES: tuple[str, ...] = (
    "Western",
    "Central",
    "Southern",
    "Northern",
    "Eastern",
    "North Western",
    "North Central",
    "Uva",
    "Sabaragamuwa",
)


def province_of(district: str | None) -> str:
    entry = DISTRICTS.get(district or "")
    return entry[0] if entry else "Unknown"


def district_names() -> list[str]:
    return list(DISTRICTS)
