"""HTTP routers for the LedgerMind API.

Each router is a thin presentation layer: it parses/validates the request via
the Pydantic schemas, calls into :mod:`app.models` / :mod:`app.services`,
and serialises the result. No SQL lives here.
"""