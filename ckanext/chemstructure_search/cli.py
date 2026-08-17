import json

import click
from sqlalchemy import text

import ckan.model as model
import ckan.plugins.toolkit as toolkit

from ckanext.chemstructure_search.sync import sync_molecule_package_safely


PACKAGE_BATCH_SQL = text("""
    SELECT name
    FROM package
    WHERE type = 'molecule'
      AND state = 'active'
      AND (:start_after IS NULL OR name > :start_after)
    ORDER BY name, id
    LIMIT :batch_size
""")

VERIFY_SQL = {
    "packages_missing_molecules": text("""
        SELECT count(*)
        FROM package p
        LEFT JOIN package_extra pe
          ON pe.package_id = p.id AND pe.key = 'inchi_key'
        LEFT JOIN rdk.molecules m
          ON upper(m.inchi_key) = upper(pe.value)
        WHERE p.type = 'molecule' AND p.state = 'active'
          AND m.molecule_id IS NULL
    """),
    "molecules_missing_fingerprints": text("""
        SELECT count(*)
        FROM rdk.molecules m
        LEFT JOIN rdk.fingerprints f ON f.molecule_id = m.molecule_id
        WHERE f.molecule_id IS NULL
    """),
    "null_fingerprints": text("""
        SELECT count(*) FROM rdk.fingerprints
        WHERE mfp2 IS NULL OR ffp2 IS NULL
    """),
    "duplicate_inchi_codes": text("""
        SELECT count(*) FROM (
            SELECT inchi_code FROM rdk.molecules
            GROUP BY inchi_code HAVING count(*) > 1
        ) duplicates
    """),
    "package_rdkit_inchi_key_mismatches": text("""
        SELECT count(*)
        FROM package p
        JOIN package_extra pi
          ON pi.package_id = p.id AND pi.key = 'inchi'
        JOIN package_extra pk
          ON pk.package_id = p.id AND pk.key = 'inchi_key'
        JOIN rdk.molecules m ON m.inchi_code = pi.value
        WHERE p.type = 'molecule' AND p.state = 'active'
          AND upper(coalesce(m.inchi_key, '')) <> upper(pk.value)
    """),
    "packages_with_names_missing_ckan_names": text("""
        SELECT count(DISTINCT p.id)
        FROM package p
        JOIN package_extra pa
          ON pa.package_id = p.id AND pa.key = 'alternate_name'
        JOIN package_extra pi
          ON pi.package_id = p.id AND pi.key = 'inchi'
        JOIN rdk.molecules m ON m.inchi_code = pi.value
        LEFT JOIN rdk.molecule_names mn
          ON mn.molecule_id = m.molecule_id AND mn.source = 'CKAN'
        WHERE p.type = 'molecule' AND p.state = 'active'
          AND btrim(pa.value) NOT IN ('', '[]')
          AND mn.molecule_id IS NULL
    """),
}


def _write_failure(handle, package_name, reason):
    handle.write(json.dumps({
        "package": package_name,
        "reason": reason,
    }, sort_keys=True) + "\n")
    handle.flush()


@click.group(name="chemstructure")
def chemstructure():
    """Synchronize and verify CKAN molecule data in PostgreSQL RDKit."""


@chemstructure.command(name="sync")
@click.option("--dry-run", is_flag=True, help="Roll back every batch.")
@click.option("--limit", type=click.IntRange(min=1), default=None)
@click.option("--batch-size", type=click.IntRange(min=1), default=100,
              show_default=True)
@click.option("--start-after", default=None, metavar="PACKAGE_NAME")
@click.option("--failure-log", type=click.Path(dir_okay=False),
              default="chemstructure-sync-failures.jsonl", show_default=True)
def sync_command(dry_run, limit, batch_size, start_after, failure_log):
    """Synchronize all active molecule packages in deterministic batches."""
    counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    processed = 0
    cursor = start_after

    with open(failure_log, "a") as failure_handle:
        while limit is None or processed < limit:
            requested = batch_size
            if limit is not None:
                requested = min(requested, limit - processed)
            rows = model.Session.execute(PACKAGE_BATCH_SQL, {
                "start_after": cursor,
                "batch_size": requested,
            }).fetchall()
            if not rows:
                model.Session.rollback()
                break

            for row in rows:
                package_name = row[0]
                package_dict = toolkit.get_action("package_show")(
                    {"ignore_auth": True}, {"id": package_name}
                )
                result = sync_molecule_package_safely(
                    {"ignore_auth": True}, package_dict
                )
                status = result["status"]
                counts[status] += 1
                if status == "failed":
                    _write_failure(
                        failure_handle, package_name, result["reason"]
                    )
                processed += 1
                cursor = package_name

            if dry_run:
                model.Session.rollback()
            else:
                model.Session.commit()

            click.echo(
                "batch complete through {0} ({1} processed)".format(
                    cursor, processed
                )
            )

    click.echo("mode={0} created={created} updated={updated} "
               "skipped={skipped} failed={failed}".format(
                   "dry-run" if dry_run else "write", **counts
               ))


@chemstructure.command(name="verify")
def verify_command():
    """Print consistency issue counts without changing the database."""
    try:
        for label, query in VERIFY_SQL.items():
            count = model.Session.execute(query).scalar()
            click.echo("{0}={1}".format(label, count))
    finally:
        model.Session.rollback()


def get_commands():
    return [chemstructure]
