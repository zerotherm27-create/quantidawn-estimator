from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class TakeoffLine:
    group: str
    mark: str
    description: str
    unit: str
    length: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    diameter: Optional[float] = None
    area: Optional[float] = None
    count: Optional[float] = None
    volume: Optional[float] = None
    source_page: Optional[int] = None
    confidence: Optional[float] = None
    status: str = "PENDING"
    kind: str = ""            # element kind: strip_footing, pad_footing, slab_on_ground ...
    element: int = -1         # index of the legend entry / measured element this line derives from
    calc: str = ""            # prism | plan | edge | face2 | len | pt  (see core/calc.py)
    k: Optional[float] = None # kg per m2 for post-tension lines, kg per m for bar lines
    openings: Optional[float] = None  # m2 taken off a wall area (doors, windows) by the wall calc
    factor: Optional[float] = None    # a per-unit multiplier (bricks per m2, screws per m); 1 when absent
    rate: Optional[float] = None  # unit rate, only when pricing is switched on for the project
    rate_source: str = ""        # where that rate came from (price list entry, accepted, typed by hand)
    labour_hours: Optional[float] = None   # labour hours per unit of quantity
    labour_rate: Optional[float] = None    # labour $ per hour

    def quantity(self) -> Optional[float]:
        """The headline quantity in this line's own unit."""
        unit = (self.unit or "").lower()
        if unit == "m3":
            return self.volume
        if unit in ("m2", "sq m"):
            return self.area
        if unit in ("m", "lm"):
            return self.length
        return self.count if self.count is not None else self.volume

    def to_dict(self):
        return asdict(self)
