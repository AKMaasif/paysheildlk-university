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

# Approximate district centres (latitude, longitude), for placing a customer on the map.
COORDS: dict[str, tuple[float, float]] = {
    "Colombo": (6.9271, 79.8612), "Gampaha": (7.0873, 79.9925), "Kalutara": (6.5854, 79.9607), "Kandy": (7.2906, 80.6337),
    "Matale": (7.4675, 80.6234), "Nuwara Eliya": (6.9497, 80.7891), "Galle": (6.0329, 80.2168), "Matara": (5.9549, 80.5550),
    "Hambantota": (6.1241, 81.1185), "Jaffna": (9.6615, 80.0255), "Kilinochchi": (9.3803, 80.3770), "Mannar": (8.9810, 79.9044),
    "Vavuniya": (8.7514, 80.4971), "Mullaitivu": (9.2671, 80.8142), "Batticaloa": (7.7102, 81.6924), "Ampara": (7.2991, 81.6747),
    "Trincomalee": (8.5874, 81.2152), "Kurunegala": (7.4863, 80.3647), "Puttalam": (8.0408, 79.8394), "Anuradhapura": (8.3114, 80.4037),
    "Polonnaruwa": (7.9403, 81.0188), "Badulla": (6.9934, 81.0550), "Monaragala": (6.8728, 81.3507), "Ratnapura": (6.6828, 80.3992),
    "Kegalle": (7.2513, 80.3464),
}


def coordinates_of(district: str | None) -> tuple[float, float] | None:
    return COORDS.get(district or "")


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
