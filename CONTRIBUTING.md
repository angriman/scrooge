# Contributing

Contributions are licensed under the Apache License 2.0, like the rest of the project
(section 5 of [LICENSE](LICENSE)).

Every change comes as a pull request against `main`, and `make check` must pass: ruff,
`mypy --strict`, and pytest with branch coverage. Never lower a threshold, loosen a rule, or
skip a test to get there.

Never commit real data. Statements, databases and `users/` stay out of git. A fixture in
`tests/fixtures/` is redacted: invented names, account numbers **and amounts**.

## Adding a source

A source is a kind of file one institution exports. It has one module in
`src/scrooge/extractors/`, and every module there uses the same interface,
`scrooge.extractors.source`. The router knows a source only through it.

1. **Fixture first.** Put a redacted file in `tests/fixtures/<name>/` and write by hand the
   transactions it must give in `expected.json`. For a PDF, store its text layer
   (`statement.txt`).
2. **Module.** Write `src/scrooge/extractors/<name>.py`, named like the folder, with a pure
   `extract(content: str, source_file: str) -> Extraction`:
   - no network, no files, no global state, no `print`;
   - money is `Decimal`, parsed from the text, never `float`; dates are `datetime.date`;
   - it states the days the statement covers (`start`, `end`) and the balances or totals
     it prints, so the import can reconcile it. A statement that prints none names its
     accounts in `unreconciled`; one with neither is refused by the router;
   - anything it cannot read raises `ExtractionError`. It never guesses.

   Reuse the parsers in `extractors/common.py` before writing your own.
3. **Declare the source** as the last statement of the module:

   ```python
   SOURCE = Source(
       name="<name>",
       label="<Bank> account statement",  # what the import page calls its files
       reads="text",
       recognises=starts_with("<its header row>"),
       extract=extract,
   )
   ```

   - `reads` is `"text"` for a CSV/TSV export, or `"pdf"` for a PDF with a text layer.
   - `recognises` looks at the content only, never the file name: a header row through
     `starts_with`, or a function that looks for what only that institution prints.
   - A scanned certificate without a text layer uses `CertificateSource` instead.
4. **Register** `<name>.SOURCE` in `router.SOURCES`, or in `router.CERTIFICATES` for a
   certificate. The order matters only if two sources could claim the same file. They must
   not: the tests check it.
5. **Tests.** Add a golden test, `tests/extractors/test_<name>.py` with
   `assert_matches_golden`. Add one test for each kind of row the format has and one for
   each refusal.

`tests/extractors/test_sources.py` fails when a module in the package declares no `SOURCE`,
names it differently from the module, is missing from the router, has no fixture,
`expected.json` or `test_<name>.py`, or recognises another source's fixture.
