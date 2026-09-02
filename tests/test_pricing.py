import copy

import pytest

from pricing import (
    ConditionTier,
    PricingInputError,
    ProfitabilityStatus,
    apply_price_ending,
    build_analysis_snapshot,
    build_research_queries,
    calculate_scenario,
    comparables_to_rows,
    default_msrp,
    default_pricing,
    format_cents,
    merge_comparable_rows,
    msrp_target_cents,
    parse_money_to_cents,
    parse_percent_to_bps,
    summarize_active_p25,
    summarize_sold_population,
    type7_percentile_cents,
)


def _channel(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "shipping_mode": "FREE",
        "buyer_shipping_charge_cents": 0,
        "seller_shipping_cost_cents": 625,
        "acquisition_cost_cents": 407,
        "packaging_cost_cents": 0,
        "other_costs_cents": 0,
        "marketplace_fee_bps": 1360,
        "promotion_fee_bps": 200,
        "fixed_order_fee_cents": 40,
    }
    value.update(overrides)
    return value


def _comp(
    comp_id: str,
    delivered_cents: int,
    *,
    tier: str = "EXACT",
    event_date: str = "2026-08-01",
    item_id: str | None = None,
    active: bool = False,
) -> dict[str, object]:
    return {
        "comp_id": comp_id,
        "item_id": item_id or comp_id,
        "transaction_id": None,
        "title": f"Comparable {comp_id}",
        "url": f"https://example.invalid/{comp_id}",
        "event_date": None if active else event_date,
        "item_price_cents": delivered_cents,
        "shipping_cents": 0,
        "quantity": 1,
        "sale_format": "FIXED_PRICE",
        "match_tier": tier,
        "included": True,
        "exclusion_reason": None,
        "operator_note": None,
    }


def _package_with_market(prices: list[int]) -> dict[str, object]:
    pricing = default_pricing("used")
    pricing["condition_tier"] = "USED"
    pricing["research"]["demand_position"] = "BALANCED"  # type: ignore[index]
    pricing["research"]["sold_comps"] = [  # type: ignore[index]
        _comp(f"SOLD-{index:03d}", price) for index, price in enumerate(prices, start=1)
    ]
    pricing["channels"]["ebay"].update(  # type: ignore[index]
        {
            "shipping_mode": "FREE",
            "buyer_shipping_charge_cents": 0,
            "price_ending_policy": "EXACT_CENTS",
        }
    )
    return {
        "product": {"msrp": default_msrp()},
        "pricing": pricing,
    }


def test_money_and_percent_parsing_use_exact_integer_units() -> None:
    assert parse_money_to_cents("$1,234.56") == 123456
    assert parse_money_to_cents("0") == 0
    assert parse_money_to_cents("") is None
    assert parse_percent_to_bps("13.6%") == 1360
    assert format_cents(123456) == "1234.56"

    with pytest.raises(PricingInputError):
        parse_money_to_cents("1.999")
    with pytest.raises(PricingInputError):
        parse_percent_to_bps("100.01")


def test_oversized_money_is_rejected_and_cannot_become_an_msrp_target() -> None:
    oversized_dollars = "9" * 100

    with pytest.raises(PricingInputError):
        parse_money_to_cents(oversized_dollars)

    assert msrp_target_cents(10**100, ConditionTier.NWT) is None


def test_msrp_condition_values_are_advisory_rounded_targets() -> None:
    assert msrp_target_cents(5999, ConditionTier.NWT) == 1500
    assert msrp_target_cents(5999, ConditionTier.NWOT) == 1200
    assert msrp_target_cents(5999, ConditionTier.USED) == 600
    assert msrp_target_cents(5999, ConditionTier.UNSPECIFIED) is None


def test_tayion_economics_and_offer_scenarios_match_prior_example() -> None:
    initial = calculate_scenario(_channel(), 2199, 0)
    offer = calculate_scenario(_channel(), 2199, 2000)

    assert initial.net_profit_cents == 784
    assert initial.margin_bps == 3565
    assert initial.roi_bps == 19263
    assert initial.profitability_status == ProfitabilityStatus.PREFERRED
    assert offer.item_price_cents == 1759
    assert offer.net_profit_cents == 413
    assert offer.profitability_status == ProfitabilityStatus.MIXED


