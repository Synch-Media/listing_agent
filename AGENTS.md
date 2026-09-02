# AGENTS.md

## Project purpose

GoodeDeals Listing Workspace is a fresh local Streamlit application for opening,
reviewing, editing, and downloading JSON created by the GoodeDeals Listing
Builder GPT. It provides copy-ready fields for manually creating eBay and
Shopify listings.

This project is separate from `procurement_analyzer`. Do not read from, write to,
or reuse implementation code from that project unless the user explicitly asks
for a future integration.

## Current scope

- Keep the application local and operator-driven.
- Do not add eBay, Shopify, OpenAI, or other external API calls without explicit
  approval.
- Do not publish listings, create marketplace drafts, or mutate marketplace
  data.
- Do not add pricing, inventory, procurement, authentication, hosting, or a
  database unless explicitly requested.
- Preserve unfamiliar JSON fields whenever the operator edits known fields.
- Treat Brand as required and UPC and MPN as optional.
- Preserve `null` attribute values so non-applicable fields can remain blank.
- Keep manual review required; the operator remains responsible for final
  marketplace corrections.

## Commands

Run from this directory.

```powershell
uv sync --all-groups
uv run streamlit run streamlit_app.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Definition of done

A behavior change is complete only when relevant tests pass, Ruff lint passes,
Ruff formatting passes, and the Streamlit app has a smoke test. Never commit raw
listing JSON, product images, credentials, databases, or other user source data.
