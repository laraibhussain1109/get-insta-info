# Instagram Insights Estimation API

A compliant API scaffold for estimating public Instagram profile metrics such as average likes, comments, views, location ratio, and gender ratio by username.

> This project intentionally does **not** include scraping evasion, proxy rotation, fingerprint bypassing, or automated access to private/non-consented Instagram insights. Use Meta's official APIs, creator OAuth, licensed data providers, or first-party imported data. Audience demographics are estimates and should be returned with confidence scores and clear source labels.

## Architecture

```text
[Request] -> HTTP API -> Auth/Rate Limit (gateway-ready)
    -> Orchestrator
    -> Data Provider Adapter (official API, licensed provider, OAuth import)
    -> Normalizer
    -> Estimator Service
    -> Repository/Cache
    -> API Response
```

### Components

- `app/providers`: data-source adapters. The included `MockProvider` is deterministic and useful for local development/tests.
- `app/services/orchestrator.py`: coordinates cache lookup, provider fetches, normalization, and estimation.
- `app/services/estimator.py`: baseline heuristic estimator for averages and audience ratios with confidence scoring.
- `app/repositories.py`: in-memory cache/repository with TTL. Replace with Redis/Postgres for production.
- `app/main.py`: standard-library HTTP routes and server bootstrap.

## API

### Get profile insights

```bash
curl "http://localhost:8000/v1/profiles/natgeo/insights?window=30"
```

### Queue a batch

```bash
curl -X POST "http://localhost:8000/v1/profiles/batch" \
  -H "Content-Type: application/json" \
  -d '{"usernames":["natgeo","instagram"],"window":30}'
```

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m app.main
```

## Testing

```bash
pytest
```

## Production notes

1. Replace `MockProvider` with one or more compliant providers:
   - Meta Instagram Graph API for authenticated creators who grant permission.
   - Licensed social data providers with contractual rights to provide profile metrics.
   - First-party creator imports for ground-truth labels.
2. Store normalized data and model features in Postgres/ClickHouse.
3. Use Redis for response and job cache TTLs.
4. Add a job queue for refreshes and batch requests.
5. Add model training pipelines with labeled data from opted-in creators.
6. Audit all data access and expose confidence/source fields to downstream users.
