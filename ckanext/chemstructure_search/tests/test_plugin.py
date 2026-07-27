import pytest
from flask import Flask

import ckan.plugins.toolkit as toolkit

import ckanext.chemstructure_search.action as action
import ckanext.chemstructure_search.plugin as plugin


class FakeResult(object):
    def __init__(self, scalar_value=None, one=None, all_rows=None):
        self.scalar_value = scalar_value
        self.one = one
        self.all_rows = all_rows or []

    def scalar(self):
        return self.scalar_value

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all_rows


class CapturingSession(object):
    def __init__(self, rows=None, fail_search=False):
        self.rows = rows or []
        self.fail_search = fail_search
        self.calls = []

    def execute(self, sql, params=None):
        sql_text = str(sql)
        self.calls.append((sql_text, params or {}))

        if "set_config('rdkit.tanimoto_threshold'" in sql_text:
            return FakeResult(one={"set_config": params["threshold"]})

        if "query_canonical_smiles" in sql_text:
            return FakeResult(one={"query_canonical_smiles": "CCO"})

        if "TRUE AS valid" in sql_text:
            return FakeResult(one={"valid": True})

        if self.fail_search:
            raise RuntimeError("database exploded")

        return FakeResult(all_rows=self.rows)


def metadata(
    mapping="inchi_key",
    operators=None,
    package_extra_has_state=True,
):
    if operators is None:
        operators = {
            "threshold": True,
            "knn": True,
        }

    return {
        "mapping": mapping,
        "functions": {
            "mol_from_smiles": '"public"."mol_from_smiles"',
            "mol_to_smiles": '"public"."mol_to_smiles"',
            "morganbv_fp": '"public"."morganbv_fp"',
            "tanimoto_sml": '"public"."tanimoto_sml"',
        },
        "smarts_function": '"public"."qmol_from_smarts"',
        "package_extra_has_state": package_extra_has_state,
        "similarity_operators": operators,
    }


def run_with_captured_sql(
    monkeypatch,
    mode,
    rows=25,
    result_rows=None,
    meta=None,
):
    session = CapturingSession(rows=result_rows or [{
        "id": "pkg-1",
        "name": "ethanol",
        "title": "Ethanol",
        "canonical_smiles": "CCO",
        "similarity": 0.91,
    }])

    monkeypatch.setattr(action.model, "Session", session)
    monkeypatch.setattr(
        action,
        "_inspect_rdkit_schema",
        lambda requested_mode: meta or metadata(),
    )

    result = action.run_structure_search(
        query="CCO" if mode != "smarts" else "[#6]",
        mode=mode,
        threshold=0.7,
        rows=rows,
    )

    search_sql, params = session.calls[-1]
    return result, search_sql, params, session.calls


def assert_common_package_mapping_sql(sql):
    assert "FROM rdk.molecules m" in sql
    assert "JOIN package_extra pe" in sql
    assert "pe.key = 'inchi_key'" in sql
    assert "pe.state = 'active'" in sql
    assert 'JOIN "package" p' in sql
    assert "p.type = 'molecule'" in sql
    assert "p.state = 'active'" in sql
    assert "DISTINCT ON (p.id)" in sql
    assert "FROM rdkit" not in sql
    assert "FROM rdk.mols" not in sql


def test_exact_matching_uses_cartridge_operator(monkeypatch):
    result, sql, params, _calls = run_with_captured_sql(monkeypatch, "exact")

    assert "m.molecule @= q.molecule" in sql
    assert_common_package_mapping_sql(sql)
    assert result["source"] == "postgresql_cartridge"
    assert result["solr_used"] is False
    assert result["query_canonical_smiles"] == "CCO"
    assert result["results"][0]["name"] == "ethanol"
    assert result["results"][0]["mode"] == "exact"
    assert "similarity" not in result["results"][0]
    assert params["query"] == "CCO"
    assert params["rows"] == 25


def test_smiles_substructure_matching_uses_contains_operator(monkeypatch):
    _result, sql, _params, _calls = run_with_captured_sql(
        monkeypatch,
        "substructure",
    )

    assert "m.molecule @> q.molecule" in sql
    assert_common_package_mapping_sql(sql)


