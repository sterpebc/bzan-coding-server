# Sample data

Local, gitignored fixtures for manual testing. Nothing in the app hardcodes
their names or location, and none of it is read by an automated test suite.

- **`*.db`** — sample databases for exercising the upload/browse feature.
- **`*.json`** — sample data passed as an argument to `seed_api_data.py`
  (e.g. `python seed_api_data.py samples/pokemon_tcg_data.json`). The real
  source of truth for these is outside the repo — colleagues supply them, or
  they're hand-crafted locally — so they're never tracked.

Add or replace whatever files are handy for your own testing; there's no
need to keep this folder in sync with anyone else's.
