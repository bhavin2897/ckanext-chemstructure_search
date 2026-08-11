import logging

from flask import request

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit

from ckanext.chemstructure_search.action import (
    chemstructure_exact_search,
    chemstructure_rdkit_search,
    chemstructure_render_query_image,
    run_structure_search,
)
from ckanext.chemstructure_search.helpers import chemstructure_search_params
from ckanext.chemstructure_search.indexing import add_molecule_names

from ckanext.chemstructure_search.views import get_blueprints


log = logging.getLogger(__name__)

CHEMICAL_RELEVANCE_SORT = "score desc, metadata_modified desc"
STRUCTURE_RANK_EXTRAS_KEY = "chemstructure_search_rank"


class ChemstructureSearchPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IPackageController, inherit=True)
    plugins.implements(plugins.ITemplateHelpers,inherit=True)

    def update_config(self, config):
        toolkit.add_template_directory(config, "templates")
        toolkit.add_public_directory(config, "public")

    def get_blueprint(self):
        return get_blueprints()

    def get_actions(self):
        return {
            "chemstructure_exact_search": chemstructure_exact_search,
            "chemstructure_rdkit_search": chemstructure_rdkit_search,
            "chemstructure_render_query_image" : chemstructure_render_query_image,
        }

    def get_helpers(self):
        return {
            "chemstructure_search_params" : chemstructure_search_params
        }

    def before_index(self, pkg_dict):
        return add_molecule_names(pkg_dict)

    def before_search(self, search_params):
        """
        Apply structure-search filtering to the normal /molecule page.

        URL example:
            /molecule?structure_query=c1ccccc1&structure_mode=similarity&threshold=0.25
        """

        try:
            structure_query = request.args.get("structure_query")
        except RuntimeError:
            return search_params

        if not structure_query:
            return search_params

        request_path = request.path or ""

        # Only apply this to the molecule listing page.
        if not request_path.rstrip("/").endswith("/molecule"):
            return search_params

        # Only apply structure filtering to the main molecule listing search.
        # Avoid affecting internal package_search calls used for organization counts,
        # helper functions, snippets, facets from unrelated contexts, etc.
        if search_params.get("include_dataset_count"):
            return search_params

        if search_params.get("include_users"):
            return search_params

        if search_params.get("id") or search_params.get("name"):
            return search_params

        self._remove_structure_params_from_fq(search_params)

        structure_mode = request.args.get("structure_mode", "similarity")
        threshold = request.args.get("threshold", "0.25")

        log.warning(
            "CHEMSTRUCTURE before_search structure_query=%s mode=%s threshold=%s path=%s",
            structure_query,
            structure_mode,
            threshold,
            request_path,
        )

        try:
            structure_result = run_structure_search(
                query=structure_query,
                mode=structure_mode,
                threshold=float(threshold),
                rows=None,
            )
        except Exception:
            log.exception("CHEMSTRUCTURE structure search failed during /molecule filtering")
            self._append_fq(search_params, 'name:"__chemstructure_error_no_results__"')
            return search_params

        names = [
            item.get("name")
            for item in structure_result.get("results", [])
            if item.get("name")
        ]

        log.warning(
            "CHEMSTRUCTURE before_search matched_names=%s",
            len(names)
        )

        if not names:
            self._append_fq(search_params, 'name:"__chemstructure_no_results__"')
            return search_params

        fq = self._build_name_filter(names)
        self._append_fq(search_params, fq)

        if self._uses_chemical_relevance_sort(search_params):
            self._prepare_chemical_ranking(
                search_params,
                structure_result.get("results", []),
            )

        return search_params

    def after_search(self, search_results, search_params):
        """
        Restore the PostgreSQL RDKit order after Solr has loaded the packages.

        The structure-name filter sent to Solr is deliberately unscored, so
        Solr cannot preserve the Tanimoto ordering returned by RDKit. For
        chemical relevance searches, before_search asks Solr for the complete
        authorized result set and stores the requested page here. This lets us
        rank first and paginate second, so a top match cannot land on a later
        Solr page.
        """

        extras = search_params.get("extras") or {}
        ranking = extras.get(STRUCTURE_RANK_EXTRAS_KEY)

        if not ranking:
            return search_results

        rank_by_name = ranking["rank_by_name"]
        similarity_by_name = ranking["similarity_by_name"]
        unranked_position = len(rank_by_name)
        results = list(search_results.get("results") or [])

        results.sort(
            key=lambda item: rank_by_name.get(
                item.get("name"),
                unranked_position,
            )
        )

        for item in results:
            name = item.get("name")

            if name not in similarity_by_name:
                continue

            similarity = similarity_by_name[name]

            if similarity is not None:
                item["structure_similarity"] = similarity

            item["structure_rank"] = rank_by_name[name] + 1

        requested_start = ranking["requested_start"]
        requested_rows = ranking["requested_rows"]

        if requested_rows is None:
            search_results["results"] = results[requested_start:]
        else:
            requested_end = requested_start + requested_rows
            search_results["results"] = results[
                requested_start:requested_end
            ]

        return search_results

    def _uses_chemical_relevance_sort(self, search_params):
        """
        Treat a missing sort on an active structure search as relevance.

        request.args is checked directly so this remains correct regardless
        of whether another plugin has already applied the empty-listing
        name-ascending default.
        """

        requested_sort = request.args.get("sort")

        if requested_sort:
            return requested_sort in (CHEMICAL_RELEVANCE_SORT, "rank")

        search_params["sort"] = CHEMICAL_RELEVANCE_SORT
        return True

    def _prepare_chemical_ranking(self, search_params, results):
        ranked_results = [
            item
            for item in results
            if item.get("name")
        ]
        rank_by_name = {
            item["name"]: position
            for position, item in enumerate(ranked_results)
        }
        similarity_by_name = {
            item["name"]: item.get("similarity")
            for item in ranked_results
        }

        requested_start = self._nonnegative_int(
            search_params.get("start"),
            default=0,
        )
        requested_rows = self._nonnegative_int(
            search_params.get("rows"),
            default=None,
        )

        extras = search_params.get("extras")

        if not isinstance(extras, dict):
            extras = {}
            search_params["extras"] = extras

        extras[STRUCTURE_RANK_EXTRAS_KEY] = {
            "rank_by_name": rank_by_name,
            "similarity_by_name": similarity_by_name,
            "requested_start": requested_start,
            "requested_rows": requested_rows,
        }

        # Solr must return every authorized structure match before we can
        # apply the RDKit order and then select the requested page.
        search_params["start"] = 0
        search_params["rows"] = len(rank_by_name)
        search_params["sort"] = CHEMICAL_RELEVANCE_SORT

    def _nonnegative_int(self, value, default):
        if value is None or value == "":
            return default

        try:
            value = int(value)
        except (TypeError, ValueError):
            return default

        return value if value >= 0 else default

    def _append_fq(self, search_params, fq):
        """
        Append a Solr fq safely.

        CKAN may already have fq as a string or a list. We normalize it to a
        plain string to avoid nested lists like:
            fq = [[old_fq, new_fq], '+site_id:"default"']
        """

        existing_fq = search_params.get("fq")

        if not existing_fq:
            search_params["fq"] = fq
            return

        if isinstance(existing_fq, list):
            flat_parts = []

            for item in existing_fq:
                if isinstance(item, list):
                    flat_parts.extend([str(x) for x in item if x])
                elif item:
                    flat_parts.append(str(item))

            flat_parts.append(fq)
            search_params["fq"] = " ".join(flat_parts)
            return

        search_params["fq"] = "{} {}".format(existing_fq, fq)

    def _build_name_filter(self, names):
        """
        Build a Solr filter for many package names.

        Use Solr terms query parser instead:
            {!terms f=name}a,b,c
        """

        cleaned_names = [
            self._escape_solr_terms_value(name)
            for name in names
            if name
        ]

        if not cleaned_names:
            return 'name:"__chemstructure_no_results__"'

        return "{!terms f=name}" + ",".join(cleaned_names)

    def _escape_solr_terms_value(self, value):
        """
        Escape values for Solr {!terms} parser.

        Package names normally do not contain commas, but escape defensively.
        """

        return str(value).replace("\\", "\\\\").replace(",", "\\,")

    def _remove_structure_params_from_fq(self, search_params):
        """
        Remove structure_query, structure_mode and threshold pseudo-filters
        from fq.

        Some CKAN/theme search code may turn unknown URL parameters into fq
        terms. These are not real Solr fields, so they must not reach Solr.
        """

        fq = search_params.get("fq")

        if not fq:
            return

        def clean_one(value):
            value = str(value)

            parts = value.split()
            cleaned_parts = []

            for part in parts:
                if part.startswith("structure_query:"):
                    continue
                if part.startswith("structure_mode:"):
                    continue
                if part.startswith("threshold:"):
                    continue

                cleaned_parts.append(part)

            return " ".join(cleaned_parts)

        if isinstance(fq, list):
            cleaned = []

            for item in fq:
                if isinstance(item, list):
                    for nested_item in item:
                        cleaned_item = clean_one(nested_item)
                        if cleaned_item:
                            cleaned.append(cleaned_item)
                else:
                    cleaned_item = clean_one(item)
                    if cleaned_item:
                        cleaned.append(cleaned_item)

            search_params["fq"] = " ".join(cleaned)
            return

        search_params["fq"] = clean_one(fq)
