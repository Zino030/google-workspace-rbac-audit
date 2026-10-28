"""
workspace_audit — read-only Google Workspace permissions audit tool.

READ-ONLY GUARANTEE
-------------------
This tool performs exclusively GET requests to Google Workspace APIs.
No permissions, memberships, delegations, or any other state is modified.
The auth.py module enforces this at runtime via an HTTP method guard.

Usage:
  python -m workspace_audit --admin admin@example.com
  python -m workspace_audit --admin admin@example.com --key ./service-account.json
  python -m workspace_audit --admin admin@example.com --only mailboxes,groups
  python -m workspace_audit --admin admin@example.com --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from .auth import validate_key_file
from .core.matrix import build_matrix, Grant
from .core.normalize import NONE, EDITOR, READER
from .discover import discover_all

logger = logging.getLogger(__name__)

VALID_MODULES = {"mailboxes", "groups", "drives"}


def _setup_logging(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    log_file = out_dir / f"audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    fmt = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
    logging.basicConfig(
        level=logging.DEBUG,
        format=fmt,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Suppress noisy google-api libs at DEBUG level on console
    for noisy in ("googleapiclient", "google.auth", "urllib3", "httplib2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger.info("Log file: %s", log_file)


def _normalise_alias(email: str, alias_cache: dict[str, str], primary_emails: set[str]) -> str:
    """Return cached primary email, or the email itself if not in cache."""
    return alias_cache.get(email, email)


def _build_alias_cache(users: list) -> tuple[dict[str, str], set[str]]:
    """Map every known primary email to itself (basis for alias resolution)."""
    primary_set = {u.primary_email for u in users}
    cache: dict[str, str] = {e: e for e in primary_set}
    return cache, primary_set


def _reclassify_grantees(
    grants: list[Grant],
    group_email_set: set[str],
    internal_domains: set[str],
) -> list[Grant]:
    """Fix grantee_type using the full known group set."""
    result = []
    for g in grants:
        ge = g.grantee
        if ge in group_email_set:
            grantee_type = "Gruppe"
        elif ge.startswith("domain:"):
            domain_name = ge[7:]
            grantee_type = "Person" if domain_name in internal_domains else "extern"
        else:
            domain = ge.split("@")[-1] if "@" in ge else ""
            grantee_type = "Person" if domain in internal_domains else "extern"
        result.append(Grant(
            resource_type=g.resource_type,
            resource=g.resource,
            grantee=ge,
            grantee_type=grantee_type,
            native_role=g.native_role,
            matrix_role=g.matrix_role,
        ))
    return result


def _normalise_grants(grants: list[Grant], alias_cache: dict[str, str]) -> list[Grant]:
    """Normalise all grantee emails to their primary address."""
    result = []
    for g in grants:
        primary = alias_cache.get(g.grantee, g.grantee).lower()
        result.append(Grant(
            resource_type=g.resource_type,
            resource=g.resource,
            grantee=primary,
            grantee_type=g.grantee_type,
            native_role=g.native_role,
            matrix_role=g.matrix_role,
        ))
    return result


def _print_summary(matrix, all_errors: list[str], elapsed: float) -> None:
    from collections import Counter

    rows_by_type: Counter = Counter(r.resource_type for r in matrix.rows)
    grantees_by_type: Counter = Counter(matrix.grantee_meta.values())
    set_cells = sum(
        1
        for row in matrix.rows
        for role in row.permissions.values()
        if role != NONE
    )

    print("\n" + "=" * 60)
    print("  WORKSPACE AUDIT — ZUSAMMENFASSUNG")
    print("=" * 60)
    print(f"  Laufzeit:          {elapsed:.1f}s")
    print(f"  Ressourcen:")
    for rt, count in rows_by_type.items():
        print(f"    {rt:<30} {count}")
    print(f"  Eindeutige Grantees (Spalten): {len(matrix.grantee_order)}")
    print(f"    Gruppen:     {grantees_by_type.get('Gruppe', 0)}")
    print(f"    Personen:    {grantees_by_type.get('Person', 0)}")
    print(f"    Extern:      {grantees_by_type.get('extern', 0)}")
    print(f"  Gesetzte Berechtigungszellen:  {set_cells}")
    if all_errors:
        print(f"\n  FEHLER ({len(all_errors)} Ressource(n) übersprungen):")
        for e in all_errors:
            print(f"    ⚠  {e}")
    print("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only Google Workspace permissions audit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--admin", required=True, help="Admin e-mail to impersonate")
    parser.add_argument(
        "--key",
        default="./service-account.json",
        help="Path to service account key JSON (default: ./service-account.json)",
    )
    parser.add_argument(
        "--out",
        default="./output",
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--only",
        default="mailboxes,groups,drives",
        help="Comma-separated modules to run: mailboxes,groups,drives",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned API calls without executing them",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    _setup_logging(out_dir)

    key_file = Path(args.key)
    admin_email = args.admin.lower().strip()
    modules = {m.strip() for m in args.only.split(",")} & VALID_MODULES

    if not modules:
        logger.error("No valid modules selected. Choose from: %s", ", ".join(VALID_MODULES))
        sys.exit(1)

    logger.info("Starting workspace_audit | admin=%s | modules=%s | dry_run=%s",
                admin_email, modules, args.dry_run)

    if not args.dry_run:
        try:
            validate_key_file(key_file)
        except Exception as exc:
            logger.error("Key file error: %s", exc)
            sys.exit(1)

    t0 = time.monotonic()
    all_grants: list[Grant] = []
    all_errors: list[str] = []

    # --- Discover ---
    if args.dry_run:
        logger.info("[dry-run] Skipping API discovery")
        from .discover import OrgResources
        org = OrgResources()
    else:
        org = discover_all(key_file, admin_email)

    internal_domains = set(org.domains)
    alias_cache, _ = _build_alias_cache(org.users)
    group_email_set = {g.email for g in org.groups}

    # --- Extract ---
    if "mailboxes" in modules:
        from .extractors import mailboxes
        grants, errors = mailboxes.extract(
            key_file, org.users, internal_domains, dry_run=args.dry_run
        )
        all_grants.extend(grants)
        all_errors.extend(errors)

    if "groups" in modules:
        from .extractors import groups
        grants, errors = groups.extract(
            key_file, admin_email, org.groups, internal_domains, dry_run=args.dry_run
        )
        all_grants.extend(grants)
        all_errors.extend(errors)

    if "drives" in modules:
        from .extractors import shared_drives
        grants, errors = shared_drives.extract(
            key_file, admin_email, org.drives, internal_domains,
            group_email_set, dry_run=args.dry_run
        )
        all_grants.extend(grants)
        all_errors.extend(errors)

    if args.dry_run:
        logger.info("[dry-run] Finished. No API calls were made.")
        return

    if not all_grants:
        logger.warning("No grants found — nothing to export.")
        return

    # --- Normalise & reclassify ---
    all_grants = _normalise_grants(all_grants, alias_cache)
    all_grants = _reclassify_grantees(all_grants, group_email_set, internal_domains)

    # --- Build matrix ---
    matrix = build_matrix(all_grants)

    # Final dedup invariant
    assert len(set(matrix.grantee_order)) == len(matrix.grantee_order), \
        "BUG: duplicate grantees in final matrix columns"

    # --- Export ---
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    xlsx_path = out_dir / f"rollenrechte_matrix_{timestamp}.xlsx"
    csv_path = out_dir / f"rollenrechte_matrix_{timestamp}.csv"

    from .export import xlsx_writer, csv_writer
    xlsx_writer.write(matrix, xlsx_path)
    csv_writer.write(matrix, csv_path)

    # Also write without timestamp for easy access
    xlsx_writer.write(matrix, out_dir / "rollenrechte_matrix.xlsx")
    csv_writer.write(matrix, out_dir / "rollenrechte_matrix.csv")

    elapsed = time.monotonic() - t0
    _print_summary(matrix, all_errors, elapsed)


if __name__ == "__main__":
    main()
