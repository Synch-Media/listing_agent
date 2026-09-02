import copy
import json
from pathlib import Path
from typing import Any

from streamlit.testing.v1 import AppTest

from pricing import default_pricing

APP_FILE = Path(__file__).parents[1] / "streamlit_app.py"


def _package() -> dict[str, Any]:
    description = "A concise product description.\nOffers are Welcome"
    return {
        "schema_version": "1.0",
        "package_type": "goodeals_listing_content",
        "input": {
            "brand": "Example Brand",
            "upc": None,
            "mpn": "MODEL-1",
            "image_filenames": ["front.png"],
        },
        "product": {
            "brand": "Example Brand",
            "upc": None,
            "mpn": "MODEL-1",
            "name": "Example Product",
            "condition": "New",
            "condition_details": "Unused.",
            "future_product_field": {"keep": True},
        },
        "images": [{"order": 1, "filename": "front.png"}],
        "sources": [],
        "platforms": {
            "ebay": {
                "title": "Example Brand Example Product",
                "description": description,
                "category_suggestion": "Example > Category",
                "item_specifics": {
                    "Brand": "Example Brand",
                    "UPC": None,
                    "MPN": "MODEL-1",
                    "Future attribute": None,
                },
                "future_ebay_field": [1, 2, 3],
            },
            "shopify": {
                "title": "Example Brand Example Product",
                "description": description,
                "vendor": "Example Brand",
                "category_suggestion": "Example > Category",
                "product_type": "Example Product",
                "attributes": {"Color": "Blue", "Future attribute": None},
                "mpn_metafield_candidate": "MODEL-1",
                "tags": ["Example Brand", "Blue"],
                "seo_title": "Example Brand Example Product",
                "seo_description": "Example product in blue.",
                "url_handle": "example-brand-example-product",
                "future_shopify_field": {"keep": "yes"},
            },
        },
        "review": {
            "status": "ready_for_import",
            "missing_information": [],
            "warnings": [],
            "manual_review_required": True,
        },
        "future_top_level_field": {"keep": "always"},
    }


def _uploaded_app(package: dict[str, Any]) -> AppTest:
    app = AppTest.from_file(str(APP_FILE)).run(timeout=10)
    app.get("file_uploader")[0].upload(
        "sample-listing.json",
        json.dumps(package).encode("utf-8"),
        "application/json",
    ).run(timeout=10)
    return app


def _element_with_label(elements: Any, label: str) -> Any:
    matches = [element for element in elements if element.label == label]
    assert len(matches) == 1
    return matches[0]


def test_app_renders_before_a_file_is_selected() -> None:
    app = AppTest.from_file(str(APP_FILE)).run(timeout=10)

    assert not app.exception
    assert app.title[0].value == "GoodeDeals listing workspace"
    assert "Choose the JSON" in app.info[0].value


def test_shared_save_synchronizes_identifiers_and_preserves_unknown_fields() -> None:
    package = _package()
    app = _uploaded_app(package)

    assert app.session_state["listing_download_filename"] == "sample-listing-edited.json"

    _element_with_label(app.text_input, "Brand").set_value("Updated Brand")
    _element_with_label(app.text_input, "UPC").set_value("012345678905")
    _element_with_label(app.text_input, "MPN").set_value("MODEL-2")
    _element_with_label(app.button, "Save shared edits").click().run(timeout=10)

    assert not app.exception
    assert any(message.value == "Shared listing edits saved." for message in app.success)
    updated = app.session_state["listing_package"]
    assert updated["input"]["brand"] == "Updated Brand"
    assert updated["product"]["brand"] == "Updated Brand"
    assert updated["platforms"]["ebay"]["item_specifics"]["Brand"] == "Updated Brand"
    assert updated["platforms"]["shopify"]["vendor"] == "Updated Brand"
    assert updated["platforms"]["ebay"]["item_specifics"]["UPC"] == "012345678905"
    assert updated["platforms"]["ebay"]["item_specifics"]["MPN"] == "MODEL-2"
    assert updated["future_top_level_field"] == package["future_top_level_field"]
    assert updated["product"]["future_product_field"] == {"keep": True}
    assert updated["platforms"]["ebay"]["future_ebay_field"] == [1, 2, 3]
    assert updated["platforms"]["shopify"]["future_shopify_field"] == {"keep": "yes"}
    assert any(code.value == "012345678905" for code in app.code)

    _element_with_label(app.button, "Reset uploaded file").click().run(timeout=10)

    assert not app.exception
    assert any(
        message.value == "The original uploaded file has been restored." for message in app.success
    )
    assert app.session_state["listing_package"] == package


def test_rendering_malformed_sections_does_not_replace_their_source_values() -> None:
    package = _package()
    package["product"] = ["preserve", {"this": "exact value"}]
    package["platforms"]["ebay"] = "preserve this malformed section"
    expected = copy.deepcopy(package)

    app = _uploaded_app(package)

    assert not app.exception
    assert app.session_state["listing_package"] == expected


