#!/usr/bin/env python3
"""Export Endless Sky game data from a running dolt sql-server into .txt files
that the game's vanilla DataFile parser can consume.

Used as the payload of the "Export Dolt Data" build configuration in TeamCity.
The resulting files are published as a build artifact and consumed by the game
build (see scripts/README-pipeline.md).

Assumes a `dolt sql-server` is reachable. Mirrors the SQL queries the game
itself ran against Dolt (see source/UniverseObjects.cpp pre-strip), so the
output is a faithful translation of the live database into the game's native
text format.
"""

import argparse
import json
import os
import sys
from typing import Any, IO, Iterable, List

try:
    import mysql.connector
except ImportError:
    sys.stderr.write(
        "mysql-connector-python is required.\n"
        "Install it with: python3 -m pip install mysql-connector-python\n"
    )
    sys.exit(2)


# DataFile token quoting
# ----------------------------------------------------------------------------

_NEEDS_QUOTING = set(' \t')


def needs_quotes(token: str) -> bool:
    if token == "":
        return True
    return any(c in _NEEDS_QUOTING for c in token)


def quote(token: str) -> str:
    """Return a token as it should appear in a DataFile line."""
    s = str(token)
    if '"' in s or '`' in s or '\n' in s:
        # Backtick-quote strings that contain double quotes or newlines so the
        # DataFile parser keeps them as a single token.
        if '`' not in s:
            return f"`{s}`"
        # Last resort: replace embedded backticks with single quotes so we can
        # still wrap with backticks. Lossy but rare in practice.
        return "`" + s.replace("`", "'") + "`"
    if needs_quotes(s):
        return f'"{s}"'
    return s


def fmt_num(v: Any) -> str:
    """Format a number the way DataFile readers (and Format::Number) expect."""
    if isinstance(v, bool):
        # Bool is also an int; handle it before the int branch.
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        # Strip trailing zeros from a fixed-precision rendering.
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        return s if s else "0"
    return str(v)


def write_line(out: IO[str], indent: int, *tokens: Any) -> None:
    out.write("\t" * indent)
    out.write(" ".join(quote(t) for t in tokens))
    out.write("\n")


# JSON -> DataNode emission
# ----------------------------------------------------------------------------

# Keys whose value is a sprite-shaped object (name + animation attrs).
_SPRITE_KEYS = {
    "sprite",
    "hardpoint sprite",
    "flare sprite",
    "reverse flare sprite",
    "steering flare sprite",
}

# Keys whose value is an effect — either a bare name string, or an object with
# {"args": ["name", count]}.
_EFFECT_KEYS = {
    "fire effect",
    "live effect",
    "hit effect",
    "target effect",
    "die effect",
    "afterburner effect",
    "jump effect",
}

# Keys whose value is a list of two numbers laid out on the same line.
_POINT_KEYS = {"hardpoint offset", "damage dropoff"}


def emit_sprite(out: IO[str], indent: int, key: str, value: dict) -> None:
    """Emit a sprite-shaped JSON object as `<key> "<name>"` plus child attrs."""
    name = value.get("name", "")
    write_line(out, indent, key, name)
    for k, v in value.items():
        if k == "name":
            continue
        if isinstance(v, bool):
            if v:
                write_line(out, indent + 1, k)
        elif isinstance(v, (int, float)):
            write_line(out, indent + 1, k, fmt_num(v))
        else:
            write_line(out, indent + 1, k, v)


def emit_effect(out: IO[str], indent: int, key: str, value: Any) -> None:
    if isinstance(value, str):
        write_line(out, indent, key, value)
        return
    if isinstance(value, dict) and "args" in value and isinstance(value["args"], list):
        args = value["args"]
        # First positional arg is the name; optional count comes second.
        toks: List[Any] = [key]
        if args:
            toks.append(args[0])
        if len(args) > 1 and isinstance(args[1], (int, float)):
            toks.append(fmt_num(args[1]))
        write_line(out, indent, *toks)
        return
    # Unknown shape — drop a comment so it's visible in the output.
    out.write("\t" * indent + f"# unrecognized effect '{key}': {json.dumps(value)}\n")


