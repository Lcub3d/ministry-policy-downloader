# Contributing

Thanks for helping improve Ministry Policy Downloader.

## Before opening a change

- Search existing issues and keep each pull request focused on one problem.
- Do not commit downloaded pages, attachments, spreadsheets, logs, backups,
  credentials, or machine-specific absolute paths.
- Preserve the distinction between configured index coverage and complete
  official-site coverage.
- Respect website rate limits and do not add login, CAPTCHA, or access-control
  bypasses.

## Local setup

```bash
python -m venv .venv
python -m pip install -e .
python -m unittest discover -s tests -v
```

On Windows, activate with `.\.venv\Scripts\Activate.ps1`; on macOS or Linux,
use `source .venv/bin/activate`.

## Pull requests

- Add or update the smallest offline test that proves the behavior.
- Site parser tests must use synthetic or minimal redacted HTML fixtures and
  must not contact live government websites in CI.
- Keep shared behavior in the common pipeline and site-specific selectors in
  `policy_harvester.sites`.
- Update the README when a user-facing command, output, source, or limitation
  changes.
- Run the full test suite before submitting.

By contributing, you agree that your contribution is licensed under the
repository's MIT License.


