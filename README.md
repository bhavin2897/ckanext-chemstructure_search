
# ckanext-chemstructure_search

A CKAN extension that adds a prototype chemical structure search interface for molecule packages.

This plugin provides a structure search page where users can sketch a chemical structure using Ketcher, export the drawn structure as SMILES, and send it to a CKAN backend action. The backend uses RDKit to parse the query and compare it against stored molecule structures.

### Current features
* Adds a CKAN structure search page.

## CKAN to RDKit synchronization

With the `chemstructure_search` plugin enabled, CKAN 2.9 package creation and
update hooks synchronize active `type=molecule` packages into
`rdk.molecules`, `rdk.fingerprints`, and `rdk.molecule_names`. CKAN remains the
source of truth. The hook performs no network requests and never invokes
`package_update`; names copied from `alternate_name` use type
`alternate_name` and source `CKAN`. Existing names from PubChem or other
sources are not removed.

The package must contain valid `smiles` and `inchi` values. Values may be
Scheming top-level fields or CKAN extras. Invalid records are logged by package
ID/name and do not abort an unrelated harvest.

Backfill safely before enabling writes in production:

```bash
ckan -c /etc/ckan/default/ckan.ini chemstructure sync \
  --dry-run --batch-size 100 --failure-log /var/tmp/rdkit-dry-run.jsonl
```

After reviewing the dry-run failures, run the real reconciliation:

```bash
ckan -c /etc/ckan/default/ckan.ini chemstructure sync \
  --batch-size 100 --failure-log /var/tmp/rdkit-sync.jsonl
```

Use `--limit N` for a bounded run and `--start-after PACKAGE_NAME` to resume
after the last package name printed by a completed batch. Reruns are
idempotent. Dry-run rolls back each batch and does not retain database changes.

Read-only consistency counts are available with:

```bash
ckan -c /etc/ckan/default/ckan.ini chemstructure verify
```
* Embeds a local Ketcher editor build in the UI.
* Exports drawn chemical structures as SMILES.
* Sends SMILES queries to a CKAN action.
* Uses RDKit for structure parsing and fingerprint-based matching.
* Searches molecule packages stored in CKAN/PostgreSQL.
* Displays matching molecule packages in a results table
* Links results to the corresponding molecule page.

### Workflow
Ketcher sketch
→ SMILES export
→ CKAN JavaScript frontend
→ CKAN action endpoint
→ PostgreSQL molecule records
→ RDKit fingerprint / structure matching
→ molecule results table

## Requirements

Compatibility with core CKAN versions:

| CKAN version   | Compatible? |
|----------------|-------------|
| 2.8 or earlier | not tested  |
| 2.9            | Yes         |
| 2.10           | not tested  |

* Python/RDKit available in the CKAN environment. 
* Molecule packages with usable SMILES or InChI metadata. 
* **Important**: Built Ketcher frontend assets committed under public/chemstructure_search/ketcher/ 

## Installation

To install ckanext-chemstructure_search:

1. Activate your CKAN virtual environment, for example:

     `. /usr/lib/ckan/default/bin/activate`

2. Clone the source and install it on the virtualenv

    `git clone ` 
     `cd ckanext-chemstructure_search`
    `pip install -e .`
	`pip install -r requirements.txt`

3. Add `chemstructure_search` to the `ckan.plugins` setting in your CKAN
   config file (by default the config file is located at
   `/etc/ckan/default/ckan.ini`).

4. Restart CKAN. 

     `sudo service supervisor reload`
      `sudo service nginx reload`


## Config settings

The synchronized SMILES / SMARTS query text field below Ketcher is hidden by
default. To keep it visible on a test deployment, add this to that server's
CKAN configuration:

```ini
ckanext.chemstructure_search.show_ketcher_text = true
```


## Developer Notes

The Ketcher frontend was built separately and copied into the CKAN extension as static files. The production CKAN instance does not need the temporary frontend build directory or node_modules; it only needs the generated files under:

`public/chemstructure_search/ketcher/`

Before deployment, verify that:

* The Ketcher page opens directly.
* The CKAN structure search page loads the Ketcher iframe.
* Drawing a molecule exports SMILES.
* The backend RDKit action returns results.
* Result links open the corresponding molecule pages.


## Releasing a new version of ckanext-chemstructure_search

If ckanext-chemstructure_search should be available on PyPI you can follow these steps to publish a new version:

1. Update the version number in the `setup.py` file. See [PEP 440](http://legacy.python.org/dev/peps/pep-0440/#public-version-identifiers) for how to choose version numbers.

2. Make sure you have the latest version of necessary packages:

    pip install --upgrade setuptools wheel twine

3. Create a source and binary distributions of the new version:

       python setup.py sdist bdist_wheel && twine check dist/*

   Fix any errors you get.

4. Upload the source distribution to PyPI:

       twine upload dist/*

5. Commit any outstanding changes:

       git commit -a
       git push

6. Tag the new release of the project on GitHub with the version number from
   the `setup.py` file. For example if the version number in `setup.py` is
   0.0.1 then do:

       git tag 0.0.1
       git push --tags

## License

[AGPL](https://www.gnu.org/licenses/agpl-3.0.en.html)
