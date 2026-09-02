import json

import pytest

from listing_package import (
    IssueLevel,
    ListingPackageError,
    dump_listing_package,
    load_listing_package,
    mapping_to_rows,
    parse_shopify_tags,
    plain_review_note,
    plain_review_status,
    platform_copy_text,
    rows_to_mapping,
    validate_listing_package,
)
from pricing import build_analysis_snapshot, default_msrp, default_pricing


def _package() -> dict[str, object]:
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
                },
            },
            "shopify": {
                "title": "Example Brand Example Product",
                "description": description,
                "vendor": "Example Brand",
                "category_suggestion": "Example > Category",
                "product_type": "Example Product",
                "attributes": {"Color": "Blue", "Country of origin": None},
                "mpn_metafield_candidate": "MODEL-1",
                "tags": ["Example Brand", "Blue"],
                "seo_title": "Example Brand Example Product",
                "seo_description": "Example product in blue.",
                "url_handle": "example-brand-example-product",
            },
        },
        "review": {
            "status": "ready_for_import",
            "missing_information": [],
            "warnings": [],
            "manual_review_required": True,
        },
    }


def _package_with_pricing() -> dict[str, object]:
    package = _package()
    package["product"]["msrp"] = default_msrp()  # type: ignore[index]
    package["pricing"] = default_pricing(package["product"]["condition"])  # type: ignore[index]
    return package


def _pricing_comp(comp_id: str, *, active: bool = False) -> dict[str, object]:
    return {
        "comp_id": comp_id,
        "item_id": comp_id,
        "transaction_id": None,
        "title": f"Comparable {comp_id}",
        "url": f"https://example.invalid/{comp_id}",
        "event_date": None if active else "2026-08-01",
        "item_price_cents": 1999,
        "shipping_cents": 0,
        "quantity": 1,
        "match_tier": "EXACT",
        "sale_format": "FIXED_PRICE",
        "included": True,
        "exclusion_reason": None,
        "operator_note": None,
    }


def test_load_and_dump_round_trip() -> None:
    package = _package()

    loaded = load_listing_package(json.dumps(package).encode())
    dumped = dump_listing_package(loaded)

    assert json.loads(dumped) == package
    assert dumped.endswith("\n")


def test_utf8_bom_is_accepted() -> None:
    raw = b"\xef\xbb\xbf" + json.dumps(_package()).encode()

    assert load_listing_package(raw)["schema_version"] == "1.0"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"", "EMPTY_FILE"),
        (b"[]", "ROOT_NOT_OBJECT"),
        (b"{not-json}", "INVALID_JSON"),
        (b'{"brand":"one","brand":"two"}', "DUPLICATE_JSON_KEY"),
        (b'{"value":NaN}', "NON_FINITE_NUMBER"),
        (b'{"value":Infinity}', "NON_FINITE_NUMBER"),
        (b'{"value":-Infinity}', "NON_FINITE_NUMBER"),
    ],
)
def test_bad_input_is_rejected(raw: bytes, code: str) -> None:
    with pytest.raises(ListingPackageError) as error:
        load_listing_package(raw)

    assert error.value.code == code


def test_valid_package_has_no_local_issues() -> None:
    assert validate_listing_package(_package()) == ()


def test_pricing_extension_is_optional_and_a_complete_extension_validates() -> None:
    package = _package()
    package["product"]["msrp"] = default_msrp()  # type: ignore[index]
    package["pricing"] = default_pricing(package["product"]["condition"])  # type: ignore[index]

    assert validate_listing_package(package) == ()


def test_included_comps_require_a_complete_source_identity() -> None:
    package = _package_with_pricing()
    sold = _pricing_comp("SOLD-001")
    sold["item_id"] = None
    active = _pricing_comp("ACTIVE-001", active=True)
    active["item_id"] = None
    active["url"] = None
    research = package["pricing"]["research"]  # type: ignore[index]
    research["sold_comps"] = [sold]
    research["active_comps"] = [active]

    issues = validate_listing_package(package)

    assert any(
        issue.level is IssueLevel.ERROR and issue.path.startswith("pricing.research.sold_comps[0]")
        for issue in issues
    )
    assert any(
        issue.level is IssueLevel.ERROR
        and issue.path.startswith("pricing.research.active_comps[0]")
        for issue in issues
    )


@pytest.mark.parametrize(
    ("shipping_mode", "buyer_shipping_charge_cents"),
    [
        ("UNSPECIFIED", 0),
        ("FREE", None),
        ("FREE", 500),
        ("BUYER_PAID", None),
    ],
)
def test_shipping_mode_and_buyer_charge_must_be_consistent(
    shipping_mode: str,
    buyer_shipping_charge_cents: int | None,
) -> None:
    package = _package_with_pricing()
    ebay = package["pricing"]["channels"]["ebay"]  # type: ignore[index]
    ebay["shipping_mode"] = shipping_mode
    ebay["buyer_shipping_charge_cents"] = buyer_shipping_charge_cents

    issues = validate_listing_package(package)

    assert any(
        issue.level is IssueLevel.ERROR and issue.path.startswith("pricing.channels.ebay")
        for issue in issues
    )