def test_exact_preferences_do_not_count_as_above() -> None:
    scenario = calculate_scenario(
        _channel(
            seller_shipping_cost_cents=0,
            acquisition_cost_cents=300,
            other_costs_cents=400,
            marketplace_fee_bps=0,
            promotion_fee_bps=0,
            fixed_order_fee_cents=0,
        ),
        1000,
        0,
    )

    assert scenario.margin_bps == 3000
    assert scenario.roi_bps == 10000
    assert scenario.profitability_status == ProfitabilityStatus.BELOW_PREFERENCES


def test_zero_or_missing_acquisition_cost_never_becomes_infinite_roi() -> None:
    zero_cost = calculate_scenario(_channel(acquisition_cost_cents=0), 2199, 0)
    missing_fee = calculate_scenario(_channel(marketplace_fee_bps=None), 2199, 0)

    assert zero_cost.roi_bps is None
    assert zero_cost.profitability_status == ProfitabilityStatus.UNKNOWN
    assert missing_fee.net_profit_cents is None


def test_type7_percentile_and_price_ending_are_deterministic() -> None:
    assert type7_percentile_cents([1000, 2000, 3000, 4000], 2500) == 1750
    assert apply_price_ending(2207, "END_99") == 2199
    assert apply_price_ending(1000, "END_99") == 1000
    assert apply_price_ending(1050, "END_99") == 1000
    assert apply_price_ending(999, "EXACT_CENTS") == 999


def test_fallback_and_auction_comps_cannot_drag_down_exact_market() -> None:
    values = [_comp("S1", 2000), _comp("S2", 2100), _comp("S3", 2200)]
    values.append(_comp("LOW", 100, tier="FALLBACK"))
    auction = _comp("AUCT", 200, tier="EXACT")
    auction["sale_format"] = "AUCTION"
    values.append(auction)

    summary = summarize_sold_population(values)

    assert summary.population_tier == "EXACT"
    assert summary.count == 3
    assert summary.p25_cents == 2050


def test_deduplication_uses_sale_events_not_item_id_alone() -> None:
    first = _comp("S1", 2000, item_id="ITEM-1", event_date="2026-08-01")
    second = _comp("S2", 2100, item_id="ITEM-1", event_date="2026-08-02")
    duplicate = copy.deepcopy(first)
    duplicate["comp_id"] = "S3"

    summary = summarize_sold_population([first, second, duplicate])

    assert summary.count == 2
    assert summary.duplicate_comp_ids == ("S3",)


def test_included_sold_comps_without_a_complete_event_identity_are_ignored() -> None:
    valid_transaction = _comp("VALID", 2000)
    valid_transaction.update(
        transaction_id="TRANSACTION-1",
        item_id=None,
        event_date=None,
        quantity=None,
    )
    missing_item = _comp("NO-ITEM", 2100)
    missing_item["item_id"] = None
    missing_date = _comp("NO-DATE", 2200)
    missing_date["event_date"] = None
    missing_quantity = _comp("NO-QUANTITY", 2300)
    missing_quantity["quantity"] = None

    summary = summarize_sold_population(
        [valid_transaction, missing_item, missing_date, missing_quantity]
    )

    assert summary.count == 1
    assert summary.p25_cents == 2000


def test_included_active_comps_without_item_id_or_url_are_ignored() -> None:
    missing_identity = _comp("MISSING", 1800, active=True)
    missing_identity["item_id"] = None
    missing_identity["url"] = None
    url_identity = _comp("URL-ONLY", 1900, active=True)
    url_identity["item_id"] = None

    active_p25, count, duplicate_ids = summarize_active_p25([missing_identity, url_identity])

    assert active_p25 == 1900
    assert count == 1
    assert duplicate_ids == ()


