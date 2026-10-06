# Project URL plan

`pyproject-metadata` keeps `project.urls` as a raw `{label: url}` mapping by
default. Callers that want validated, categorized URLs can build an
**explicit URL plan** instead:

```python
from pyproject_metadata import StandardMetadata

metadata = StandardMetadata.from_pyproject(parsed_pyproject)
plan = metadata.url_plan()  # or: build_url_plan(metadata.urls)

for entry in plan:
    print(entry.label, entry.url, entry.category)
```

The plan follows these rules:

- **Classification**: labels are mapped to well-known categories
  (`Homepage`, `Documentation`, `Source`, `Download`, `Changelog`,
  `Bug Tracker`, `Funding`, `Mailing List`, `CI`, `Code of Conduct`) via
  `URL_CATEGORY_LABELS`. Matching is case-insensitive and treats `-`/`_` as
  spaces; multilingual aliases are included (e.g. `主页` maps to
  `Homepage`). Unknown labels are kept as-is with no category.
- **Preservation**: the original label and the original URL are always kept
  for output (`plan.as_dict()` round-trips the input). Multiple addresses in
  the same category are never silently overwritten.
- **Conflict diagnostics**: duplicate canonical labels (same category) with
  *different* addresses are reported as source conflicts — e.g. `Homepage`
  and `主页` pointing at different addresses raises a `ConfigurationError`.
  The same address from several labels is fine and both sources are kept.
  With `all_errors=True`, all diagnostics are collected into an
  `ExceptionGroup`.
- **Validation**: only absolute URLs with a supported scheme
  (`http`, `https`) are accepted. Relative URLs, malformed URLs, and
  unsupported schemes such as `mailto:` are explicitly rejected. Query
  strings and fragments are part of the address and are never stripped.
- **Offline only**: validation parses URLs with `urllib.parse.urlsplit`;
  addresses are never fetched over the network.
- **All or nothing**: if any diagnostic is produced, the build fails and no
  partial plan (or partial metadata) is returned.

## Running the tests

The URL plan tests can be run with:

```bash
python -m pytest tests/test_url_plan.py -v
```

If unrelated pytest plugins installed in the environment interfere, disable
plugin autoloading:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_url_plan.py -v
```
