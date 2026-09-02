# GoodeDeals listing workspace

A fresh, local application for reviewing and editing the JSON produced by the
GoodeDeals Listing Builder. The first version is deliberately offline: it does
not connect to eBay, Shopify, OpenAI, or any other service.

## What it does now

- Loads one GoodeDeals listing JSON file into memory.
- Shows structural errors and review warnings without discarding unfamiliar
  future attributes.
- Edits shared listing content, eBay item specifics, and Shopify attributes.
- Provides separate copy-ready boxes for each major eBay and Shopify field,
  plus a complete reference block.
- Uses comma-separated Shopify tags while preserving them as separate values in
  the downloaded JSON.
- Keeps review notes and the full JSON editor under **More tools** so the normal
  workflow stays focused on Shared data, eBay, Shopify, and Pricing.
- Records factual MSRP evidence in Shared data, including its source IDs,
  confidence, date, and operator-review status.
- Builds narrow-to-broad eBay Product Research searches for the operator to run
  manually without opening, signing in to, or scraping eBay.
- Keeps sold results and active competition separate, with editable match,
  format, shipping, inclusion, and evidence fields.
- Calculates deterministic market statistics, a market-supported initial item
  price, and list/offer profitability scenarios from operator-reviewed inputs.
- Records the operator's final `LIST`, `LIVE_SELL`, `HOLD_FOR_SEASON`, or
  `MANUAL_REVIEW` disposition without publishing anything.
- Downloads the revised JSON with `-edited` added to the filename so the
  original file is less likely to be overwritten.
- Provides an advanced full-JSON editor for fields not yet represented by a
  dedicated form.

The app does not save a database record, assign inventory, create a marketplace
draft, or publish anything. Its pricing output is decision guidance; the
operator chooses and reviews the final price.

## Setup

Open PowerShell in this folder and run:

```powershell
uv sync --all-groups
```

## Run

```powershell
uv run streamlit run streamlit_app.py
```

Streamlit will show the local address, normally `http://localhost:8501`.

## Use

1. Choose the JSON file created by the GoodeDeals Listing Builder.
2. Review the local validation message.
3. In **Shared data**, review the product facts and MSRP evidence. Keep MSRP
   unknown unless the exact item has defensible support, then save the evidence.
4. Edit the eBay and Shopify tabs. Save each section after editing.
5. Separate Shopify tags with commas, just as they will be entered in Shopify.
6. In **Pricing**, copy the manual Product Research searches from narrowest to
   broadest and run them yourself in eBay Product Research.
7. Enter the supplied research window, sell-through figures, sold results, and
   active competition. Keep item price and buyer-visible shipping separate.
8. Review each comparable, then explicitly select **Include** only for records
   that should enter the calculation. Choose the demand/competition position.
9. Enter the listing, shipping, cost, and fee assumptions. Leave unknown values
   blank rather than treating them as zero.
10. Review the market recommendation, 10%/15%/20% offer scenarios, warnings,
    margin, and ROI. Record your final operator disposition.
11. Use the copy control on the individual eBay or Shopify field boxes when
   manually creating the marketplace listing.
12. Open **More tools** only when you want the plain-English listing check or the
   full JSON editor.
13. Download the revised JSON if you want to retain your edits.

The uploaded file and edits live in the current browser session. Refreshes and
server restarts can clear unsaved work, so download the revised JSON before
closing the app.

## Pricing rules and calculations

All saved money uses integer cents and all saved rates use integer basis points
(100 basis points = 1%). Displayed dollar and percentage fields are converted
at the interface boundary without binary floating-point arithmetic.

Market evidence is based on buyer-delivered price before tax:

```text
delivered comparable price = item price + buyer-paid shipping
```

Only operator-included fixed-price records with both amounts and a usable event
identity enter the default statistics. Sold evidence needs a transaction ID or
the complete item-ID/date/price/shipping/quantity identity; active evidence
needs an item ID or URL. The app uses at least three exact matches by
themselves; otherwise it combines exact and near-exact matches. Fallback and
context-only results and auctions remain visible but do not set the default
price. Sold events are deduplicated by transaction ID when available, or by
item ID, sale date, item price, shipping, and quantity. Active listings are
deduplicated by item ID or URL.