def test_smarts_matching_uses_confirmed_smarts_function(monkeypatch):
    result, sql, params, _calls = run_with_captured_sql(monkeypatch, "smarts")

    assert '"public"."qmol_from_smarts"(:query)' in sql
    assert "m.molecule @> q.pattern" in sql
    assert result["query_canonical_smiles"] is None
    assert params["query"] == "[#6]"


def test_similarity_threshold_filtering_ordering_and_fingerprint_join(
    monkeypatch,
):
    result, sql, params, calls = run_with_captured_sql(
        monkeypatch,
        "similarity",
    )

    assert "JOIN rdk.fingerprints f" in sql
    assert "ON f.molecule_id = m.molecule_id" in sql
    assert '"public"."morganbv_fp"(molecule)' in sql
    assert (
        '"public"."tanimoto_sml"(q.query_fingerprint, f.mfp2) '
        ">= :threshold"
    ) in sql
    assert "f.mfp2 % q.query_fingerprint" in sql
    assert "ORDER BY f.mfp2 <%> q.query_fingerprint" in sql
    assert "ORDER BY similarity DESC NULLS LAST, name" in sql
    assert params["threshold"] == 0.7
    assert result["threshold"] == 0.7
    assert result["results"][0]["similarity"] == 0.91
    assert any(
        "set_config('rdkit.tanimoto_threshold'" in call[0]
        for call in calls
    )


def test_similarity_omits_gist_operators_when_not_supported(monkeypatch):
    _result, sql, _params, _calls = run_with_captured_sql(
        monkeypatch,
        "similarity",
        meta=metadata(operators={"threshold": False, "knn": False}),
    )

    assert "f.mfp2 % q.query_fingerprint" not in sql
    assert "f.mfp2 <%> q.query_fingerprint" not in sql
    assert (
        '"public"."tanimoto_sml"(q.query_fingerprint, f.mfp2) '
        ">= :threshold"
    ) in sql


def test_direct_package_mapping_when_molecule_id_matches_package_id(
    monkeypatch,
):
    _result, sql, _params, _calls = run_with_captured_sql(
        monkeypatch,
        "exact",
        meta=metadata(mapping="package_id"),
    )

    assert 'JOIN "package" p' in sql
    assert "p.id::text = h.molecule_id::text" in sql
    assert "JOIN package_extra pe" not in sql


def test_rows_none_omits_limit_clause(monkeypatch):
    _result, sql, params, _calls = run_with_captured_sql(
        monkeypatch,
        "exact",
        rows=None,
    )

    assert "LIMIT" not in sql
    assert "rows" not in params


def test_numeric_result_limits_are_bound(monkeypatch):
    _result, sql, params, _calls = run_with_captured_sql(
        monkeypatch,
        "exact",
        rows="7",
    )

    assert "LIMIT :rows" in sql
    assert params["rows"] == 7


def test_invalid_smiles_raises_validation_error(monkeypatch):
    session = CapturingSession()

    def invalid_execute(sql, params=None):
        sql_text = str(sql)
        session.calls.append((sql_text, params or {}))
        if "query_canonical_smiles" in sql_text:
            return FakeResult(one=None)
        return FakeResult(all_rows=[])

    session.execute = invalid_execute
    monkeypatch.setattr(action.model, "Session", session)
    monkeypatch.setattr(
        action,
        "_inspect_rdkit_schema",
        lambda mode: metadata(),
    )

    with pytest.raises(toolkit.ValidationError):
        action.run_structure_search("not-smiles", mode="exact", rows=10)


def test_invalid_smarts_raises_validation_error(monkeypatch):
    session = CapturingSession()

    def invalid_execute(sql, params=None):
        sql_text = str(sql)
        session.calls.append((sql_text, params or {}))
        if "TRUE AS valid" in sql_text:
            return FakeResult(one=None)
        return FakeResult(all_rows=[])

    session.execute = invalid_execute
    monkeypatch.setattr(action.model, "Session", session)
    monkeypatch.setattr(
        action,
        "_inspect_rdkit_schema",
        lambda mode: metadata(),
    )

    with pytest.raises(toolkit.ValidationError):
        action.run_structure_search("[", mode="smarts", rows=10)