def emit_point(out: IO[str], indent: int, key: str, value: Any) -> None:
    if isinstance(value, dict) and "args" in value and isinstance(value["args"], list):
        nums = [fmt_num(a) for a in value["args"] if isinstance(a, (int, float))]
        write_line(out, indent, key, *nums)
        return
    if isinstance(value, list):
        nums = [fmt_num(a) for a in value if isinstance(a, (int, float))]
        write_line(out, indent, key, *nums)
        return
    write_line(out, indent, key, value)


def emit_submunition(out: IO[str], indent: int, items: Any) -> None:
    if not isinstance(items, list):
        return
    for entry in items:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        count = entry.get("count")
        toks: List[Any] = ["submunition", name]
        if isinstance(count, (int, float)):
            toks.append(fmt_num(count))
        write_line(out, indent, *toks)
        if "facing" in entry:
            write_line(out, indent + 1, "facing", fmt_num(entry["facing"]))
        if "offset" in entry:
            offset = entry["offset"]
            offset_args = offset.get("args") if isinstance(offset, dict) else offset
            if isinstance(offset_args, list) and len(offset_args) >= 2:
                write_line(
                    out,
                    indent + 1,
                    "offset",
                    fmt_num(offset_args[0]),
                    fmt_num(offset_args[1]),
                )


def emit_attribute(out: IO[str], indent: int, key: str, value: Any) -> None:
    """Emit a single (key, value) pair from a JSON attributes blob."""
    if key in _SPRITE_KEYS and isinstance(value, dict):
        emit_sprite(out, indent, key, value)
        return
    if key in _EFFECT_KEYS:
        emit_effect(out, indent, key, value)
        return
    if key in _POINT_KEYS:
        emit_point(out, indent, key, value)
        return
    if key == "submunition":
        emit_submunition(out, indent, value)
        return
    if key == "licenses" and isinstance(value, list):
        write_line(out, indent, "licenses")
        for entry in value:
            write_line(out, indent + 1, entry)
        return
    if key == "ammo":
        # Either a bare name or an args-list with [name, count].
        if isinstance(value, str):
            write_line(out, indent, "ammo", value)
            return
        if isinstance(value, dict) and isinstance(value.get("args"), list):
            args = value["args"]
            toks: List[Any] = ["ammo"]
            if args:
                toks.append(args[0])
            if len(args) > 1 and isinstance(args[1], (int, float)):
                toks.append(fmt_num(args[1]))
            write_line(out, indent, *toks)
            return

    # Generic shapes.
    if isinstance(value, bool):
        if value:
            write_line(out, indent, key)
    elif isinstance(value, (int, float)):
        write_line(out, indent, key, fmt_num(value))
    elif isinstance(value, str):
        write_line(out, indent, key, value)
    elif value is None:
        write_line(out, indent, key)
    else:
        # Arrays of primitives, unknown objects: best-effort serialization.
        out.write("\t" * indent + f"# unrecognized attribute '{key}': {json.dumps(value)}\n")


def emit_json_attributes(out: IO[str], indent: int, attrs_text: str) -> None:
    """Parse a JSON attributes blob and emit each key as a child node."""
    if not attrs_text:
        return
    try:
        attrs = json.loads(attrs_text)
    except json.JSONDecodeError as e:
        out.write("\t" * indent + f"# could not parse attributes JSON: {e}\n")
        return
    if not isinstance(attrs, dict):
        return
    for k, v in attrs.items():
        emit_attribute(out, indent, k, v)


# Per-table exporters
# ----------------------------------------------------------------------------

def export_colors(cursor, out_dir: str) -> int:
    cursor.execute(
        "SELECT name, red, green, blue, alpha FROM color ORDER BY name;"
    )
    rows = cursor.fetchall()
    path = os.path.join(out_dir, "color.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Generated by scripts/export-dolt-to-data.py from the `color` table.\n\n")
        for r in rows:
            write_line(
                f,
                0,
                "color",
                r["name"],
                fmt_num(r["red"]),
                fmt_num(r["green"]),
                fmt_num(r["blue"]),
                fmt_num(r["alpha"]),
            )
    return len(rows)


