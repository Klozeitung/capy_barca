"""
Store the times of day of all date values in UTC, once.

Before 0.17.1.0 a time of day was stored as entered, without a zone. Since
then it is stored in UTC and shown in each account's zone (see
app.timezones). This command converts the values written before, reading
each as local time in the given zone with the daylight saving time of its
own date: a January time with the winter offset, an August time with the
summer offset. On screen nothing changes.

It converts every time of day of every date property: flat values, the slots
of timelines, entry templates, and the property snapshots that entries moved
out of a database carry in ``content.schemaMemory``. Values without a time of
day (all-day dates) and timeline period keys stay as they are.

A value already stored in UTC ends in ``Z`` and is left alone, so running the
command again changes nothing.

Usage (inside the backend container)::

    docker compose exec backend python -m app.maintenance.convert_dates_to_utc --dry-run
    docker compose exec backend python -m app.maintenance.convert_dates_to_utc

``--timezone`` names the zone the old values were entered in; it defaults to
``CB_TIMEZONE``. The system alerts of calendar dates are brought in line
afterwards.
"""
import argparse
import sys
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timezones


@dataclass
class Report:
    """What a run converted, or would convert in a dry run."""

    values: int = 0
    snapshots: int = 0

    def __str__(self) -> str:
        return (
            f"{self.values} date values and {self.snapshots} moved-entry snapshots "
            "with times of day to convert"
        )


def _convert_snapshot(content: Any, zone: Any) -> Optional[dict]:
    """*content* with the date values of its ``schemaMemory`` in UTC, or None if unchanged."""
    if not isinstance(content, dict) or not isinstance(content.get("schemaMemory"), list):
        return None
    changed = False
    memory = []
    for item in content["schemaMemory"]:
        if (
            isinstance(item, dict)
            and item.get("type") == "date"
            and timezones.date_value_needs_utc(item.get("value"))
        ):
            item = {**item, "value": timezones.date_value_to_utc(item["value"], zone)}
            changed = True
        memory.append(item)
    return {**content, "schemaMemory": memory} if changed else None


def convert(db: Session, zone: Any, *, dry_run: bool = False) -> Report:
    """Convert every local time of day in *db*; commits unless *dry_run*."""
    from app.blocks.models import Block, PropertySchema, PropertyValue

    report = Report()
    rows = db.scalars(
        select(PropertyValue)
        .join(PropertySchema, PropertySchema.id == PropertyValue.property_schema_id)
        .where(PropertySchema.type == "date")
    )
    for row in rows:
        if timezones.date_value_needs_utc(row.value):
            report.values += 1
            if not dry_run:
                row.value = timezones.date_value_to_utc(row.value, zone)

    for block in db.scalars(select(Block).where(Block.content.is_not(None))):
        converted = _convert_snapshot(block.content, zone)
        if converted is not None:
            report.snapshots += 1
            if not dry_run:
                block.content = converted

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.maintenance.convert_dates_to_utc",
        description="Store the times of day of all date values in UTC (once).",
    )
    parser.add_argument(
        "--timezone",
        default=None,
        help="IANA zone the old values were entered in (default: CB_TIMEZONE)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="only count, change nothing",
    )
    args = parser.parse_args(argv)

    zone = timezones.parse_zone(args.timezone) if args.timezone else timezones.installation_zone()
    if zone is None:
        print(f"Unknown time zone: {args.timezone}", file=sys.stderr)
        return 2

    import app.database.database as db_module
    from app.alerts import system_alerts

    with db_module.SessionLocal() as db:
        report = convert(db, zone, dry_run=args.dry_run)
    print(f"Zone {zone}: {report}{' (dry run, nothing changed)' if args.dry_run else ', done'}.")
    if not args.dry_run:
        system_alerts.reconcile_in_own_session()
        print("Calendar alerts brought in line.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