def test_unsupported_mode_raises_validation_error():
    with pytest.raises(toolkit.ValidationError):
        action.run_structure_search("CCO", mode="contains")


@pytest.mark.parametrize("threshold", ["bad", -0.1, 1.1])
def test_invalid_thresholds_raise_validation_error(threshold):
    with pytest.raises(toolkit.ValidationError):
        action.run_structure_search(
            "CCO",
            mode="similarity",
            threshold=threshold,
        )


@pytest.mark.parametrize("rows", ["bad", -1])
def test_invalid_rows_raise_validation_error(rows):
    with pytest.raises(toolkit.ValidationError):
        action.run_structure_search("CCO", mode="exact", rows=rows)


def test_missing_rdkit_extension_raises_validation_error(monkeypatch):
    monkeypatch.setattr(action, "_scalar", lambda sql, params=None: False)

    with pytest.raises(toolkit.ValidationError):
        action._inspect_rdkit_schema("exact")


def test_missing_rdk_molecules_raises_validation_error(monkeypatch):
    monkeypatch.setattr(action, "_scalar", lambda sql, params=None: True)
    monkeypatch.setattr(action, "_fetch_table_names", lambda: {
        "molecules": None,
        "fingerprints": "rdk.fingerprints",
        "package_table": "package",
        "package_extra_table": "package_extra",
    })

    with pytest.raises(toolkit.ValidationError):
        action._inspect_rdkit_schema("exact")


def test_missing_rdk_fingerprints_raises_for_similarity(monkeypatch):
    monkeypatch.setattr(action, "_scalar", lambda sql, params=None: True)
    monkeypatch.setattr(action, "_fetch_table_names", lambda: {
        "molecules": "rdk.molecules",
        "fingerprints": None,
        "package_table": "package",
        "package_extra_table": "package_extra",
    })

    with pytest.raises(toolkit.ValidationError):
        action._inspect_rdkit_schema("similarity")


def test_mapping_falls_back_to_inchi_key_for_non_string_molecule_id():
    columns = {
        ("rdk", "molecules"): {
            "molecule_id": "integer",
            "molecule": "USER-DEFINED",
            "canonical_smiles": "text",
            "inchi_key": "text",
        },
        ("public", "package"): {
            "id": "text",
            "name": "text",
            "title": "text",
            "type": "text",
            "state": "text",
        },
        ("public", "package_extra"): {
            "package_id": "text",
            "key": "text",
            "value": "text",
            "state": "text",
        },
    }

    assert action._select_package_mapping(columns) == "inchi_key"


def test_mapping_raises_when_no_confirmed_join_exists():
    columns = {
        ("rdk", "molecules"): {
            "molecule_id": "integer",
            "molecule": "USER-DEFINED",
            "canonical_smiles": "text",
        },
        ("public", "package"): {
            "id": "text",
            "name": "text",
            "title": "text",
            "type": "text",
            "state": "text",
        },
        ("public", "package_extra"): {
            "package_id": "text",
            "key": "text",
            "value": "text",
        },
    }

    with pytest.raises(toolkit.ValidationError):
        action._select_package_mapping(columns)


def test_sql_failure_has_no_python_fallback(monkeypatch):
    session = CapturingSession(fail_search=True)

    monkeypatch.setattr(action.model, "Session", session)
    monkeypatch.setattr(
        action,
        "_inspect_rdkit_schema",
        lambda mode: metadata(),
    )

    assert not hasattr(action, "_run_structure_search_python")

    with pytest.raises(toolkit.ValidationError):
        action.run_structure_search("CCO", mode="exact", rows=10)


def test_before_search_generates_existing_solr_package_name_filter(
    monkeypatch,
):
    app = Flask(__name__)
    search_plugin = plugin.ChemstructureSearchPlugin()

    monkeypatch.setattr(plugin, "run_structure_search", lambda **kwargs: {
        "results": [
            {"name": "ethanol"},
            {"name": "benzene"},
        ],
    })

    with app.test_request_context(
        "/molecule?structure_query=CCO&structure_mode=exact&threshold=0.7"
    ):
        params = search_plugin.before_search({
            "fq": 'owner_org:"org-1" structure_query:CCO threshold:0.7',
            "start": 0,
            "rows": 20,
            "sort": "title_string asc",
            "extras": {},
        })

    assert "{!terms f=name}ethanol,benzene" in params["fq"]
    assert "owner_org" in params["fq"]
    assert "structure_query:" not in params["fq"]
    assert "threshold:" not in params["fq"]
    assert params["sort"] == plugin.CHEMICAL_RELEVANCE_SORT
    assert params["start"] == 0
    assert params["rows"] == 2


