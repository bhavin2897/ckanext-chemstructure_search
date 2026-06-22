import logging

from flask import request

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit

from ckanext.chemstructure_search.action import (chemstructure_exact_search, chemstructure_rdkit_search, run_structure_search)

from ckanext.chemstructure_search.views import get_blueprints


log = logging.getLogger(__name__)


class ChemstructureSearchPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IPackageController, inherit=True)

    def update_config(self, config):
        toolkit.add_template_directory(config, "templates")
        toolkit.add_public_directory(config, "public")

    def get_blueprint(self):
        return get_blueprints()

    def get_actions(self):
        return {
            "chemstructure_exact_search": chemstructure_exact_search,
            "chemstructure_rdkit_search": chemstructure_rdkit_search,
        }

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

        if structure_mode == "substructure":
            structure_mode = "smarts"

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

        return search_params

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
        quoted_names = [
            '"{}"'.format(self._escape_solr_phrase(name))
            for name in names
        ]

        return "name:({})".format(" OR ".join(quoted_names))

    def _escape_solr_phrase(self, value):
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

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