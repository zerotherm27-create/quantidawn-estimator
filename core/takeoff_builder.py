"""Turns classified legend elements into grouped TakeoffLines (structural trade).

A Profile says which groups a project's takeoff uses; the derivation rules are shared.
Anything that cannot be derived from the legend alone (footing reo tonnage, panels,
blinding rules that vary by project) is returned in `skipped` rather than guessed.
"""
from dataclasses import dataclass
from typing import Optional

from core.legend import classify
from core.models import TakeoffLine


@dataclass
class Profile:
    name: str
    exc: str
    conc: str
    form: str
    poly: str
    sand: str
    precast: Optional[str] = None      # None: precast columns go into `conc`
    pt: Optional[str] = None
    reo_mesh: Optional[str] = None
    blind_pads_h: Optional[float] = None   # concrete blinding under pads/strips (m), if the project takes it off
    blind: Optional[str] = None
    joints: bool = False
    wall_form_both_faces: bool = True


WESTGARTH = Profile(
    "westgarth", exc="DETAILED EXCAVATION", conc="SUPPLY AND INSTALL (CONCRETE)",
    form="FORMWORKS", poly="0.2mm POLYTHENE VAPOUR BARRIER", sand="50mm SAND BEDDING",
    precast="PRECAST", pt="POST TENSION")
BOXSHALL = Profile(
    "boxshall", exc="DETAILED EXCAVATION", conc="CONCRETE SUPPLY AND INSTALL",
    form="FORMWORKS", poly="BLINDING", sand="BLINDING", reo_mesh="REINFORCEMENT",
    pt="POST TENSION REINFORCEMENT", blind="BLINDING", blind_pads_h=0.3, joints=True)
PROFILES = {p.name: p for p in (WESTGARTH, BOXSHALL)}


def group_order(profile):
    seq = [profile.exc, profile.poly, profile.sand, profile.blind, profile.conc,
           profile.precast, profile.reo_mesh, profile.pt, profile.form]
    out = []
    for g in seq:
        if g and g not in out:
            out.append(g)
    return out


def build_takeoff(entries, profile=WESTGARTH):
    p = profile
    lines, skipped = [], []

    cur = {"i": -1}

    def add(group, el, unit, calc="", **kw):
        lines.append(TakeoffLine(group=group, mark=el.mark or el.kind.upper(), description=el.label,
                                 unit=unit, kind=el.kind, element=cur["i"], calc=calc, **kw))

    def footing(el, area, depth, vol_kw):
        """Excavation, bedding, concrete (and blinding) for any footing with plan area + depth."""
        add(p.exc, el, "m3", "prism", height=depth, **vol_kw, volume=area * depth)
        if p.blind_pads_h:
            add(p.blind, el, "m3", "prism", **{**vol_kw, "area": None if "length" in vol_kw else area},
                height=p.blind_pads_h, volume=area * p.blind_pads_h)
        else:
            add(p.poly, el, "m2", "plan", height=depth, **{**vol_kw, "area": area})
            add(p.sand, el, "m2", "plan", height=depth, **{**vol_kw, "area": area})
        add(p.conc, el, "m3", "prism", height=depth, **vol_kw, volume=area * depth)

    for idx, e in enumerate(entries):
        cur["i"] = idx
        el = classify(e)
        d, q, u = el.dims, el.quantity, el.unit
        if el.kind == "strip_footing" and {"width", "depth"} <= set(d) and u == "M":
            footing(el, q * d["width"], d["depth"], dict(length=q, width=d["width"]))
        elif el.kind == "pad_footing" and {"length", "width", "depth"} <= set(d):
            L, W, H = d["length"], d["width"], d["depth"]
            if u == "EA":
                footing(el, L * W * q, H, dict(length=L, width=W, count=q))
            elif u == "SQ M":
                footing(el, q, H, dict(area=q))
            else:
                skipped.append((el.label, "unexpected pad footing unit " + u))
        elif el.kind == "raft_footing" and "depth" in d and u == "SQ M":
            footing(el, q, d["depth"], dict(area=q))
        elif el.kind == "bulk_excavation" and "depth" in d and u == "SQ M":
            add(p.exc, el, "m3", "prism", height=d["depth"], area=q, volume=q * d["depth"])
        elif el.kind in ("slab_on_ground", "ramp_slab") and u == "SQ M":
            if el.kind == "slab_on_ground" and not p.blind_pads_h:
                add(p.poly, el, "m2", "plan", area=q)
                add(p.sand, el, "m2", "plan", area=q)
            t = d.get("thickness")
            if t:
                add(p.conc, el, "m3", "prism", area=q, height=t, volume=q * t)
            if p.reo_mesh and "MESH" in el.label.upper():
                add(p.reo_mesh, el, "m2", "plan", area=q)
        elif el.kind == "sand_blinding" and u == "SQ M" and d.get("thickness"):
            add(p.sand, el, "m3", "prism", area=q, height=d["thickness"], volume=q * d["thickness"])
        elif el.kind == "poly_membrane" and u == "SQ M":
            add(p.poly, el, "m2", "plan", area=q)
        elif el.kind == "edge_form" and d.get("thickness") and u == "M":
            add(p.form, el, "m2", "edge", length=q, height=d["thickness"], area=q * d["thickness"])
        elif el.kind == "joint" and u == "M" and p.joints:
            add(p.conc, el, "m", "len", length=q)
        elif el.kind == "pt_slab" and u == "SQ M" and d.get("kg_m2") and p.pt:
            add(p.pt, el, "tons", "pt", area=q, count=q * d["kg_m2"] / 1000, k=d["kg_m2"])
        elif el.kind == "column" and {"length", "width", "height"} <= set(d) and u == "EA":
            L, W, H = d["length"], d["width"], d["height"]
            add(p.precast or p.conc, el, "m3", "prism", length=L, width=W, height=H, count=q,
                volume=L * W * H * q)
        elif el.kind == "wall" and {"thickness", "height"} <= set(d) and u == "M":
            t, H = d["thickness"], d["height"]
            add(p.conc, el, "m3", "prism", length=q, width=t, height=H, volume=q * t * H)
            add(p.form, el, "m2", "face2", length=q, width=t, height=H, area=q * 2 * H)
        elif el.kind == "hob" and {"width", "height"} <= set(d) and u == "M":
            W, H = d["width"], d["height"]
            add(p.conc, el, "m3", "prism", length=q, width=W, height=H, volume=q * W * H)
            add(p.form, el, "m2", "face2", length=q, width=W, height=H, area=q * 2 * H)
        else:
            skipped.append((el.label, f"no rule for {el.kind} ({u})"))
    order = group_order(p)
    lines.sort(key=lambda l: order.index(l.group))
    return lines, skipped