@pytest.mark.parametrize("saved_initial_price_cents", [None, 999])
def test_reviewed_list_requires_a_saved_initial_price_of_at_least_ten_dollars(
    saved_initial_price_cents: int | None,
) -> None:
    package = _package_with_pricing()
    pricing = package["pricing"]  # type: ignore[assignment]
    ebay = pricing["channels"]["ebay"]
    ebay["shipping_mode"] = "FREE"
    ebay["buyer_shipping_charge_cents"] = 0
    ebay["operator_initial_price_cents"] = saved_initial_price_cents
    pricing["review"]["status"] = "REVIEWED"
    pricing["review"]["operator_disposition"] = "LIST"

    issues = validate_listing_package(package)

    assert any(
        issue.level is IssueLevel.ERROR
        and (
            issue.path == "pricing.channels.ebay.operator_initial_price_cents"
            or issue.path.startswith("pricing.review")
        )
        for issue in issues
    )


def test_reviewed_pricing_requires_an_operator_disposition() -> None:
    package = _package_with_pricing()
    pricing = package["pricing"]  # type: ignore[assignment]
    pricing["review"]["status"] = "REVIEWED"
    pricing["review"]["operator_disposition"] = None

    issues = validate_listing_package(package)

    assert any(
        issue.level is IssueLevel.ERROR and issue.path == "pricing.review.operator_disposition"
        for issue in issues
    )


@pytest.mark.parametrize("discounts", [[2_001], [1_000, 1_000]])
def test_offer_discounts_stop_at_twenty_percent_and_must_be_unique(
    discounts: list[int],
) -> None:
    package = _package_with_pricing()
    ebay = package["pricing"]["channels"]["ebay"]  # type: ignore[index]
    ebay["offer_discounts_bps"] = discounts

    issues = validate_listing_package(package)

    assert any(
        issue.level is IssueLevel.ERROR
        and issue.path == "pricing.channels.ebay.offer_discounts_bps"
        for issue in issues
    )


def test_saved_analysis_with_an_extra_scenario_is_stale() -> None:
    package = _package_with_pricing()
    pricing = package["pricing"]  # type: ignore[assignment]
    ebay = pricing["channels"]["ebay"]
    ebay.update(
        {
            "shipping_mode": "FREE",
            "buyer_shipping_charge_cents": 0,
            "operator_initial_price_cents": 1_000,
        }
    )
    pricing["analysis"] = build_analysis_snapshot(package)
    pricing["analysis"]["scenarios"].append({"discount_bps": 500})

    issues = validate_listing_package(package)

    assert any(
        issue.level is IssueLevel.WARNING and issue.path == "pricing.analysis" for issue in issues
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("basis", "UNKNOWN"),
        ("confidence", "INSUFFICIENT"),
        ("source_ids", ["MISSING"]),
    ],
)
def test_msrp_amount_requires_resolved_supporting_evidence(field: str, value: object) -> None:
    package = _package_with_pricing()
    package["sources"] = [{"source_id": "S001"}]
    msrp = package["product"]["msrp"]  # type: ignore[index]
    msrp.update(
        {
            "amount_cents": 5_999,
            "basis": "CURRENT",
            "confidence": "EXACT",
            "source_ids": ["S001"],
            "observed_on": "2026-09-01",
            "operator_reviewed": True,
        }
    )
    msrp[field] = value

    issues = validate_listing_package(package)

    assert any(
        issue.level is IssueLevel.ERROR and issue.path.startswith("product.msrp")
        for issue in issues
    )


def test_malformed_pricing_is_reported_without_discarding_unknown_fields() -> None:
    package = _package()
    package["product"]["msrp"] = default_msrp()  # type: ignore[index]
    package["product"]["msrp"]["amount_cents"] = 59.99  # type: ignore[index]
    pricing = default_pricing("used")
    pricing["future_pricing_field"] = {"keep": True}
    pricing["research"]["sold_comps"] = [  # type: ignore[index]
        {
            "comp_id": "SOLD-001",
            "match_tier": "EXACT",
            "sale_format": "FIXED_PRICE",
            "item_price_cents": 1999,
            "shipping_cents": None,
            "quantity": 1,
            "included": True,
        }
    ]
    package["pricing"] = pricing

    issues = validate_listing_package(package)
    round_tripped = load_listing_package(dump_listing_package(package).encode())

    assert any(issue.path == "product.msrp.amount_cents" for issue in issues)
    assert any(issue.path == "pricing.research.sold_comps[0]" for issue in issues)
    assert round_tripped["pricing"]["future_pricing_field"] == {"keep": True}


