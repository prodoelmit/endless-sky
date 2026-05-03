#!/usr/bin/env python3
"""Run data-quality checks against a running Dolt sql-server hosting the
Endless Sky game database, reporting individual results to TeamCity via
service messages so they show up as proper tests in the build UI.

Used as the payload of the "Test Dolt data" build configuration. Mirrors
the schema discovery the exporter uses (the dolt-vcs TC plugin names the
DB after the Dolt repo, e.g. dolthub/endless-sky -> "endless-sky" or
"endless_sky"), so the same dolt-sql-server build feature can host both.

Tests are written as small SQL probes; each is reported as a single test
case under the "dolt" suite. Exits 0 on full pass, 1 if any test failed.
"""

import argparse
import sys
import time
from typing import Callable, List, Tuple

import mysql.connector


def tc_escape(s: object) -> str:
    return (str(s)
            .replace('|', '||')
            .replace("'", "|'")
            .replace('\n', '|n')
            .replace('\r', '|r')
            .replace('[', '|[')
            .replace(']', '|]'))


def tc(kind: str, **attrs: object) -> None:
    parts = ' '.join(f"{k}='{tc_escape(v)}'" for k, v in attrs.items())
    print(f"##teamcity[{kind} {parts}]", flush=True)


SYSTEM_DBS = {"information_schema", "mysql", "performance_schema", "sys", "dolt"}


def resolve_schema(conn, requested: str) -> str:
    cur = conn.cursor()
    cur.execute("SHOW DATABASES")
    available = {row[0] for row in cur.fetchall()}
    cur.close()
    candidates = [requested, requested.replace("-", "_"), requested.replace("_", "-")]
    for c in candidates:
        if c in available:
            return c
    user_dbs = [d for d in available if d.lower() not in SYSTEM_DBS]
    if len(user_dbs) == 1:
        return user_dbs[0]
    raise RuntimeError(
        f"Schema not found. Tried {candidates}. Available: {sorted(available)}"
    )


# Test cases
# ----------------------------------------------------------------------------

REQUIRED_TABLES = ("color", "galaxy", "star", "outfits", "outfitters",
                   "outfitter_outfits", "weapons", "sprites")


def t_required_tables(cur):
    cur.execute("SHOW TABLES")
    present = {row[0] for row in cur.fetchall()}
    missing = [t for t in REQUIRED_TABLES if t not in present]
    assert not missing, f"missing tables: {missing}"


def t_color_rgba_in_unit_range(cur):
    cur.execute(
        "SELECT name, red, green, blue, alpha FROM color "
        "WHERE red < 0 OR red > 1 OR green < 0 OR green > 1 "
        "OR blue < 0 OR blue > 1 OR alpha < 0 OR alpha > 1"
    )
    rows = cur.fetchall()
    assert not rows, f"out-of-range rgba: {rows[:5]} (total {len(rows)})"


def t_color_unique_names(cur):
    cur.execute(
        "SELECT name, COUNT(*) c FROM color GROUP BY name HAVING c > 1"
    )
    rows = cur.fetchall()
    assert not rows, f"duplicate color names: {rows}"


def t_galaxy_unique_names(cur):
    cur.execute(
        "SELECT name, COUNT(*) c FROM galaxy GROUP BY name HAVING c > 1"
    )
    rows = cur.fetchall()
    assert not rows, f"duplicate galaxy names: {rows}"


def t_galaxy_position_present(cur):
    cur.execute("SELECT name FROM galaxy WHERE posx IS NULL OR posy IS NULL")
    rows = cur.fetchall()
    assert not rows, f"galaxies without position: {rows}"


def t_star_power_non_negative(cur):
    cur.execute("SELECT name, power FROM star WHERE power IS NOT NULL AND power < 0")
    rows = cur.fetchall()
    assert not rows, f"stars with negative power: {rows}"


def t_outfits_unique_names(cur):
    cur.execute(
        "SELECT name, COUNT(*) c FROM outfits GROUP BY name HAVING c > 1"
    )
    rows = cur.fetchall()
    assert not rows, f"duplicate outfit names: {rows}"


def t_outfits_cost_non_negative(cur):
    cur.execute("SELECT name, cost FROM outfits WHERE cost IS NOT NULL AND cost < 0")
    rows = cur.fetchall()
    assert not rows, f"outfits with negative cost: {rows}"


