# Contributing

Use Python 3.12. Run these commands from the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m scripts.check_repository
python -m ruff check src techpulse scripts tests
python -m mypy src techpulse scripts
python -m pytest -q
python -m scripts.demo --check
python -m pip_audit --strict --local
```

The regression tests use mocked network responses. The offline demo does not read `.env` or contact providers. The dependency audit needs network access. CI runs these checks for pull requests and pushes to `main` without deployment secrets. Dependabot checks Python packages and GitHub Actions monthly. Review its changes and test them before merging.

Use publisher-backed fixtures and include the original link and publication date. For a new provider, test failure and fallback without real keys. For delivery changes, test duplicate claims, failed writes, and acknowledgment handling. For interactive changes, test authorization and update replay. Run an AI-disabled live preview when changing collection or ranking.

Do not commit credentials, SQLite files, or real chat identifiers. The repository check detects common credential patterns in tracked files and is not a full history audit. Report a suspected leak privately and rotate the credential promptly.

A redistribution license has not been selected. Contact the repository owner before redistributing code or source assets.
