# Project URL plan

`pyproject-metadata` applies an explicit parsing plan to the
`[project.urls]` table (implemented in `pyproject_metadata.urls` and
enforced by `StandardMetadata.validate`):

- **Label categorization.** Labels are classified into well-known
  categories via `URL_LABEL_CATEGORIES`. Lookup is case-insensitive and
  treats `-`/`_`/whitespace runs as a single space, so `Homepage`,
  `home_page`, and `主页` all resolve to the `homepage` category.
  Unknown labels are kept as-is and are not categorized.
- **Originals preserved.** The original labels and original URLs are
  always kept for output (`Project-URL` entries). Entries are never
  normalized away, and multiple addresses of the same category are not
  overwritten by default.
- **Conflict diagnostics.** If several labels map to the same category
  but give *different* addresses, one diagnostic per category is
  reported; with `all_errors=True` all such diagnostics are collected
  together in the `ExceptionGroup`. Labels mapping to the same category
  *and* the same address are all kept, preserving both sources.
- **Absolute URLs only.** Every URL must be a valid absolute URL with a
  supported scheme (`http`, `https`). Unsupported schemes such as
  `mailto:` are explicitly rejected. Fragments and query strings are
  significant and are never stripped.
- **Offline validation.** URLs are parsed and checked locally; they are
  never fetched over the network.
- **No partial metadata.** If validation fails, parsing/compilation
  raises (`ConfigurationError`, or `ExceptionGroup` with
  `all_errors=True`) instead of emitting a half-written metadata
  message.

## Running the tests

The URL plan tests live in `tests/test_urls.py`:

```console
$ python -m pytest tests/test_urls.py
```

The related metadata tests can be run with:

```console
$ python -m pytest tests/test_urls.py tests/test_standard_metadata.py
```