def t_outfitters_unique_names(cur):
    cur.execute(
        "SELECT name, COUNT(*) c FROM outfitters GROUP BY name HAVING c > 1"
    )
    rows = cur.fetchall()
    assert not rows, f"duplicate outfitter names: {rows}"


def t_outfitter_outfits_resolve(cur):
    cur.execute("""
        SELECT oo.outfitter_name_fk
        FROM outfitter_outfits oo
        LEFT JOIN outfitters o ON o.name = oo.outfitter_name_fk
        WHERE o.name IS NULL
        LIMIT 5
    """)
    bad_outfitter = cur.fetchall()
    cur.execute("""
        SELECT oo.outfit_name_fk
        FROM outfitter_outfits oo
        LEFT JOIN outfits f ON f.name = oo.outfit_name_fk
        WHERE f.name IS NULL
        LIMIT 5
    """)
    bad_outfit = cur.fetchall()
    assert not (bad_outfitter or bad_outfit), (
        f"outfitter_outfits has unresolved refs: "
        f"outfitter={bad_outfitter}, outfit={bad_outfit}"
    )


def t_weapons_sprite_resolves(cur):
    cur.execute("""
        SELECT w.id
        FROM weapons w
        LEFT JOIN sprites s ON s.id = w.sprite_id_fk
        WHERE w.sprite_id_fk IS NOT NULL AND s.id IS NULL
        LIMIT 5
    """)
    rows = cur.fetchall()
    assert not rows, f"weapons referencing missing sprites: {rows}"


def t_outfit_weapon_resolves(cur):
    cur.execute("""
        SELECT o.name
        FROM outfits o
        LEFT JOIN weapons w ON w.id = o.weapon_id_fk
        WHERE o.weapon_id_fk IS NOT NULL AND w.id IS NULL
        LIMIT 5
    """)
    rows = cur.fetchall()
    assert not rows, f"outfits referencing missing weapons: {rows}"


TESTS: List[Tuple[str, Callable]] = [
    ("schema.required_tables", t_required_tables),
    ("color.rgba_in_unit_range", t_color_rgba_in_unit_range),
    ("color.unique_names", t_color_unique_names),
    ("galaxy.unique_names", t_galaxy_unique_names),
    ("galaxy.position_present", t_galaxy_position_present),
    ("star.power_non_negative", t_star_power_non_negative),
    ("outfits.unique_names", t_outfits_unique_names),
    ("outfits.cost_non_negative", t_outfits_cost_non_negative),
    ("outfitters.unique_names", t_outfitters_unique_names),
    ("outfitter_outfits.foreign_keys_resolve", t_outfitter_outfits_resolve),
    ("weapons.sprite_resolves", t_weapons_sprite_resolves),
    ("outfits.weapon_resolves", t_outfit_weapon_resolves),
]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3306)
    p.add_argument("--user", default="dolt")
    p.add_argument("--password", default="")
    p.add_argument("--schema", default="endless-sky")
    p.add_argument("--suite", default="dolt")
    args = p.parse_args()

    try:
        conn = mysql.connector.connect(
            host=args.host, port=args.port,
            user=args.user, password=args.password,
        )
    except mysql.connector.Error as e:
        sys.stderr.write(f"Failed to connect to {args.host}:{args.port}: {e}\n")
        return 1

    try:
        schema = resolve_schema(conn, args.schema)
    except RuntimeError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    if schema != args.schema:
        print(f"Note: using schema '{schema}' (asked for '{args.schema}')")
    conn.database = schema

    tc("testSuiteStarted", name=args.suite)
    failed = 0
    for full, fn in TESTS:
        tc("testStarted", name=full, captureStandardOutput="true")
        t0 = time.monotonic()
        try:
            cur = conn.cursor()
            try:
                fn(cur)
            finally:
                cur.close()
        except AssertionError as e:
            tc("testFailed", name=full, message=str(e) or "assertion failed")
            failed += 1
        except Exception as e:
            tc("testFailed", name=full,
               message=f"{type(e).__name__}: {e}")
            failed += 1
        finally:
            duration_ms = int((time.monotonic() - t0) * 1000)
            tc("testFinished", name=full, duration=str(duration_ms))

    tc("testSuiteFinished", name=args.suite)
    conn.close()

    total = len(TESTS)
    print(f"\n{total - failed}/{total} tests passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