def test_after_search_restores_similarity_order_and_values(monkeypatch):
    app = Flask(__name__)
    search_plugin = plugin.ChemstructureSearchPlugin()

    monkeypatch.setattr(plugin, "run_structure_search", lambda **kwargs: {
        "results": [
            {"name": "ethylbenzene", "similarity": 1.0},
            {"name": "propylbenzene", "similarity": 0.61},
            {"name": "butylbenzene", "similarity": 0.52},
        ],
    })

    with app.test_request_context(
        "/molecule?structure_query=CCc1ccccc1"
        "&structure_mode=similarity&threshold=0.05"
        "&sort=score+desc%2C+metadata_modified+desc"
    ):
        params = search_plugin.before_search({
            "fq": "",
            "start": 0,
            "rows": 20,
            "sort": plugin.CHEMICAL_RELEVANCE_SORT,
            "extras": {},
        })
        search_results = search_plugin.after_search({
            "count": 3,
            "results": [
                {"name": "butylbenzene"},
                {"name": "propylbenzene"},
                {"name": "ethylbenzene"},
            ],
        }, params)

    assert [
        item["name"]
        for item in search_results["results"]
    ] == [
        "ethylbenzene",
        "propylbenzene",
        "butylbenzene",
    ]
    assert search_results["results"][0]["structure_similarity"] == 1.0
    assert search_results["results"][0]["structure_rank"] == 1


def test_chemical_ranking_is_applied_before_pagination(monkeypatch):
    app = Flask(__name__)
    search_plugin = plugin.ChemstructureSearchPlugin()

    monkeypatch.setattr(plugin, "run_structure_search", lambda **kwargs: {
        "results": [
            {"name": "first", "similarity": 1.0},
            {"name": "second", "similarity": 0.9},
            {"name": "third", "similarity": 0.8},
            {"name": "fourth", "similarity": 0.7},
        ],
    })

    with app.test_request_context(
        "/molecule?structure_query=CCO&structure_mode=similarity"
    ):
        params = search_plugin.before_search({
            "fq": "",
            "start": 2,
            "rows": 2,
            "extras": {},
        })

        assert params["start"] == 0
        assert params["rows"] == 4

        search_results = search_plugin.after_search({
            "count": 4,
            "results": [
                {"name": "fourth"},
                {"name": "second"},
                {"name": "first"},
                {"name": "third"},
            ],
        }, params)

    assert search_results["count"] == 4
    assert [
        item["name"]
        for item in search_results["results"]
    ] == ["third", "fourth"]


def test_explicit_name_sort_keeps_solr_order_and_pagination(monkeypatch):
    app = Flask(__name__)
    search_plugin = plugin.ChemstructureSearchPlugin()

    monkeypatch.setattr(plugin, "run_structure_search", lambda **kwargs: {
        "results": [
            {"name": "ethylbenzene", "similarity": 1.0},
            {"name": "butylbenzene", "similarity": 0.52},
        ],
    })

    with app.test_request_context(
        "/molecule?structure_query=CCc1ccccc1"
        "&structure_mode=similarity&sort=title_string+asc"
    ):
        params = search_plugin.before_search({
            "fq": "",
            "start": 20,
            "rows": 20,
            "sort": "title_string asc",
            "extras": {},
        })
        search_results = search_plugin.after_search({
            "count": 2,
            "results": [
                {"name": "butylbenzene"},
                {"name": "ethylbenzene"},
            ],
        }, params)

    assert params["start"] == 20
    assert params["rows"] == 20
    assert plugin.STRUCTURE_RANK_EXTRAS_KEY not in params["extras"]
    assert [
        item["name"]
        for item in search_results["results"]
    ] == ["butylbenzene", "ethylbenzene"]