def test_old_package_renders_pricing_tab_without_creating_pricing_data() -> None:
    package = _package()
    expected = copy.deepcopy(package)

    app = _uploaded_app(package)

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Shared data", "eBay", "Shopify", "Pricing"]
    assert app.session_state["listing_package"] == expected
    assert "msrp" not in app.session_state["listing_package"]["product"]
    assert "pricing" not in app.session_state["listing_package"]


def test_msrp_save_creates_additive_object_and_preserves_unknown_product_fields() -> None:
    package = _package()
    package["product"]["future_product_field"] = {"keep": ["this", "exactly"]}
    app = _uploaded_app(package)

    _element_with_label(app.text_input, "MSRP amount (USD)").set_value("59.99")
    _element_with_label(app.selectbox, "MSRP basis").set_value("CURRENT")
    _element_with_label(app.selectbox, "MSRP evidence confidence").set_value("EXACT")
    _element_with_label(app.text_input, "MSRP source IDs").set_value("S001, S002, S001")
    _element_with_label(app.text_input, "MSRP observed date").set_value("2026-09-01")
    _element_with_label(app.text_area, "MSRP note").set_value("Exact manufacturer evidence.")
    _element_with_label(app.checkbox, "I reviewed this MSRP evidence").set_value(True)
    _element_with_label(app.button, "Save MSRP evidence").click().run(timeout=10)

    assert not app.exception
    assert any(message.value == "MSRP evidence saved." for message in app.success)
    updated = app.session_state["listing_package"]
    assert updated["product"]["future_product_field"] == {"keep": ["this", "exactly"]}
    assert updated["product"]["msrp"] == {
        "amount_cents": 5999,
        "currency": "USD",
        "basis": "CURRENT",
        "confidence": "EXACT",
        "source_ids": ["S001", "S002"],
        "observed_on": "2026-09-01",
        "operator_reviewed": True,
        "note": "Exact manufacturer evidence.",
    }
    assert "pricing" not in updated


def test_pricing_assumptions_save_recomputes_analysis_and_preserves_unknown_fields() -> None:
    package = _package()
    package["pricing"] = {
        "future_pricing_field": {"keep": "top level"},
        "research": {"future_research_field": ["keep", "nested"]},
        "channels": {"ebay": {"future_channel_field": {"keep": True}}},
        "analysis": {
            "future_analysis_field": {"keep": "analysis"},
            "scenarios": [
                {
                    "discount_bps": 0,
                    "future_scenario_field": "keep scenario metadata",
                }
            ],
        },
        "review": {"future_review_field": "keep review metadata"},
    }
    app = _uploaded_app(package)

    _element_with_label(app.selectbox, "Condition pricing tier").set_value("NWT")
    _element_with_label(app.selectbox, "Shipping method").set_value("FREE")
    _element_with_label(app.selectbox, "Price ending").set_value("EXACT_CENTS")
    for label, value in (
        ("Initial item price (USD)", "21.99"),
        ("Acquisition cost (USD)", "5.00"),
        ("Packaging cost (USD)", "0"),
        ("Buyer shipping charge (USD)", "0"),
        ("Estimated seller shipping cost (USD)", "0"),
        ("Other selling costs (USD)", "0"),
        ("Marketplace fee (%)", "0"),
        ("Promotion fee (%)", "0"),
        ("Fixed order fee (USD)", "0"),
    ):
        _element_with_label(app.text_input, label).set_value(value)
    _element_with_label(app.button, "Save pricing assumptions").click().run(timeout=10)

    assert not app.exception
    assert any(
        message.value == "Pricing assumptions saved and results recalculated."
        for message in app.success
    )
    pricing = app.session_state["listing_package"]["pricing"]
    assert pricing["future_pricing_field"] == {"keep": "top level"}
    assert pricing["research"]["future_research_field"] == ["keep", "nested"]
    assert pricing["channels"]["ebay"]["future_channel_field"] == {"keep": True}
    assert pricing["analysis"]["future_analysis_field"] == {"keep": "analysis"}
    assert pricing["review"]["future_review_field"] == "keep review metadata"

    ebay = pricing["channels"]["ebay"]
    assert ebay["shipping_mode"] == "FREE"
    assert ebay["operator_initial_price_cents"] == 2199
    assert ebay["acquisition_cost_cents"] == 500
    assert ebay["buyer_shipping_charge_cents"] == 0
    assert pricing["condition_tier"] == "NWT"

    analysis = pricing["analysis"]
    assert analysis["calculation_status"] == "COMPLETE"
    assert analysis["recommendation"] == "MANUAL_REVIEW"
    assert analysis["operator_initial_price_status"] == "PASSES_LISTING_MINIMUM"
    assert len(analysis["scenarios"]) == 4
    assert analysis["scenarios"][0] == {
        "discount_bps": 0,
        "item_price_cents": 2199,
        "buyer_shipping_charge_cents": 0,
        "buyer_total_cents": 2199,
        "marketplace_fee_cents": 0,
        "promotion_fee_cents": 0,
        "net_profit_cents": 1699,
        "margin_bps": 7726,
        "roi_bps": 33980,
        "profitability_status": "PREFERRED",
        "future_scenario_field": "keep scenario metadata",
    }