def test_iqr_outliers_activate_at_eight_records_not_seven() -> None:
    seven = [_comp(f"S{index}", 1000) for index in range(1, 7)] + [_comp("HIGH", 10000)]
    eight = seven[:-1] + [_comp("S7", 1000), _comp("HIGH", 10000)]

    seven_summary = summarize_sold_population(seven)
    eight_summary = summarize_sold_population(eight)

    assert seven_summary.outlier_comp_ids == ()
    assert eight_summary.outlier_comp_ids == ("HIGH",)
    assert eight_summary.count == 7
    assert eight_summary.sample_strength == "LIMITED"

    package = _package_with_market([1000, 1000, 1000, 1000, 1000, 1000, 1000, 10000])
    analysis = build_analysis_snapshot(package)

    assert analysis["outlier_comp_ids"] == ["SOLD-008"]
    assert analysis["recommendation"] == "MANUAL_REVIEW"


def test_active_market_can_cap_but_never_raise_sold_target() -> None:
    low_active = [_comp("A1", 1800, active=True)]
    high_active = [_comp("A2", 4000, active=True)]

    assert summarize_active_p25(low_active)[0] == 1800
    assert summarize_active_p25(high_active)[0] == 4000

    low_package = _package_with_market([2000, 2100, 2200])
    low_package["pricing"]["research"]["active_comps"] = low_active  # type: ignore[index]
    high_package = _package_with_market([2000, 2100, 2200])
    high_package["pricing"]["research"]["active_comps"] = high_active  # type: ignore[index]

    assert build_analysis_snapshot(low_package)["recommended_initial_item_price_cents"] == 1799
    assert build_analysis_snapshot(high_package)["recommended_initial_item_price_cents"] == 2050


def test_ten_dollar_gate_uses_initial_item_price_not_shipping_or_offers() -> None:
    below = _package_with_market([999, 999, 999])
    exact = _package_with_market([1000, 1000, 1000])

    assert build_analysis_snapshot(below)["recommendation"] == "LIVE_SELL"
    assert build_analysis_snapshot(exact)["recommendation"] == "LIST"

    exact["pricing"]["channels"]["ebay"].update(  # type: ignore[index]
        _channel(
            buyer_shipping_charge_cents=500,
            seller_shipping_cost_cents=0,
            acquisition_cost_cents=100,
            marketplace_fee_bps=0,
            promotion_fee_bps=0,
            fixed_order_fee_cents=0,
            operator_initial_price_cents=1000,
        )
    )
    analysis = build_analysis_snapshot(exact)
    scenarios = analysis["scenarios"]
    assert scenarios[-1]["item_price_cents"] == 800
    assert analysis["operator_initial_price_status"] == "PASSES_LISTING_MINIMUM"


def test_shipping_is_required_and_subtracted_from_delivered_market_ceiling() -> None:
    package = _package_with_market([2000, 2100, 2200])
    ebay = package["pricing"]["channels"]["ebay"]  # type: ignore[index]
    ebay["buyer_shipping_charge_cents"] = None
    assert build_analysis_snapshot(package)["recommendation"] == "MANUAL_REVIEW"

    ebay["buyer_shipping_charge_cents"] = 500
    ebay["shipping_mode"] = "BUYER_PAID"
    analysis = build_analysis_snapshot(package)
    assert analysis["market_delivered_ceiling_cents"] == 2050
    assert analysis["market_item_ceiling_cents"] == 1550


@pytest.mark.parametrize(
    ("shipping_mode", "buyer_shipping_charge_cents"),
    [
        ("UNSPECIFIED", 0),
        ("FREE", None),
        ("FREE", 500),
        ("BUYER_PAID", None),
    ],
)
def test_incomplete_or_inconsistent_shipping_never_produces_a_recommendation(
    shipping_mode: str,
    buyer_shipping_charge_cents: int | None,
) -> None:
    package = _package_with_market([2000, 2100, 2200])
    ebay = package["pricing"]["channels"]["ebay"]  # type: ignore[index]
    ebay["shipping_mode"] = shipping_mode
    ebay["buyer_shipping_charge_cents"] = buyer_shipping_charge_cents

    analysis = build_analysis_snapshot(package)

    assert analysis["recommended_initial_item_price_cents"] is None
    assert analysis["recommendation"] == "MANUAL_REVIEW"
    assert analysis["calculation_status"] == "INVALID"


