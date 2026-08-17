from contextlib import contextmanager
from unittest import mock

import pytest

from ckanext.chemstructure_search import sync
from ckanext.chemstructure_search.plugin import ChemstructureSearchPlugin


class Result(object):
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeSession(object):
    def __init__(self, existing=False, canonical="CCO"):
        self.existing = existing
        self.canonical = canonical
        self.calls = []

    def execute(self, query, parameters=None):
        sql = " ".join(str(query).split())
        self.calls.append((sql, parameters or {}))
        if "SELECT mol_to_smiles" in sql:
            return Result((self.canonical,))
        if "SELECT molecule_id FROM rdk.molecules" in sql:
            return Result((7,) if self.existing else None)
        if "RETURNING molecule_id" in sql:
            return Result((7,))
        return Result()

    @contextmanager
    def begin_nested(self):
        yield


def molecule(**changes):
    value = {
        "id": "molecule-one",
        "name": "molecule-one",
        "type": "molecule",
        "state": "active",
        "smiles": "C(C)O",
        "inchi": "InChI=1S/C2H6O",
        "inchi_key": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
        "mol_formula": "C2H6O",
        "exactmass": "46.0419",
        "alternate_name": ["ethanol", "Ethyl alcohol"],
    }
    value.update(changes)
    return value


@pytest.mark.parametrize("raw, expected", [
    (["one", "two"], ["one", "two"]),
    ('["one", "two"]', ["one", "two"]),
    ("one", ["one"]),
    (None, []),
    ("", []),
    (["one", " ONE ", None, ""], ["one"]),
])
def test_parse_alternate_names(raw, expected):
    assert sync.parse_alternate_names(raw) == expected


def test_sync_creates_molecule_fingerprint_and_unique_names():
    session = FakeSession()
    result = sync.sync_molecule_package({}, molecule(), session=session)
    sql = " ".join(call[0] for call in session.calls)

    assert result == {"status": "created", "molecule_id": 7, "names": 2}
    assert "ON CONFLICT (inchi_code) DO UPDATE" in sql
    assert "morganbv_fp(molecule)" in sql
    assert "featmorganbv_fp(molecule)" in sql
    assert sql.count("ON CONFLICT (molecule_id, name) DO NOTHING") == 2
    assert "public.molecules" not in sql
    assert "molecule_rel_data" not in sql


def test_repeated_sync_updates_without_duplicate_names():
    session = FakeSession(existing=True)
    result = sync.sync_molecule_package(
        {}, molecule(alternate_name='["ethanol", "ethanol"]'),
        session=session,
    )
    name_inserts = [sql for sql, unused in session.calls
                    if "INSERT INTO rdk.molecule_names" in sql]
    assert result["status"] == "updated"
    assert len(name_inserts) == 1


def test_values_can_come_from_extras():
    package = molecule(smiles=None, inchi=None, alternate_name=None)
    package["extras"] = [
        {"key": "smiles", "value": "CCO"},
        {"key": "inchi", "value": "InChI=1S/C2H6O"},
        {"key": "alternate_name", "value": "alcohol"},
    ]
    result = sync.sync_molecule_package({}, package, session=FakeSession())
    assert result["names"] == 1


def test_dataset_and_inactive_packages_are_ignored():
    session = FakeSession()
    assert sync.sync_molecule_package(
        {}, molecule(type="dataset"), session=session
    )["status"] == "skipped"
    assert sync.sync_molecule_package(
        {}, molecule(state="deleted"), session=session
    )["status"] == "skipped"
    assert session.calls == []


def test_invalid_smiles_is_logged_without_query_or_exception(caplog):
    package = molecule(smiles="sensitive-invalid-value")
    result = sync.sync_molecule_package_safely(
        {}, package, session=FakeSession(canonical=None)
    )
    assert result == {"status": "failed", "reason": "invalid SMILES"}
    assert "molecule-one" in caplog.text
    assert "invalid SMILES" in caplog.text
    assert "sensitive-invalid-value" not in caplog.text


def test_existing_pubchem_names_are_never_deleted_or_updated():
    session = FakeSession()
    sync.sync_molecule_package({}, molecule(), session=session)
    name_sql = " ".join(sql for sql, unused in session.calls
                        if "rdk.molecule_names" in sql)
    assert "DELETE" not in name_sql
    assert "DO NOTHING" in name_sql
    assert all(params.get("source") == "CKAN"
               for sql, params in session.calls
               if "INSERT INTO rdk.molecule_names" in sql)


def test_package_lifecycle_hooks_use_safe_central_sync():
    plugin = ChemstructureSearchPlugin()
    with mock.patch(
        "ckanext.chemstructure_search.plugin.sync_molecule_package_safely"
    ) as central_sync:
        plugin.after_create({"user": "tester"}, molecule())
        plugin.after_update({"user": "tester"}, molecule())
    assert central_sync.call_count == 2