def test_pricing_review_cannot_mark_a_below_ten_dollar_price_for_listing() -> None:
    package = _package()
    pricing = default_pricing("used")
    pricing["channels"]["ebay"].update(  # type: ignore[index]
        {
            "shipping_mode": "FREE",
            "buyer_shipping_charge_cents": 0,
            "operator_initial_price_cents": 999,
        }
    )
    package["pricing"] = pricing
    app = _uploaded_app(package)

    _element_with_label(app.selectbox, "Pricing disposition").set_value("LIST")
    _element_with_label(
        app.checkbox,
        "I reviewed the MSRP, market evidence, costs, and offer scenarios",
    ).set_value(True)
    _element_with_label(app.button, "Save pricing decision").click().run(timeout=10)

    assert not app.exception
    assert any("at least $10" in message.value for message in app.error)
    assert app.session_state["listing_package"]["pricing"]["review"] == pricing["review"]


def test_pricing_tables_accept_integer_comparable_quantities() -> None:
    package = _package()
    pricing = default_pricing("used")
    pricing["research"]["sold_comps"] = [  # type: ignore[index]
        {
            "comp_id": "SOLD-001",
            "item_id": "ITEM-001",
            "transaction_id": None,
            "title": "Comparable listing",
            "url": "https://example.invalid/item-001",
            "event_date": "2026-08-15",
            "item_price_cents": 1999,
            "shipping_cents": 0,
            "quantity": 1,
            "sale_format": "FIXED_PRICE",
            "match_tier": "EXACT",
            "included": True,
            "exclusion_reason": None,
            "operator_note": None,
        }
    ]
    package["pricing"] = pricing

    app = _uploaded_app(package)

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Shared data", "eBay", "Shopify", "Pricing"]


def test_pricing_tables_render_malformed_excluded_comparable_metadata() -> None:
    package = _package()
    pricing = default_pricing("used")
    pricing["research"]["active_comps"] = [  # type: ignore[index]
        {
            "comp_id": 2,
            "item_id": 123,
            "transaction_id": None,
            "title": 42,
            "url": 456,
            "event_date": 20260901,
            "item_price_cents": None,
            "shipping_cents": None,
            "quantity": "not-a-number",
            "sale_format": 8,
            "match_tier": 7,
            "included": False,
            "exclusion_reason": True,
            "operator_note": ["future", "value"],
        }
    ]
    package["pricing"] = pricing

    app = _uploaded_app(package)

    assert not app.exception
    assert app.error


def test_shopify_tags_use_commas_and_save_as_separate_json_values() -> None:
    app = _uploaded_app(_package())

    tags = _element_with_label(app.text_area, "Tags — separated by commas")
    assert tags.value == "Example Brand, Blue"
    tags.set_value("Example Brand, Purple, One Piece, Size L")
    _element_with_label(app.button, "Save Shopify edits").click().run(timeout=10)

    assert not app.exception
    assert app.session_state["listing_package"]["platforms"]["shopify"]["tags"] == [
        "Example Brand",
        "Purple",
        "One Piece",
        "Size L",
    ]
    assert any(code.value == "Example Brand, Purple, One Piece, Size L" for code in app.code)


def test_secondary_tools_are_accessible_without_cluttering_main_tabs() -> None:
    package = _package()
    review_warning = (
        "product.attributes.material_composition: PROXY family-level specifications; "
        "verify the garment label."
    )
    package["review"]["warnings"] = [review_warning]
    app = _uploaded_app(package)

    assert [tab.label for tab in app.tabs] == ["Shared data", "eBay", "Shopify", "Pricing"]
    assert _element_with_label(app.button, "Listing check")
    assert _element_with_label(app.button, "Full JSON editor")

    _element_with_label(app.button, "Listing check").click().run(timeout=10)

    assert not app.exception
    assert len(app.get("dialog")) == 1
    assert any(header.value == "What to review" for header in app.subheader)
    assert any("ready for your review" in message.value for message in app.info)
    assert any(
        warning.value.startswith("Material Composition: Uses closely related product")
        for warning in app.warning
    )
    assert all("PROXY" not in warning.value for warning in app.warning)

    json_app = _uploaded_app(_package())
    _element_with_label(json_app.button, "Full JSON editor").click().run(timeout=10)

    assert not json_app.exception
    assert len(json_app.get("dialog")) == 1
    assert _element_with_label(json_app.text_area, "Complete listing JSON")
