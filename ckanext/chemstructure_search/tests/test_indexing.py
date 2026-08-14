from unittest import mock

from sqlalchemy.sql.elements import TextClause

import ckanext.chemstructure_search.indexing as indexing
from ckanext.chemstructure_search.plugin import ChemstructureSearchPlugin


def molecule(**values):
    data = {
        "id": "package-1",
        "name": "water",
        "dataset_type": "molecule",
        "state": "active",
        "inchi_key": "XLYOFNOQVPJJNP-UHFFFAOYSA-N",
    }
    data.update(values)
    return data


def test_molecule_with_synonyms_appends_searchable_text():
    search_data = molecule(text="existing searchable content")

    with mock.patch.object(
        indexing,
        "get_molecule_synonyms",
        return_value=["Dihydrogen monoxide", "Water"],
    ):
        result = indexing.add_molecule_synonyms(search_data)

    assert result is search_data
    assert result["text"] == (
        "existing searchable content Dihydrogen monoxide Water"
    )


def test_molecule_without_inchi_key_is_unchanged():
    search_data = molecule(inchi_key=None)
    original = dict(search_data)

    with mock.patch.object(indexing, "get_molecule_synonyms") as get_synonyms:
        result = indexing.add_molecule_synonyms(search_data)

    assert result == original
    get_synonyms.assert_not_called()


def test_molecule_without_synonyms_is_unchanged():
    search_data = molecule(text="existing")
    original = dict(search_data)

    with mock.patch.object(indexing, "get_molecule_synonyms", return_value=[]):
        result = indexing.add_molecule_synonyms(search_data)

    assert result == original


def test_no_matching_rdkit_molecule_is_unchanged():
    search_data = molecule(text="existing")
    original = dict(search_data)

    with mock.patch.object(indexing, "get_molecule_synonyms", return_value=[]):
        result = indexing.add_molecule_synonyms(search_data)

    assert result == original


def test_synonyms_without_existing_text_have_no_extra_whitespace():
    search_data = molecule()

    with mock.patch.object(
        indexing,
        "get_molecule_synonyms",
        return_value=[" Benzaldehyde "],
    ):
        result = indexing.add_molecule_synonyms(search_data)

    assert result["text"] == "Benzaldehyde"


def test_dataset_package_is_unchanged():
    search_data = molecule(dataset_type="dataset", type="dataset")
    original = dict(search_data)

    with mock.patch.object(indexing, "get_molecule_synonyms") as get_synonyms:
        result = indexing.add_molecule_synonyms(search_data)

    assert result == original
    get_synonyms.assert_not_called()


def test_non_active_molecule_is_unchanged():
    search_data = molecule(state="deleted")
    original = dict(search_data)

    with mock.patch.object(indexing, "get_molecule_synonyms") as get_synonyms:
        result = indexing.add_molecule_synonyms(search_data)

    assert result == original
    get_synonyms.assert_not_called()


def test_duplicate_synonyms_differing_only_by_case_are_removed():
    search_data = molecule()

    with mock.patch.object(
        indexing,
        "get_molecule_synonyms",
        return_value=["water", "WATER", " Water ", "Aqua"],
    ):
        result = indexing.add_molecule_synonyms(search_data)

    assert result["text"] == "Aqua water"


def test_database_error_does_not_stop_indexing():
    search_data = molecule(text="existing")
    original = dict(search_data)

    with mock.patch.object(
        indexing,
        "get_molecule_synonyms",
        side_effect=RuntimeError("database unavailable"),
    ), mock.patch.object(indexing.log, "exception") as log_exception:
        result = indexing.add_molecule_synonyms(search_data)

    assert result == original
    log_exception.assert_called_once()


def test_flattened_extras_inchi_key_is_supported():
    search_data = molecule(inchi_key=None, extras_inchi_key="EXTRA-INCHI-KEY")

    with mock.patch.object(
        indexing,
        "get_molecule_synonyms",
        return_value=["Water"],
    ) as get_synonyms:
        indexing.add_molecule_synonyms(search_data)

    get_synonyms.assert_called_once_with("EXTRA-INCHI-KEY")


def test_direct_inchi_key_is_stripped():
    search_data = molecule(inchi_key="  DIRECT-INCHI-KEY  ")

    with mock.patch.object(
        indexing,
        "get_molecule_synonyms",
        return_value=["Water"],
    ) as get_synonyms:
        indexing.add_molecule_synonyms(search_data)

    get_synonyms.assert_called_once_with("DIRECT-INCHI-KEY")


def test_flattened_extras_inchi_key_is_stripped():
    search_data = molecule(
        inchi_key=None,
        extras_inchi_key="  EXTRA-INCHI-KEY  ",
    )

    with mock.patch.object(
        indexing,
        "get_molecule_synonyms",
        return_value=["Water"],
    ) as get_synonyms:
        indexing.add_molecule_synonyms(search_data)

    get_synonyms.assert_called_once_with("EXTRA-INCHI-KEY")


def test_type_field_takes_precedence_over_dataset_type():
    search_data = molecule(type="dataset", dataset_type="molecule")
    original = dict(search_data)

    with mock.patch.object(indexing, "get_molecule_synonyms") as get_synonyms:
        result = indexing.add_molecule_synonyms(search_data)

    assert result == original
    get_synonyms.assert_not_called()


def test_query_uses_inchi_relationship_and_bound_parameter():
    rows = [("Water",), ("Aqua",)]

    with mock.patch.object(indexing.model.Session, "execute", return_value=rows) as execute:
        result = indexing.get_molecule_synonyms("INCHI-KEY")

    query, parameters = execute.call_args[0]
    normalized_sql = " ".join(str(query).split())

    assert result == ["Water", "Aqua"]
    assert isinstance(query, TextClause)
    assert "JOIN rdk.molecule_names AS mn" in normalized_sql
    assert "mn.molecule_id = rm.molecule_id" in normalized_sql
    assert "rm.inchi_key = :inchi_key" in normalized_sql
    assert "mn.name IS NOT NULL" in normalized_sql
    assert "btrim(mn.name) <> ''" in normalized_sql
    assert "SELECT DISTINCT mn.name" in normalized_sql
    assert "ORDER BY mn.name" in normalized_sql
    assert "molecule_rel_data" not in normalized_sql
    assert parameters == {"inchi_key": "INCHI-KEY"}
    assert "INCHI-KEY" not in str(query)


def test_plugin_uses_ckan_29_before_index_hook():
    search_data = molecule()

    with mock.patch(
        "ckanext.chemstructure_search.plugin.add_molecule_synonyms",
        return_value=search_data,
    ) as add_synonyms:
        result = ChemstructureSearchPlugin().before_index(search_data)

    assert result is search_data
    add_synonyms.assert_called_once_with(search_data)


def test_plugin_exposes_requested_before_dataset_index_method():
    search_data = molecule()

    with mock.patch(
        "ckanext.chemstructure_search.plugin.add_molecule_synonyms",
        return_value=search_data,
    ) as add_synonyms:
        result = ChemstructureSearchPlugin().before_dataset_index(search_data)

    assert result is search_data
    add_synonyms.assert_called_once_with(search_data)