def test_sparse_results_require_manual_review_even_when_a_price_exists() -> None:
    package = _package_with_market([2000, 2100])

    analysis = build_analysis_snapshot(package)

    assert analysis["recommended_initial_item_price_cents"] == 2025
    assert analysis["recommendation"] == "MANUAL_REVIEW"


def test_msrp_target_requires_resolved_supported_and_operator_reviewed_evidence() -> None:
    package = _package_with_market([2000, 2100, 2200])
    package["sources"] = [{"source_id": "S001"}]
    supported = {
        "amount_cents": 5999,
        "currency": "USD",
        "basis": "CURRENT",
        "confidence": "EXACT",
        "source_ids": ["S001"],
        "observed_on": "2026-09-01",
        "operator_reviewed": True,
        "note": None,
    }
    package["product"]["msrp"] = supported  # type: ignore[index]
    assert build_analysis_snapshot(package)["msrp_target_cents"] == 600

    for changes in (
        {"operator_reviewed": False},
        {"basis": "UNKNOWN"},
        {"confidence": "INSUFFICIENT"},
        {"source_ids": ["MISSING"]},
    ):
        package["product"]["msrp"] = {**supported, **changes}  # type: ignore[index]
        assert build_analysis_snapshot(package)["msrp_target_cents"] is None


def test_comparable_edit_preserves_unknown_fields_and_assigns_new_ids() -> None:
    original = [_comp("SOLD-001", 2000)]
    original[0]["future_evidence"] = {"keep": True}
    rows = comparables_to_rows(original)
    rows[0]["Item price ($)"] = "21.50"
    rows.append(
        {
            "Comp ID": "",
            "Include": True,
            "Match tier": "NEAR_EXACT",
            "Format": "FIXED_PRICE",
            "Title": "New comp",
            "Item ID": "ITEM-2",
            "Event date": "2026-08-02",
            "Item price ($)": "19.99",
            "Shipping ($)": "0",
            "Quantity": 1.0,
        }
    )

    merged = merge_comparable_rows(original, rows, prefix="SOLD")

    assert merged[0]["item_price_cents"] == 2150
    assert merged[0]["future_evidence"] == {"keep": True}
    assert merged[1]["comp_id"] == "SOLD-002"
    assert merged[1]["quantity"] == 1


def test_comparable_quantity_rejects_fractional_numeric_editor_values() -> None:
    rows = comparables_to_rows([_comp("SOLD-001", 2000)])
    rows[0]["Quantity"] = 1.5

    with pytest.raises(PricingInputError) as error:
        merge_comparable_rows([], rows, prefix="SOLD")

    assert error.value.code == "INVALID_QUANTITY"


def test_comparable_rows_normalize_configured_editor_column_types() -> None:
    rows = comparables_to_rows(
        [
            {
                "comp_id": 123,
                "included": "yes",
                "match_tier": 7,
                "sale_format": False,
                "title": 42,
                "item_id": 99,
                "transaction_id": None,
                "event_date": 20260901,
                "item_price_cents": "invalid",
                "shipping_cents": None,
                "quantity": "fractional",
                "url": 456,
                "exclusion_reason": True,
                "operator_note": ["future", "value"],
            }
        ]
    )

    assert rows == [
        {
            "Comp ID": "123",
            "Include": False,
            "Match tier": "CONTEXT_ONLY",
            "Format": "UNKNOWN",
            "Title": "42",
            "Item ID": "99",
            "Transaction ID": None,
            "Event date": "20260901",
            "Item price ($)": "",
            "Shipping ($)": "",
            "Quantity": None,
            "URL": "456",
            "Exclusion reason": "True",
            "Operator note": "['future', 'value']",
        }
    ]


def test_search_plan_moves_from_exact_identifiers_to_controlled_broad_query() -> None:
    package = {
        "input": {"brand": "Example", "upc": "012345678905", "mpn": "MODEL-1"},
        "product": {
            "name": "Example Shirt",
            "product_type": "Dress Shirt",
            "attributes": {"Size": "L", "Color": "Blue"},
        },
    }

    queries = build_research_queries(package)

    assert queries[0] == ("Exact UPC", "012345678905")
    assert queries[-1] == ("Controlled broad search", "Example Dress Shirt L Blue")