def export_galaxies(cursor, out_dir: str) -> int:
    cursor.execute(
        "SELECT name, posx AS x, posy AS y, sprite FROM galaxy ORDER BY name;"
    )
    rows = cursor.fetchall()
    path = os.path.join(out_dir, "galaxies.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Generated by scripts/export-dolt-to-data.py from the `galaxy` table.\n\n")
        for r in rows:
            write_line(f, 0, "galaxy", r["name"])
            if r["x"] is not None and r["y"] is not None:
                write_line(f, 1, "pos", fmt_num(r["x"]), fmt_num(r["y"]))
            if r["sprite"]:
                write_line(f, 1, "sprite", r["sprite"])
            f.write("\n")
    return len(rows)


def export_stars(cursor, out_dir: str) -> int:
    cursor.execute("SELECT name, power, wind FROM star ORDER BY name;")
    rows = cursor.fetchall()
    path = os.path.join(out_dir, "stars.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Generated by scripts/export-dolt-to-data.py from the `star` table.\n\n")
        for r in rows:
            write_line(f, 0, "star", r["name"])
            if r["power"] is not None:
                write_line(f, 1, "power", fmt_num(r["power"]))
            if r["wind"] is not None:
                write_line(f, 1, "wind", fmt_num(r["wind"]))
            f.write("\n")
    return len(rows)


def export_outfitters(cursor, out_dir: str) -> int:
    cursor.execute(
        "SELECT outfitters.name AS name, "
        "outfitter_outfits.outfit_name_fk AS outfit_name "
        "FROM outfitters "
        "LEFT JOIN outfitter_outfits "
        "  ON outfitters.name = outfitter_outfits.outfitter_name_fk "
        "ORDER BY outfitters.name, outfit_name;"
    )
    rows = cursor.fetchall()
    grouped: dict = {}
    for r in rows:
        grouped.setdefault(r["name"], []).append(r["outfit_name"])
    path = os.path.join(out_dir, "outfitters.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Generated by scripts/export-dolt-to-data.py from `outfitters` + `outfitter_outfits`.\n\n")
        for name in sorted(grouped):
            write_line(f, 0, "outfitter", name)
            for outfit_name in grouped[name]:
                if outfit_name:
                    write_line(f, 1, outfit_name)
            f.write("\n")
    return len(grouped)