def test_stale_saved_pricing_analysis_is_a_warning() -> None:
    package = _package()
    package["product"]["msrp"] = default_msrp()  # type: ignore[index]
    pricing = default_pricing("used")
    package["pricing"] = pricing
    pricing["analysis"] = build_analysis_snapshot(package)
    pricing["analysis"]["recommendation"] = "LIST"  # type: ignore[index]

    issues = validate_listing_package(package)

    assert any(
        issue.level is IssueLevel.WARNING and issue.path == "pricing.analysis" for issue in issues
    )


def test_validation_preserves_flexibility_but_reports_mismatches() -> None:
    package = _package()
    package["platforms"]["shopify"]["title"] = "Different title"  # type: ignore[index]

    issues = validate_listing_package(package)

    assert any(issue.level is IssueLevel.WARNING for issue in issues)
    assert any(issue.path == "platforms.*.title" for issue in issues)


def test_missing_required_brand_is_an_error() -> None:
    package = _package()
    package["input"]["brand"] = " "  # type: ignore[index]

    issues = validate_listing_package(package)

    assert any(issue.level is IssueLevel.ERROR and issue.path == "input.brand" for issue in issues)


def test_ebay_identifiers_are_checked_for_consistency() -> None:
    package = _package()
    package["platforms"]["ebay"]["item_specifics"]["MPN"] = "OLD"  # type: ignore[index]

    issues = validate_listing_package(package)

    assert any(issue.level is IssueLevel.WARNING and issue.path == "MPN" for issue in issues)


def test_invalid_shopify_tag_is_reported_without_crashing_copy_output() -> None:
    package = _package()
    package["platforms"]["shopify"]["tags"] = ["Blue", None]  # type: ignore[index]

    issues = validate_listing_package(package)
    copy_text = platform_copy_text(package, "shopify")

    assert any(issue.path == "platforms.shopify.tags[1]" for issue in issues)
    assert "TAGS\nBlue" in copy_text


def test_shopify_tags_accept_commas_and_legacy_line_breaks() -> None:
    value = "Example Brand, Purple\nOne Piece,  Size L  ,"

    assert parse_shopify_tags(value) == ["Example Brand", "Purple", "One Piece", "Size L"]


def test_customer_facing_copy_warns_when_it_contains_an_identifier() -> None:
    package = _package()
    package["platforms"]["shopify"]["url_handle"] = "example-product-model-1"  # type: ignore[index]

    issues = validate_listing_package(package)

    assert any(
        issue.path == "platforms.shopify.url_handle" and "MPN" in issue.message for issue in issues
    )


def test_shopify_seo_length_guidance_is_non_destructive() -> None:
    package = _package()
    package["platforms"]["shopify"]["seo_title"] = "T" * 71  # type: ignore[index]
    package["platforms"]["shopify"]["seo_description"] = "D" * 161  # type: ignore[index]

    issues = validate_listing_package(package)

    assert any(issue.path == "platforms.shopify.seo_title" for issue in issues)
    assert any(issue.path == "platforms.shopify.seo_description" for issue in issues)


def test_review_language_is_plain_english() -> None:
    note = (
        "product.attributes.material_composition, platforms.ebay.item_specifics.Material: "
        "PROXY family-level specifications; verify the garment label."
    )

    rendered = plain_review_note(note)

    assert rendered == (
        "Material Composition: Uses closely related product specifications; "
        "verify the garment label."
    )
    assert "PROXY" not in rendered
    assert plain_review_status("ready_for_import") == (
        "The GPT finished preparing this listing. It is ready for your review."
    )
    assert "needs your input" in plain_review_status("needs_user_input")
    assert "identity" in plain_review_status("identity_unresolved").casefold()


def test_attribute_rows_round_trip_null_and_unknown_keys() -> None:
    source = {"Known": "Value", "Future platform field": None}

    assert rows_to_mapping(mapping_to_rows(source)) == source


def test_duplicate_edited_attributes_are_rejected() -> None:
    rows = [
        {"Attribute": "Brand", "Value": "One"},
        {"Attribute": "Brand", "Value": "Two"},
    ]

    with pytest.raises(ListingPackageError) as error:
        rows_to_mapping(rows)

    assert error.value.code == "DUPLICATE_ATTRIBUTE"


def test_copy_text_contains_platform_specific_sections() -> None:
    package = _package()
    package["input"]["upc"] = "012345678905"  # type: ignore[index]
    package["product"]["upc"] = "012345678905"  # type: ignore[index]
    package["platforms"]["ebay"]["item_specifics"]["UPC"] = "012345678905"  # type: ignore[index]

    ebay = platform_copy_text(package, "ebay")
    shopify = platform_copy_text(package, "shopify")

    assert "ITEM SPECIFICS\nBrand: Example Brand" in ebay
    assert "ATTRIBUTES\nColor: Blue" in shopify
    assert "BARCODE (UPC)\n012345678905" in shopify
    assert "SEO DESCRIPTION\nExample product in blue." in shopify


def test_dump_rejects_values_that_json_cannot_represent() -> None:
    package = _package()
    package["future_value"] = float("nan")

    with pytest.raises(ListingPackageError) as error:
        dump_listing_package(package)

    assert error.value.code == "INVALID_JSON_VALUE"
