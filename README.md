# Instagram Insights Estimation API

A compliant API scaffold for estimating public Instagram profile metrics such as average likes, comments, views, location ratio, and gender ratio by username.

> This project intentionally does **not** include scraping evasion, proxy rotation, fingerprint bypassing, or automated access to private/non-consented Instagram insights. Use Meta's official APIs, creator OAuth, licensed data providers, or first-party imported data. Audience demographics are estimates and should be returned with confidence scores and clear source labels.

## Architecture

```text
[Request] -> HTTP API -> Auth/Rate Limit (gateway-ready)
    -> Orchestrator
    -> Data Provider Adapter (public crawler, official API, licensed provider, OAuth import)
    -> Normalizer
    -> Estimator Service
    -> Repository/Cache
    -> API Response
```

### Components

- `app/providers`: data-source adapters. `PublicInstagramCrawlerProvider` fetches public profile HTML and parses metadata/embedded JSON; `MockProvider` is deterministic for local development/tests.
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

## Provider selection

- `INSIGHTS_PROVIDER=crawler` (default): performs a real HTTP fetch of `https://www.instagram.com/{username}/` and parses public HTML metadata plus embedded JSON when available.
- `INSIGHTS_PROVIDER=mock`: returns deterministic generated data for repeatable tests and offline development.

The crawler can only return data that Instagram exposes in public HTML at request time. If likes, comments, views, or demographics are not present, the estimator returns transparent estimates with low confidence scores.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
INSIGHTS_PROVIDER=crawler python -m app.main
```

## Testing

```bash
pytest
```

## Production notes

1. The default `INSIGHTS_PROVIDER=crawler` path uses a direct public-page crawler implemented with Python's `HTMLParser`/`urllib`. It does not use proxy rotation, fingerprint bypassing, login cookies, CAPTCHA solving, or other anti-blocking mechanisms.
2. Use `INSIGHTS_PROVIDER=mock python -m app.main` for deterministic local development without network calls.
3. For higher reliability, replace or combine the crawler with one or more compliant providers:
   - Meta Instagram Graph API for authenticated creators who grant permission.
   - Licensed social data providers with contractual rights to provide profile metrics.
   - First-party creator imports for ground-truth labels.
4. Store normalized data and model features in Postgres/ClickHouse.
5. Use Redis for response and job cache TTLs.
6. Add a job queue for refreshes and batch requests.
7. Add model training pipelines with labeled data from opted-in creators.
8. Audit all data access and expose confidence/source fields to downstream users.

## Excel/CSV post metrics enrichment

If you have a spreadsheet of public Instagram post links, run the enrichment CLI to append public counts next to each link:

```bash
python -m app.excel_insights campaign_links.xlsx --url-column A --first-data-row 2
```

The command writes a new workbook named `campaign_links_with_insights.xlsx` by default and adds `views`, `likes`, `comments`, `shares`, `saves`, `reposts`, `shortcode`, `source`, and `error` columns. CSV files are also supported without extra packages:

```bash
python -m app.excel_insights campaign_links.csv --url-column A --first-data-row 2
```

For `.xlsx` input, install the runtime dependency first:

```bash
pip install -r requirements.txt
```

The tool only reads counts that are present in public post HTML/embedded JSON for `/p/`, `/reel/`, or `/tv/` links. Comments are parsed from both public metadata and common embedded JSON fields. Shares, saves, and reposts are written when Instagram exposes matching public fields, but these values are often private/non-public and will be blank when absent. The tool does not log in, scrape private insights, rotate proxies, bypass access controls, or guarantee counts when Instagram does not expose them publicly.