def export_outfits(cursor, out_dir: str) -> int:
    cursor.execute(
        "SELECT outfits.name AS name, "
        "outfits.category AS category, "
        "outfits.description AS description, "
        "outfits.thumbnail AS thumbnail, "
        "outfits.cost AS cost, "
        "outfits.mass AS mass, "
        "outfits.attributes AS attributes, "
        "outfits.weapon_id_fk AS weapon_id, "
        "weapons.lifetime AS lifetime, "
        "weapons.velocity AS velocity, "
        "weapons.reload AS reload, "
        "weapons.firing_energy AS firing_energy, "
        "weapons.firing_heat AS firing_heat, "
        "weapons.inaccuracy AS inaccuracy, "
        "weapons.shield_damage AS shield_damage, "
        "weapons.hull_damage AS hull_damage, "
        "weapons.attributes AS weapon_attributes, "
        "sprites.name AS sprite_name, "
        "sprites.frame_time AS frame_time, "
        "sprites.delay AS delay, "
        "sprites.scale AS scale, "
        "sprites.frame_rate AS frame_rate, "
        "sprites.random_start_frame AS random_start_frame, "
        "sprites.rewind AS rewind, "
        "sprites.no_repeat AS no_repeat "
        "FROM outfits "
        "LEFT JOIN weapons ON outfits.weapon_id_fk = weapons.id "
        "LEFT JOIN sprites ON sprites.id = weapons.sprite_id_fk "
        "ORDER BY outfits.name;"
    )
    rows = cursor.fetchall()
    path = os.path.join(out_dir, "outfits.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Generated by scripts/export-dolt-to-data.py from `outfits`/`weapons`/`sprites`.\n\n")
        for r in rows:
            write_line(f, 0, "outfit", r["name"])
            if r["category"]:
                write_line(f, 1, "category", r["category"])
            if r["cost"] is not None:
                write_line(f, 1, "cost", fmt_num(r["cost"]))
            if r["mass"] is not None:
                write_line(f, 1, "mass", fmt_num(r["mass"]))
            if r["thumbnail"]:
                write_line(f, 1, "thumbnail", r["thumbnail"])
            if r["description"]:
                # description is free-form text; backtick-quote preserves embedded spaces.
                write_line(f, 1, "description", r["description"])

            emit_json_attributes(f, 1, r["attributes"] or "")

            if r["weapon_id"] is not None:
                write_line(f, 1, "weapon")
                if r["sprite_name"]:
                    sprite_value = {"name": r["sprite_name"]}
                    for src_key, json_key in (
                        ("frame_time", "frame time"),
                        ("delay", "delay"),
                        ("scale", "scale"),
                        ("frame_rate", "frame rate"),
                        ("random_start_frame", "random start frame"),
                        ("rewind", "rewind"),
                        ("no_repeat", "no repeat"),
                    ):
                        if r.get(src_key) is not None:
                            sprite_value[json_key] = r[src_key]
                    emit_sprite(f, 2, "sprite", sprite_value)
                if r["lifetime"] is not None:
                    write_line(f, 2, "lifetime", fmt_num(r["lifetime"]))
                if r["velocity"] is not None:
                    write_line(f, 2, "velocity", fmt_num(r["velocity"]))
                if r["reload"] is not None:
                    write_line(f, 2, "reload", fmt_num(r["reload"]))
                if r["firing_energy"] is not None:
                    write_line(f, 2, "firing energy", fmt_num(r["firing_energy"]))
                if r["firing_heat"] is not None:
                    write_line(f, 2, "firing heat", fmt_num(r["firing_heat"]))
                if r["inaccuracy"] is not None:
                    write_line(f, 2, "inaccuracy", fmt_num(r["inaccuracy"]))
                if r["shield_damage"] is not None:
                    write_line(f, 2, "shield damage", fmt_num(r["shield_damage"]))
                if r["hull_damage"] is not None:
                    write_line(f, 2, "hull damage", fmt_num(r["hull_damage"]))
                emit_json_attributes(f, 2, r["weapon_attributes"] or "")
            f.write("\n")
    return len(rows)


# CLI
# ----------------------------------------------------------------------------

def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3306)
    p.add_argument("--user", default="dolt")
    p.add_argument("--password", default="")
    p.add_argument("--schema", default="datadb")
    p.add_argument("--out", required=True, help="Output directory for the .txt files")
    args = p.parse_args(list(argv) if argv is not None else None)

    os.makedirs(args.out, exist_ok=True)

    try:
        conn = mysql.connector.connect(
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            database=args.schema,
        )
    except mysql.connector.Error as e:
        sys.stderr.write(f"Failed to connect to {args.host}:{args.port}/{args.schema}: {e}\n")
        return 1

    try:
        cursor = conn.cursor(dictionary=True)
        counts = {
            "color.txt": export_colors(cursor, args.out),
            "galaxies.txt": export_galaxies(cursor, args.out),
            "stars.txt": export_stars(cursor, args.out),
            "outfitters.txt": export_outfitters(cursor, args.out),
            "outfits.txt": export_outfits(cursor, args.out),
        }
        cursor.close()
    finally:
        conn.close()

    for name, n in counts.items():
        print(f"{name}: {n} row(s)")

    if not all(counts.values()):
        empty = [n for n, c in counts.items() if not c]
        sys.stderr.write(f"Refusing to publish: empty result for {empty}\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
