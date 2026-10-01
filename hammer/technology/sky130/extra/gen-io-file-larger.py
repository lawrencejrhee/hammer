#!/usr/bin/env python3
"""Generate a Cadence IO file from a YAML IO-ring description.

Regular pad locations are calculated from the explicit pin number plus the
per-side slot count, offset, and pitch in the YAML file; there is no table of
physical locations in this script. Unlisted pin numbers remain empty for the
IO-filler flow. The
``pins`` mapping contains ordered ``top``, ``right``, ``bottom``, and ``left``
mappings with explicit pin numbers (for example, ``N1`` or ``E70``). Changing
the value of a numbered pin manually assigns that IO cell. Named mapping entries
with ``cell`` and ``width`` fields are physical-only fillers. They do not consume
a pitch slot. The optional ``abut`` field selects the ``previous`` or ``next``
numbered pad in mapping order; its default preserves each side's local-right
guard placement.

Power/clamp pads are emitted as physical-only ``clamp_N`` instances with an
explicit ``cell``. Signal-pad names follow the synthesis netlist hierarchy:

* Scalar: ``iocell_<signal_name>/iocell``
* Bus bit 0: ``iocell_<base_name>/iocell``
* Bus bit N, N > 0: ``iocell_<base_name>_<N>/iocell``
* ``reset_io``: ``iocell_reset/iocell``
* ``nc_N``: ``nc_N/iocell``

"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

import yaml


ALL_SIDES = ("top", "right", "bottom", "left")

# Short names accepted in side lists in addition to the iocell_type keys.
TYPE_ALIASES = {
    "vssd": "vss_core",
    "vssio": "vss_io",
    "vccd": "vdd_core",
    "vddio": "vdd_io",
}

SIDE_ORIENT = {
    "top": "R0",
    "right": "R270",
    "bottom": "R180",
    "left": "R90",
}

SIDE_PREFIX = {
    "top": "N",
    "right": "E",
    "bottom": "S",
    "left": "W",
}

CORNER_ORIENT = {
    "topleft": "R90",
    "topright": "R0",
    "bottomright": "R270",
    "bottomleft": "R180",
}

# YAML signal name -> Chisel wrapper instance name. Only names that differ
# from the normal iocell_<signal_name> convention belong here.
SPECIAL_WRAPPER_NAMES = {
    "reset_io": "iocell_reset",
}


@dataclass
class Slot:
    pin: str
    side: str
    pad_type: str
    offset: int | float


@dataclass
class Filler:
    name: str
    side: str
    cell: str
    width: int | float
    abut: str
    offset: int | float = 0


SideEntry = Union[Slot, Filler]


def signal_to_netlist_inst(signal_name: str) -> str:
    """Convert a YAML signal name to its synthesis leaf-pad instance path."""
    if re.fullmatch(r"nc_\d+", signal_name):
        return f"{signal_name}/iocell"

    if signal_name in SPECIAL_WRAPPER_NAMES:
        return f"{SPECIAL_WRAPPER_NAMES[signal_name]}/iocell"

    match = re.fullmatch(r"(.+)\[(\d+)\]", signal_name)
    if match:
        base, index = match.group(1), int(match.group(2))
        suffix = "" if index == 0 else f"_{index}"
        return f"iocell_{base}{suffix}/iocell"

    return f"iocell_{signal_name}/iocell"


def resolve_cell(pad_type: str, iocell_type: dict[str, str]) -> tuple[str | None, bool]:
    """Return ``(cell_name, is_power)`` for a side-list entry."""
    key = TYPE_ALIASES.get(pad_type, pad_type)
    if key in iocell_type and key not in {"corner", "gpio", "reset"}:
        return iocell_type[key], True
    return None, False


def require_number(value: Any, description: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{description} must be a number, got {value!r}")
    return value


def load_entries(cfg: dict[str, Any]) -> tuple[list[Slot], dict[str, list[SideEntry]]]:
    """Validate the pin configuration and construct ordered side entries."""
    pitch = cfg.get("pitch")
    if not isinstance(pitch, dict):
        raise ValueError("configuration must contain a 'pitch' object")

    pins = cfg.get("pins")
    if not isinstance(pins, dict):
        raise ValueError("configuration must contain a directional 'pins' mapping")
    unknown_sides = set(pins) - set(ALL_SIDES)
    if unknown_sides:
        raise ValueError(f"unknown pins sides: {sorted(unknown_sides)}")

    pad_width = require_number(pitch.get("pad_width"), "pitch.pad_width")
    slots: list[Slot] = []
    side_entries: dict[str, list[SideEntry]] = {}
    for side in ALL_SIDES:
        side_pins = pins.get(side)
        if not isinstance(side_pins, dict):
            raise ValueError(f"'pins.{side}' must be a mapping")

        offset_start = require_number(pitch.get(f"{side}_offset"), f"pitch.{side}_offset")
        side_pitch = require_number(pitch.get(f"{side}_pitch"), f"pitch.{side}_pitch")

        if not all(isinstance(name, str) for name in side_pins):
            raise ValueError(f"pins.{side} entry names must be strings")
        numbered_names = [
            name for name, value in side_pins.items() if isinstance(value, str)
        ]
        if not numbered_names:
            raise ValueError(f"'pins.{side}' must contain at least one numbered pin")
        pin_numbers: dict[str, int] = {}
        for name in numbered_names:
            match = re.fullmatch(rf"{SIDE_PREFIX[side]}([1-9]\d*)", name)
            if match is None:
                raise ValueError(
                    f"pins.{side} numbered entry {name!r} must use the "
                    f"{SIDE_PREFIX[side]}<positive-number> form"
                )
            pin_numbers[name] = int(match.group(1))

        ordered_numbers = [pin_numbers[name] for name in numbered_names]
        reverse = side in {"right", "left"}
        if ordered_numbers != sorted(ordered_numbers, reverse=reverse):
            direction = "descending" if reverse else "ascending"
            raise ValueError(
                f"pins.{side} numbered entries must be in {direction} pin-number order"
            )

        slot_count_value = pitch.get(f"{side}_slots", max(ordered_numbers))
        if (
            isinstance(slot_count_value, bool)
            or not isinstance(slot_count_value, int)
            or slot_count_value < max(ordered_numbers)
        ):
            raise ValueError(
                f"pitch.{side}_slots must be an integer at least "
                f"{max(ordered_numbers)}"
            )
        slot_count = slot_count_value

        entries: list[SideEntry] = []
        for name, value in side_pins.items():
            if isinstance(value, str):
                pin_number = pin_numbers[name]
                offset_index = (
                    pin_number - 1
                    if side in {"top", "bottom"}
                    else slot_count - pin_number
                )
                slot = Slot(
                    name,
                    side,
                    value,
                    offset_start + offset_index * side_pitch,
                )
                slots.append(slot)
                entries.append(slot)
                continue

            if not isinstance(value, dict):
                raise ValueError(
                    f"pins.{side}.{name} must be a pad-type string or filler mapping"
                )
            cell = value.get("cell")
            if not isinstance(cell, str):
                raise ValueError(f"pins.{side}.{name}.cell must be a string")
            width = require_number(value.get("width"), f"pins.{side}.{name}.width")
            default_abut = "previous" if side in {"top", "left"} else "next"
            abut = value.get("abut", default_abut)
            if abut not in {"previous", "next"}:
                raise ValueError(
                    f"pins.{side}.{name}.abut must be 'previous' or 'next'"
                )
            unknown_fields = set(value) - {"cell", "width", "abut"}
            if unknown_fields:
                raise ValueError(
                    f"pins.{side}.{name} has unknown fields: {sorted(unknown_fields)}"
                )
            entries.append(Filler(name, side, cell, width, abut))

        cursor: int | float | None = None
        for entry in entries:
            if isinstance(entry, Slot):
                cursor = entry.offset + pad_width
            elif entry.abut == "previous":
                if cursor is None:
                    raise ValueError(
                        f"pins.{side}.{entry.name} needs a numbered pin before it"
                    )
                entry.offset = cursor
                cursor += entry.width
            else:
                cursor = None

        cursor = None
        for entry in reversed(entries):
            if isinstance(entry, Slot):
                cursor = entry.offset
            elif entry.abut == "next":
                if cursor is None:
                    raise ValueError(
                        f"pins.{side}.{entry.name} needs a numbered pin after it"
                    )
                entry.offset = cursor - entry.width
                cursor = entry.offset
            else:
                cursor = None

        side_entries[side] = entries

    return slots, side_entries


def load_slots(cfg: dict[str, Any]) -> list[Slot]:
    """Return only the numbered IO slots from a validated configuration."""
    return load_entries(cfg)[0]


def is_signal(slot: Slot, iocell_type: dict[str, str]) -> bool:
    return not resolve_cell(slot.pad_type, iocell_type)[1]


def validate_unique_signals(slots: list[Slot], iocell_type: dict[str, str]) -> None:
    locations: dict[str, str] = {}
    for slot in slots:
        if not is_signal(slot, iocell_type):
            continue
        path = signal_to_netlist_inst(slot.pad_type)
        if path in locations:
            raise ValueError(
                f"duplicate signal instance {path!r} at "
                f"pins.{locations[path]} and pins.{slot.side}.{slot.pin}"
            )
        locations[path] = f"{slot.side}.{slot.pin}"


def normalize_design_inst(name: str) -> str:
    return name if name.endswith("/iocell") else f"{name}/iocell"


def validate_design_info(
    slots: list[Slot], iocell_type: dict[str, str], design_info: list[dict[str, Any]]
) -> None:
    generated = {
        signal_to_netlist_inst(slot.pad_type)
        for slot in slots
        if is_signal(slot, iocell_type)
    }
    design = set()
    for item in design_info:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("each design-info entry must contain a string 'name'")
        design.add(normalize_design_inst(item["name"]))

    if generated != design:
        print(f"Insts in design but not IO config: {sorted(design - generated)}", file=sys.stderr)
        print(f"Insts in IO config but not design: {sorted(generated - design)}", file=sys.stderr)
        raise ValueError("instances do not match between design info and IO configuration")


def format_inst(attrs: dict[str, str | int | float]) -> str:
    fields = []
    for key, value in attrs.items():
        rendered = f'"{value}"' if isinstance(value, str) and key in {"name", "cell"} else str(value)
        separator = " = " if key == "name" else "="
        fields.append(f"{key}{separator}{rendered}")
    return "        (inst " + "   ".join(fields) + ")"


def generate_iofile(
    cfg: dict[str, Any],
    design_info: list[dict[str, Any]] | None = None,
) -> tuple[str, int, int]:
    """Generate IO-file text and return it with clamp/signal counts."""
    iocell_type = cfg.get("iocell_type")
    if not isinstance(iocell_type, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in iocell_type.items()
    ):
        raise ValueError("configuration must contain an 'iocell_type' string mapping")
    for required in ("corner", "gpio", "reset"):
        if required not in iocell_type:
            raise ValueError(f"iocell_type is missing required key {required!r}")

    slots, side_entries = load_entries(cfg)
    validate_unique_signals(slots, iocell_type)
    if design_info is not None:
        validate_design_info(slots, iocell_type, design_info)

    out = [
        "",
        "(globals",
        "    version = 3",
        "    io_order = default",
        ")",
        "(row_margin",
    ]
    for side in ALL_SIDES:
        out.extend(
            [
                f"    ({side}",
                "    (io_row ring_number = 1 margin = 0)",
                "    )",
            ]
        )
    out.extend([
        ")",
        "(iopad",
    ])

    section_order = (
        ("corner", "topleft"),
        ("side", "top"),
        ("corner", "topright"),
        ("side", "right"),
        ("corner", "bottomright"),
        ("side", "bottom"),
        ("corner", "bottomleft"),
        ("side", "left"),
    )

    clamp_count = 0
    signal_count = 0
    for kind, name in section_order:
        if kind == "corner":
            out.extend(
                [
                    f"    ({name}",
                    "    (locals ring_number = 1)",
                    format_inst(
                        {
                            "name": f"corner_{name}",
                            "orientation": CORNER_ORIENT[name],
                            "cell": iocell_type["corner"],
                        }
                    ),
                    "    )",
                    "",
                ]
            )
            continue

        side = name
        out.extend([f"    ({side}", "    (locals ring_number = 1)"])
        for slot in side_entries[side]:
            if isinstance(slot, Filler):
                out.append(
                    format_inst(
                        {
                            "name": slot.name,
                            "orientation": SIDE_ORIENT[side],
                            "cell": slot.cell,
                            "offset": slot.offset,
                        }
                    )
                )
                continue
            cell, is_power = resolve_cell(slot.pad_type, iocell_type)
            if is_power:
                assert cell is not None
                attrs: dict[str, str | int | float] = {
                    "name": f"clamp_{clamp_count}",
                    "orientation": SIDE_ORIENT[side],
                    "cell": cell,
                    "offset": slot.offset,
                }
                clamp_count += 1
            else:
                signal_cell = (
                    iocell_type["reset"]
                    if signal_to_netlist_inst(slot.pad_type) == "iocell_reset/iocell"
                    else iocell_type["gpio"]
                )
                attrs = {
                    "name": signal_to_netlist_inst(slot.pad_type),
                    "orientation": SIDE_ORIENT[side],
                    "cell": signal_cell,
                    "offset": slot.offset,
                }
                signal_count += 1
            out.append(format_inst(attrs))
        out.extend(["    )", ""])

    out.append(")")
    return "\n".join(out) + "\n", clamp_count, signal_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config",
        type=Path,
        help="YAML file containing cells, pitch, directional pins, and inline fillers",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="output .io path (default: the YAML path with a .io suffix)",
    )
    parser.add_argument(
        "--design-info",
        "-d",
        type=Path,
        help="optional synthesis sky130io JSON/YAML used to validate signal instances",
    )
    args = parser.parse_args()

    with args.config.open() as stream:
        cfg = yaml.safe_load(stream) or {}
    if not isinstance(cfg, dict):
        raise ValueError("top-level YAML configuration must be a mapping")

    design_info = None
    if args.design_info is not None:
        with args.design_info.open() as stream:
            design_info = yaml.safe_load(stream)
        if not isinstance(design_info, list):
            raise ValueError("top-level design-info YAML must be a list")

    out_path = args.output or args.config.with_suffix(".io")
    text, clamp_count, signal_count = generate_iofile(cfg, design_info)
    out_path.write_text(text)

    side_counts = " ".join(
        f"{side}={sum(isinstance(value, str) for value in cfg['pins'][side].values())}"
        for side in ALL_SIDES
    )
    print(f"#IOs {side_counts}")
    print(f"Generated {out_path}")
    print(f"  {clamp_count} clamp pads, {signal_count} signal pads")


if __name__ == "__main__":
    main()