At eight or more qualified sold records, 1.5×IQR outliers are flagged and
excluded from the preview percentile calculation. A flagged outlier forces
`MANUAL REVIEW` until the operator reviews the source rows and unchecks any
record that should be excluded. Fewer records remain visible without automatic
outlier handling. The operator-selected market position uses:

- Weak demand/high competition: sold P20.
- Balanced market: sold P25.
- Strong demand/low competition: sold P37.5.

Credible active competition can lower, but never raise, the sold-market anchor.
An active set is credible when it contains an exact match, or at least three
near-exact matches when no exact match is available. The cap is one cent below
active P25. The app then subtracts the buyer shipping charge to produce the
item-price ceiling and can end the recommendation in `.99` without exceeding
that ceiling.

The `$10` rule applies only to the initial item price before shipping. Buyer
shipping does not help it pass. A market-supported or saved initial item price
below `$10` produces `LIVE SELL`; offer scenarios may fall below `$10`. A
reviewed `LIST` decision cannot be saved without an initial item price of at
least `$10`. Fewer than three qualified sold results or a missing
demand/shipping input produces `MANUAL REVIEW`. Shipping must be explicitly
free at `$0` or buyer-paid with a positive known charge before it enters a
recommendation.

Condition-based MSRP values are guidance, not protected floors:

- New with tags: 25% of MSRP.
- New without tags: 20% of MSRP.
- Used: 10% of MSRP.

The app shows a condition target only after the MSRP has resolved evidence,
source IDs that exist in the package, and explicit operator review.

The initial price and the 10%, 15%, and 20% offer scenarios use these formulas:

```text
buyer total = discounted item price + buyer shipping charge

marketplace fee = buyer total × marketplace fee rate
promotion fee = buyer total × promotion fee rate

net profit = buyer total
             - marketplace fee
             - promotion fee
             - fixed order fee
             - acquisition cost
             - estimated seller shipping cost
             - packaging cost
             - other selling costs

margin = net profit ÷ buyer total
ROI = net profit ÷ acquisition cost
```

Offers reduce the item price only; the buyer shipping charge remains unchanged.
Money and basis-point calculations round half-up to the nearest integer unit.
The preferred health label requires margin above 30% and ROI above 100%. These
are advisory preferences, not rejection rules, and there is no fixed net-profit
minimum. If any required cost or fee is unknown, profitability remains
`UNKNOWN`; zero acquisition cost also leaves ROI unknown.

## Limits and operator control

- The app never opens, signs in to, automates, or scrapes eBay Product Research.
  The operator runs the prepared searches and enters or imports the results.
- The GoodeDeals Listing Builder may research factual MSRP evidence and
  structure Product Research screenshots or text supplied by the operator. It
  must not calculate or recommend a price.
- MSRP from an eBay seller is not verified MSRP. The app and GPT keep MSRP
  unknown when exact manufacturer, tag, archive, or corroborated retailer
  evidence is insufficient.
- The app does not invent demand thresholds. The operator selects weak,
  balanced, or strong market position after reviewing sell-through,
  competition, and seasonality.
- Calculated results remain local and advisory. Manual review is required
  before import, before copying to a marketplace, and again before publishing.

The proposed copywriting and Shopify SEO rules for the external Custom GPT are
kept in [`docs/gpt-builder-copy-guidance.md`](docs/gpt-builder-copy-guidance.md).
They are an addendum and should be merged without replacing the GPT's existing
identity, evidence, attribute, schema, or manual-review rules.

The additive MSRP and Product Research contract for the external Custom GPT is
kept in
[`docs/gpt-builder-pricing-guidance.md`](docs/gpt-builder-pricing-guidance.md).
It preserves the original `schema_version: "1.0"`, adds
`pricing_schema_version: "1.0"`, and keeps calculations and final decisions in
the local application.

## Verify

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```
