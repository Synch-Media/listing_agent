"""Local GoodeDeals listing-package editor."""

from __future__ import annotations

import copy
import hashlib
from datetime import date
from typing import Any

import streamlit as st

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
from pricing import (
    MIN_INITIAL_ITEM_PRICE_CENTS,
    PricingInputError,
    build_analysis_snapshot,
    build_research_queries,
    comparables_to_rows,
    default_msrp,
    default_pricing,
    format_bps,
    format_cents,
    merge_comparable_rows,
    parse_money_to_cents,
    parse_percent_to_bps,
)

_PACKAGE_KEY = "listing_package"
_ORIGINAL_KEY = "original_listing_package"
_DIGEST_KEY = "listing_file_digest"
_FILENAME_KEY = "listing_filename"
_DOWNLOAD_FILENAME_KEY = "listing_download_filename"
_FLASH_KEY = "listing_flash_message"


def _safe_filename(name: object) -> str:
    if not isinstance(name, str):
        return "goodeals-listing-edited.json"
    basename = name.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    printable = "".join(character for character in basename if character.isprintable())
    return (
        printable[:160] if printable.lower().endswith(".json") else "goodeals-listing-edited.json"
    )


def _edited_filename(source_name: str) -> str:
    stem = source_name[:-5]
    return source_name if stem.lower().endswith("-edited") else f"{stem}-edited.json"


def _clear_editor_state() -> None:
    for key in (
        "ebay_specifics_editor",
        "shopify_attributes_editor",
        "sold_comps_editor",
        "active_comps_editor",
        "advanced_json_editor",
    ):
        st.session_state.pop(key, None)


def _load_upload(uploaded: Any) -> None:
    raw = uploaded.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    if st.session_state.get(_DIGEST_KEY) == digest:
        return
    package = load_listing_package(raw)
    st.session_state[_PACKAGE_KEY] = package
    st.session_state[_ORIGINAL_KEY] = copy.deepcopy(package)
    st.session_state[_DIGEST_KEY] = digest
    st.session_state[_FILENAME_KEY] = _safe_filename(uploaded.name)
    st.session_state[_DOWNLOAD_FILENAME_KEY] = _edited_filename(st.session_state[_FILENAME_KEY])
    _clear_editor_state()


def _package() -> dict[str, Any]:
    value = st.session_state.get(_PACKAGE_KEY)
    if not isinstance(value, dict):
        raise TypeError("No listing package is loaded.")
    return value


def _object_view(parent: dict[str, Any], name: str) -> dict[str, Any]:
    value = parent.get(name)
    return value if isinstance(value, dict) else {}


def _ensure_object(parent: dict[str, Any], name: str) -> dict[str, Any]:
    value = parent.get(name)
    if not isinstance(value, dict):
        value = {}
        parent[name] = value
    return value


def _platform_view(package: dict[str, Any], name: str) -> dict[str, Any]:
    return _object_view(_object_view(package, "platforms"), name)


def _ensure_platform(package: dict[str, Any], name: str) -> dict[str, Any]:
    return _ensure_object(_ensure_object(package, "platforms"), name)


def _optional_text(value: str) -> str | None:
    normalized = value.strip()
    return normalized or None


def _records(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "to_dict"):
        records = value.to_dict("records")
    elif isinstance(value, list):
        records = value
    else:
        return []
    return [record for record in records if isinstance(record, dict)]


def _select_index(options: list[str], value: object) -> int:
    try:
        return options.index(str(value))
    except ValueError:
        return 0


def _optional_whole_number(value: object, *, label: str) -> int | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if not text.isdigit():
        raise PricingInputError(
            "INVALID_WHOLE_NUMBER",
            f"{label} must be a nonnegative whole number.",
        )
    return int(text)


def _optional_iso_date(value: object, *, label: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        date.fromisoformat(text)
    except ValueError as error:
        raise PricingInputError(
            "INVALID_DATE",
            f"{label} must use YYYY-MM-DD.",
        ) from error
    return text


def _source_ids(value: str) -> list[str]:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", ",")
    result: list[str] = []
    seen: set[str] = set()
    for item in normalized.split(","):
        source_id = item.strip()
        if source_id and source_id not in seen:
            result.append(source_id)
            seen.add(source_id)
    return result


def _merge_defaults(target: dict[str, Any], defaults: dict[str, Any]) -> None:
    for key, default in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(default)
        elif isinstance(default, dict) and isinstance(target.get(key), dict):
            _merge_defaults(target[key], default)


def _pricing_view(package: dict[str, Any]) -> dict[str, Any]:
    value = package.get("pricing")
    return value if isinstance(value, dict) else {}


def _ensure_pricing(package: dict[str, Any]) -> dict[str, Any]:
    value = package.get("pricing")
    condition = _object_view(package, "product").get("condition")
    defaults = default_pricing(condition)
    if not isinstance(value, dict):
        value = defaults
        package["pricing"] = value
    else:
        _merge_defaults(value, defaults)
    return value


def _pricing_for_display(package: dict[str, Any]) -> dict[str, Any]:
    condition = _object_view(package, "product").get("condition")
    defaults = default_pricing(condition)
    current = package.get("pricing")
    if not isinstance(current, dict):
        return defaults
    value = copy.deepcopy(current)
    _merge_defaults(value, defaults)
    return value


def _merge_analysis(pricing: dict[str, Any], computed: dict[str, Any]) -> None:
    analysis = _ensure_object(pricing, "analysis")
    existing_scenarios = analysis.get("scenarios")
    by_discount = (
        {
            item.get("discount_bps"): item
            for item in existing_scenarios
            if isinstance(item, dict) and isinstance(item.get("discount_bps"), int)
        }
        if isinstance(existing_scenarios, list)
        else {}
    )
    merged_scenarios: list[dict[str, Any]] = []
    for scenario in computed.get("scenarios", []):
        if not isinstance(scenario, dict):
            continue
        discount = scenario.get("discount_bps")
        current = by_discount.get(discount)
        merged = copy.deepcopy(current) if isinstance(current, dict) else {}
        merged.update(copy.deepcopy(scenario))
        merged_scenarios.append(merged)
    for key, value in computed.items():
        if key != "scenarios":
            analysis[key] = copy.deepcopy(value)
    analysis["scenarios"] = merged_scenarios


def _recompute_pricing_analysis(package: dict[str, Any]) -> None:
    pricing = _ensure_pricing(package)
    _merge_analysis(pricing, build_analysis_snapshot(package))


def _mark_pricing_needs_review(pricing: dict[str, Any]) -> None:
    review = _ensure_object(pricing, "review")
    review["status"] = "NEEDS_REVIEW"


def _money_label(value: object) -> str:
    rendered = format_cents(value, currency_symbol=True)
    return rendered or "UNKNOWN"


def _percentage_label(value: object) -> str:
    rendered = format_bps(value)
    return f"{rendered}%" if rendered else "UNKNOWN"


def _set_flash(message: str) -> None:
    st.session_state[_FLASH_KEY] = message


def _copy_field(label: str, value: object, *, height: int = 100) -> None:
    st.caption(label)
    st.code(str(value or ""), language=None, wrap_lines=True, height=height)


def _mapping_copy_text(mapping: Any) -> str:
    if not isinstance(mapping, dict):
        return ""
    return "\n".join(f"{key}: {'' if value is None else value}" for key, value in mapping.items())


def _render_complete_reference(package: dict[str, Any], platform_name: str) -> None:
    with st.expander("Complete reference block"):
        try:
            copy_text = platform_copy_text(package, platform_name)
        except ListingPackageError as error:
            st.warning(
                f"The reference block is unavailable ({error.code}): {error}",
                icon=":material/warning:",
            )
        else:
            st.code(copy_text, language=None, wrap_lines=True, height=420)


def _friendly_field_name(path: str) -> str:
    known_names = {
        "platforms.*.title": "Shared title",
        "platforms.*.description": "Shared description",
        "platforms.ebay.title": "eBay title",
        "platforms.ebay.description": "eBay description",
        "platforms.ebay.item_specifics": "eBay item specifics",
        "platforms.shopify.title": "Shopify title",
        "platforms.shopify.description": "Shopify description",
        "platforms.shopify.attributes": "Shopify attributes",
        "platforms.shopify.seo_title": "Shopify SEO title",
        "platforms.shopify.seo_description": "Shopify SEO description",
        "platforms.shopify.url_handle": "Shopify URL handle",
        "input.brand": "Brand",
        "product.msrp": "MSRP evidence",
        "product.msrp.amount_cents": "MSRP amount",
        "product.msrp.source_ids": "MSRP sources",
        "pricing": "Pricing data",
        "pricing.analysis": "Pricing results",
        "pricing.research": "Product Research data",
        "pricing.channels.ebay": "eBay pricing assumptions",
        "review.manual_review_required": "Manual review",
        "images": "Images",
    }
    if path in known_names:
        return known_names[path]
    return path.replace("platforms.", "").replace("_", " ").replace(".", " › ").title()


def _render_validation(package: dict[str, Any]) -> None:
    issues = validate_listing_package(package)
    errors = [issue for issue in issues if issue.level is IssueLevel.ERROR]
    warnings = [issue for issue in issues if issue.level is IssueLevel.WARNING]
    if not issues:
        st.success("This file passed the current local checks.", icon=":material/check_circle:")
        return
    if errors:
        st.error(
            f"{len(errors)} structural issue(s) need attention before copying the listing.",
            icon=":material/error:",
        )
    elif warnings:
        st.warning(
            f"This file is usable, with {len(warnings)} item(s) to double-check.",
            icon=":material/warning:",
        )
    with st.expander("File check details"):
        for issue in issues:
            st.write(f"**{_friendly_field_name(issue.path)}:** {issue.message}")


def _render_shared_editor(package: dict[str, Any]) -> None:
    st.subheader("Shared listing content")
    st.caption("Saving here keeps the customer-facing title and description synchronized.")
    product = _object_view(package, "product")
    input_data = _object_view(package, "input")
    ebay = _platform_view(package, "ebay")
    shopify = _platform_view(package, "shopify")

    with st.form("shared_listing_form"):
        title = st.text_input("Title", value=str(ebay.get("title") or shopify.get("title") or ""))
        description = st.text_area(
            "Description",
            value=str(ebay.get("description") or shopify.get("description") or ""),
            height=220,
        )
        brand = st.text_input(
            "Brand", value=str(product.get("brand") or input_data.get("brand") or "")
        )
        upc = st.text_input("UPC", value=str(product.get("upc") or input_data.get("upc") or ""))
        mpn = st.text_input("MPN", value=str(product.get("mpn") or input_data.get("mpn") or ""))
        name = st.text_input("Product name", value=str(product.get("name") or ""))
        condition = st.text_input("Condition", value=str(product.get("condition") or ""))
        condition_details = st.text_area(
            "Condition details",
            value=str(product.get("condition_details") or ""),
        )
        submitted = st.form_submit_button(
            "Save shared edits",
            type="primary",
            icon=":material/save:",
        )
    if submitted:
        product = _ensure_object(package, "product")
        input_data = _ensure_object(package, "input")
        ebay = _ensure_platform(package, "ebay")
        shopify = _ensure_platform(package, "shopify")
        ebay_specifics = _ensure_object(ebay, "item_specifics")
        brand_value = brand.strip()
        upc_value = _optional_text(upc)
        mpn_value = _optional_text(mpn)
        ebay["title"] = title.strip()
        shopify["title"] = title.strip()
        ebay["description"] = description.strip()
        shopify["description"] = description.strip()
        input_data["brand"] = brand_value
        product["brand"] = brand_value
        ebay_specifics["Brand"] = brand_value
        shopify["vendor"] = brand_value
        input_data["upc"] = upc_value
        product["upc"] = upc_value
        ebay_specifics["UPC"] = upc_value
        input_data["mpn"] = mpn_value
        product["mpn"] = mpn_value
        ebay_specifics["MPN"] = mpn_value
        shopify["mpn_metafield_candidate"] = mpn_value
        product["name"] = name.strip()
        product["condition"] = condition.strip()
        product["condition_details"] = condition_details.strip()
        _set_flash("Shared listing edits saved.")
        st.rerun()


def _render_msrp_editor(package: dict[str, Any]) -> None:
    product = _object_view(package, "product")
    current = product.get("msrp")
    msrp = current if isinstance(current, dict) else default_msrp()
    basis_options = ["UNKNOWN", "CURRENT", "HISTORICAL", "APPROXIMATE"]
    confidence_options = ["INSUFFICIENT", "EXACT", "PROXY"]
    source_ids = msrp.get("source_ids")
    source_text = (
        ", ".join(str(item) for item in source_ids if isinstance(item, str))
        if isinstance(source_ids, list)
        else ""
    )

    with st.container(border=True):
        st.markdown("#### MSRP evidence")
        st.caption(
            "MSRP is an internal pricing reference. Keep it UNKNOWN unless the exact item has support."
        )
        with st.form("msrp_evidence_form"):
            amount = st.text_input(
                "MSRP amount (USD)",
                value=format_cents(msrp.get("amount_cents")),
                placeholder="59.99",
                help="Use the manufacturer's suggested price, not an eBay seller's claimed value.",
            )
            basis = st.selectbox(
                "MSRP basis",
                basis_options,
                index=_select_index(basis_options, msrp.get("basis")),
                format_func=lambda value: {
                    "UNKNOWN": "Unknown",
                    "CURRENT": "Current MSRP",
                    "HISTORICAL": "Historical MSRP",
                    "APPROXIMATE": "Approximate style or family reference",
                }[value],
            )
            confidence = st.selectbox(
                "MSRP evidence confidence",
                confidence_options,
                index=_select_index(confidence_options, msrp.get("confidence")),
                format_func=lambda value: {
                    "INSUFFICIENT": "Insufficient",
                    "EXACT": "Exact product evidence",
                    "PROXY": "Related product or style evidence",
                }[value],
            )
            sources = st.text_input(
                "MSRP source IDs",
                value=source_text,
                help="Comma-separated IDs from the package's Sources list, such as S007.",
            )
            observed_on = st.text_input(
                "MSRP observed date",
                value=str(msrp.get("observed_on") or ""),
                placeholder="YYYY-MM-DD",
            )
            note = st.text_area(
                "MSRP note",
                value=str(msrp.get("note") or ""),
                help="Record why the evidence is exact, historical, approximate, or unresolved.",
            )
            operator_reviewed = st.checkbox(
                "I reviewed this MSRP evidence",
                value=msrp.get("operator_reviewed") is True,
            )
            submitted = st.form_submit_button(
                "Save MSRP evidence",
                type="primary",
                icon=":material/price_check:",
            )
    if submitted:
        try:
            amount_cents = parse_money_to_cents(amount, label="MSRP amount")
            observed_date = _optional_iso_date(observed_on, label="MSRP observed date")
        except PricingInputError as error:
            st.error(f"Could not save MSRP ({error.code}): {error}")
        else:
            product = _ensure_object(package, "product")
            saved = product.get("msrp")
            if not isinstance(saved, dict):
                saved = default_msrp()
                product["msrp"] = saved
            else:
                _merge_defaults(saved, default_msrp())
            saved["amount_cents"] = amount_cents
            saved["currency"] = "USD"
            saved["basis"] = basis
            saved["confidence"] = confidence
            saved["source_ids"] = _source_ids(sources)
            saved["observed_on"] = observed_date
            saved["operator_reviewed"] = operator_reviewed
            saved["note"] = _optional_text(note)
            pricing = package.get("pricing")
            if isinstance(pricing, dict):
                _mark_pricing_needs_review(pricing)
                _recompute_pricing_analysis(package)
            _set_flash("MSRP evidence saved.")
            st.rerun()


def _render_ebay_editor(package: dict[str, Any]) -> None:
    ebay = _platform_view(package, "ebay")
    specifics = ebay.get("item_specifics")
    if not isinstance(specifics, dict):
        specifics = {}

    st.subheader("eBay listing")
    with st.form("ebay_listing_form"):
        category = st.text_input(
            "Category suggestion",
            value=str(ebay.get("category_suggestion") or ""),
        )
        st.markdown("#### Item specifics")
        edited = st.data_editor(
            mapping_to_rows(specifics),
            key="ebay_specifics_editor",
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            column_config={
                "Attribute": st.column_config.TextColumn("Attribute", required=True),
                "Value": st.column_config.TextColumn("Value", help="Leave blank for JSON null."),
            },
        )
        submitted = st.form_submit_button(
            "Save eBay edits",
            type="primary",
            icon=":material/save:",
        )
    if submitted:
        try:
            updated_specifics = rows_to_mapping(_records(edited))
        except ListingPackageError as error:
            st.error(f"Could not save ({error.code}): {error}")
        else:
            ebay = _ensure_platform(package, "ebay")
            ebay["item_specifics"] = updated_specifics
            ebay["category_suggestion"] = _optional_text(category)
            _set_flash("eBay edits saved.")
            st.rerun()

    st.markdown("#### Copy-ready eBay fields")
    st.caption("Each box has its own copy control in the upper-right corner.")
    title_column, category_column = st.columns(2)
    with title_column:
        _copy_field("Title", ebay.get("title"))
    with category_column:
        _copy_field("Category suggestion", ebay.get("category_suggestion"))
    _copy_field("Description", ebay.get("description"), height=260)
    _copy_field("Item specifics", _mapping_copy_text(specifics), height=360)
    _render_complete_reference(package, "ebay")


def _render_shopify_editor(package: dict[str, Any]) -> None:
    shopify = _platform_view(package, "shopify")
    attributes = shopify.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    tags = shopify.get("tags")
    tag_text = ", ".join(str(tag) for tag in tags) if isinstance(tags, list) else ""

    st.subheader("Shopify listing")
    with st.form("shopify_listing_form"):
        vendor = st.text_input("Vendor", value=str(shopify.get("vendor") or ""))
        category = st.text_input(
            "Category suggestion",
            value=str(shopify.get("category_suggestion") or ""),
        )
        product_type = st.text_input(
            "Product type",
            value=str(shopify.get("product_type") or ""),
        )
        tags_value = st.text_area(
            "Tags — separated by commas",
            value=tag_text,
            height=120,
            help="Shopify recognizes each comma-separated value as a separate tag.",
        )
        seo_title = st.text_input(
            "SEO title",
            value=str(shopify.get("seo_title") or ""),
            help="Keep it natural and descriptive. Around 45–60 characters is ideal; up to 70 is acceptable.",
        )
        seo_description = st.text_area(
            "SEO description",
            value=str(shopify.get("seo_description") or ""),
            help="Aim for a useful, customer-focused summary of about 150–160 characters.",
        )
        url_handle = st.text_input(
            "URL handle",
            value=str(shopify.get("url_handle") or ""),
            help="Use concise lowercase words separated by hyphens. Leave out UPC and MPN.",
        )
        st.markdown("#### Shopify attributes")
        edited = st.data_editor(
            mapping_to_rows(attributes),
            key="shopify_attributes_editor",
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            column_config={
                "Attribute": st.column_config.TextColumn("Attribute", required=True),
                "Value": st.column_config.TextColumn("Value", help="Leave blank for JSON null."),
            },
        )
        submitted = st.form_submit_button(
            "Save Shopify edits",
            type="primary",
            icon=":material/save:",
        )
    if submitted:
        try:
            updated_attributes = rows_to_mapping(_records(edited))
        except ListingPackageError as error:
            st.error(f"Could not save ({error.code}): {error}")
        else:
            shopify = _ensure_platform(package, "shopify")
            shopify["attributes"] = updated_attributes
            shopify["vendor"] = vendor.strip()
            shopify["category_suggestion"] = _optional_text(category)
            shopify["product_type"] = product_type.strip()
            shopify["tags"] = parse_shopify_tags(tags_value)
            shopify["seo_title"] = seo_title.strip()
            shopify["seo_description"] = seo_description.strip()
            shopify["url_handle"] = url_handle.strip()
            _set_flash("Shopify edits saved.")
            st.rerun()

    st.markdown("#### Copy-ready Shopify fields")
    st.caption("Each box has its own copy control in the upper-right corner.")
    title_column, category_column = st.columns(2)
    with title_column:
        _copy_field("Title", shopify.get("title"))
    with category_column:
        _copy_field("Category suggestion", shopify.get("category_suggestion"))
    _copy_field("Description", shopify.get("description"), height=260)

    vendor_column, product_type_column = st.columns(2)
    with vendor_column:
        _copy_field("Vendor", shopify.get("vendor"))
    with product_type_column:
        _copy_field("Product type", shopify.get("product_type"))

    barcode = _object_view(package, "product").get("upc")
    if barcode is None:
        barcode = _object_view(package, "input").get("upc")
    barcode_column, mpn_column = st.columns(2)
    with barcode_column:
        _copy_field("Barcode (UPC)", barcode)
    with mpn_column:
        _copy_field("MPN metafield", shopify.get("mpn_metafield_candidate"))

    _copy_field("Attributes", _mapping_copy_text(attributes), height=300)
    _copy_field("Tags", tag_text, height=120)
    seo_title_column, handle_column = st.columns(2)
    with seo_title_column:
        _copy_field("SEO title", shopify.get("seo_title"))
    with handle_column:
        _copy_field("URL handle", shopify.get("url_handle"))
    _copy_field("SEO description", shopify.get("seo_description"), height=180)
    _render_complete_reference(package, "shopify")


def _comp_column_config() -> dict[str, Any]:
    return {
        "Comp ID": st.column_config.TextColumn(
            "Comp ID",
            help="Leave blank on a new row and the app will assign one when saved.",
        ),
        "Include": st.column_config.CheckboxColumn(
            "Include",
            help="Only checked exact or near-exact fixed-price comps can enter statistics.",
        ),
        "Match tier": st.column_config.SelectboxColumn(
            "Match tier",
            options=("EXACT", "NEAR_EXACT", "FALLBACK", "CONTEXT_ONLY"),
        ),
        "Format": st.column_config.SelectboxColumn(
            "Format",
            options=("FIXED_PRICE", "AUCTION", "UNKNOWN"),
        ),
        "Item price ($)": st.column_config.TextColumn(
            "Item price ($)",
            help="Use the realized sold price or current asking item price.",
        ),
        "Shipping ($)": st.column_config.TextColumn(
            "Shipping ($)",
            help="Enter 0 for free shipping. A blank amount is unknown and cannot enter statistics.",
        ),
        "Quantity": st.column_config.NumberColumn(
            "Quantity",
            help="An aggregate multi-quantity row remains one price observation.",
            min_value=1,
            step=1,
            format="%d",
        ),
        "URL": st.column_config.LinkColumn("URL", display_text="Open"),
    }


def _render_pricing_summary(package: dict[str, Any]) -> None:
    product = _object_view(package, "product")
    msrp = product.get("msrp") if isinstance(product.get("msrp"), dict) else {}
    pricing = _pricing_for_display(package)
    ebay = _object_view(_object_view(pricing, "channels"), "ebay")
    analysis = build_analysis_snapshot(package)
    scenarios = analysis.get("scenarios")
    first_scenario = scenarios[0] if isinstance(scenarios, list) and scenarios else {}

    with st.container(horizontal=True):
        st.metric("MSRP", _money_label(msrp.get("amount_cents")), border=True)
        st.metric(
            "Condition MSRP target",
            _money_label(analysis.get("msrp_target_cents")),
            border=True,
            help="NWT 25%, NWOT 20%, Used 10%. This is guidance, not a floor.",
        )
        st.metric(
            "Market recommendation",
            _money_label(analysis.get("recommended_initial_item_price_cents")),
            border=True,
        )
        st.metric(
            "Saved initial price",
            _money_label(ebay.get("operator_initial_price_cents")),
            border=True,
        )
        st.metric(
            "Initial margin",
            _percentage_label(first_scenario.get("margin_bps")),
            border=True,
        )
        st.metric(
            "Initial ROI",
            _percentage_label(first_scenario.get("roi_bps")),
            border=True,
        )

    recommendation = analysis.get("recommendation")
    operator_status = analysis.get("operator_initial_price_status")
    if operator_status == "BELOW_LISTING_MINIMUM":
        st.warning(
            "LIVE SELL — the saved initial item price is below $10 before shipping.",
            icon=":material/point_of_sale:",
        )
    elif recommendation == "LIST":
        st.success(
            "LIST — the market-supported initial item price passes the $10 listing rule.",
            icon=":material/check_circle:",
        )
    elif recommendation == "LIVE_SELL":
        st.warning(
            "LIVE SELL — the market-supported initial item price is below $10 before shipping.",
            icon=":material/point_of_sale:",
        )
    else:
        st.info(
            "MANUAL REVIEW — more qualified sold data or a complete assumption is needed.",
            icon=":material/fact_check:",
        )
    st.caption(
        "The $10 rule applies only to the initial item price. MSRP targets, margin above 30%, "
        "ROI above 100%, and 10%/15%/20% offer scenarios are decision guidance—not protected floors."
    )
    st.caption(
        f"Qualified sold comps: {analysis.get('qualified_sold_count', 0)} · "
        f"sample strength: {str(analysis.get('sample_strength', 'NONE')).replace('_', ' ').title()} · "
        f"qualified active comps: {analysis.get('qualified_active_count', 0)}"
    )
    warnings = analysis.get("warnings")
    if isinstance(warnings, list) and warnings:
        with st.expander(f"Pricing warnings ({len(warnings)})"):
            for warning in warnings:
                st.warning(str(warning))


def _render_research_plan(package: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown("#### Manual Product Research search plan")
        st.caption(
            "Run these from narrowest to broadest inside eBay Product Research. "
            "The local app does not open, scrape, or sign in to eBay."
        )
        queries = build_research_queries(package)
        if not queries:
            st.info("Add a Brand and product identity before building the search plan.")
        for label, query in queries:
            _copy_field(label, query, height=70)


def _render_research_editor(package: dict[str, Any]) -> None:
    pricing = _pricing_for_display(package)
    research = _object_view(pricing, "research")
    sold_comps = research.get("sold_comps")
    active_comps = research.get("active_comps")
    demand_options = [
        "UNSPECIFIED",
        "WEAK_HIGH_COMPETITION",
        "BALANCED",
        "STRONG_LOW_COMPETITION",
    ]
    demand_labels = {
        "UNSPECIFIED": "Not decided",
        "WEAK_HIGH_COMPETITION": "Weak demand / high competition — P20",
        "BALANCED": "Balanced market — P25",
        "STRONG_LOW_COMPETITION": "Strong demand / low competition — P37.5",
    }

    with st.container(border=True):
        st.markdown("#### Product Research evidence")
        st.caption(
            "Compare item price plus shipping. Keep sold and active results separate; "
            "auctions and weak matches stay visible but do not set the default price."
        )
        with st.form("pricing_research_form"):
            demand_position = st.selectbox(
                "Demand and competition position",
                demand_options,
                index=_select_index(demand_options, research.get("demand_position")),
                format_func=lambda value: demand_labels[value],
                help="This is an explicit operator decision; the app does not invent demand thresholds.",
            )
            metadata_row = st.columns(3)
            with metadata_row[0]:
                window_days = st.text_input(
                    "Research window (days)",
                    value=str(research.get("window_days") or ""),
                )
                observed_on = st.text_input(
                    "Research observed date",
                    value=str(research.get("observed_on") or ""),
                    placeholder="YYYY-MM-DD",
                )
            with metadata_row[1]:
                sell_through = st.text_input(
                    "eBay sell-through rate (%)",
                    value=format_bps(research.get("sell_through_rate_bps")),
                )
                sold_count = st.text_input(
                    "eBay reported sold count",
                    value=str(research.get("sold_count") or ""),
                )
            with metadata_row[2]:
                active_count = st.text_input(
                    "eBay reported active count",
                    value=str(research.get("active_count") or ""),
                )
                operator_notes = st.text_area(
                    "Research notes",
                    value=str(research.get("operator_notes") or ""),
                    height=100,
                )

            st.markdown("##### Sold results")
            sold_edited = st.data_editor(
                comparables_to_rows(sold_comps),
                key="sold_comps_editor",
                num_rows="dynamic",
                hide_index=True,
                width="stretch",
                column_config=_comp_column_config(),
            )
            st.markdown("##### Active competition")
            active_edited = st.data_editor(
                comparables_to_rows(active_comps),
                key="active_comps_editor",
                num_rows="dynamic",
                hide_index=True,
                width="stretch",
                column_config=_comp_column_config(),
            )
            submitted = st.form_submit_button(
                "Save Product Research",
                type="primary",
                icon=":material/query_stats:",
            )
    if submitted:
        try:
            saved_window = _optional_whole_number(window_days, label="Research window")
            saved_date = _optional_iso_date(observed_on, label="Research observed date")
            saved_sell_through = parse_percent_to_bps(
                sell_through,
                label="Sell-through rate",
            )
            saved_sold_count = _optional_whole_number(sold_count, label="Sold count")
            saved_active_count = _optional_whole_number(active_count, label="Active count")
            merged_sold = merge_comparable_rows(
                sold_comps,
                _records(sold_edited),
                prefix="SOLD",
            )
            merged_active = merge_comparable_rows(
                active_comps,
                _records(active_edited),
                prefix="ACTIVE",
            )
        except PricingInputError as error:
            st.error(f"Could not save Product Research ({error.code}): {error}")
        else:
            saved_pricing = _ensure_pricing(package)
            saved_research = _ensure_object(saved_pricing, "research")
            saved_research["source"] = "EBAY_PRODUCT_RESEARCH"
            saved_research["window_days"] = saved_window
            saved_research["observed_on"] = saved_date
            saved_research["sell_through_rate_bps"] = saved_sell_through
            saved_research["sold_count"] = saved_sold_count
            saved_research["active_count"] = saved_active_count
            saved_research["demand_position"] = demand_position
            saved_research["sold_comps"] = merged_sold
            saved_research["active_comps"] = merged_active
            saved_research["operator_notes"] = _optional_text(operator_notes)
            _mark_pricing_needs_review(saved_pricing)
            _recompute_pricing_analysis(package)
            _clear_editor_state()
            _set_flash("Product Research evidence saved.")
            st.rerun()


def _render_pricing_assumptions(package: dict[str, Any]) -> None:
    pricing = _pricing_for_display(package)
    ebay = _object_view(_object_view(pricing, "channels"), "ebay")
    tier_options = ["UNSPECIFIED", "NWT", "NWOT", "USED"]
    tier_labels = {
        "UNSPECIFIED": "Not classified",
        "NWT": "New with tags — 25% MSRP target",
        "NWOT": "New without tags — 20% MSRP target",
        "USED": "Used — 10% MSRP target",
    }
    shipping_options = ["UNSPECIFIED", "FREE", "BUYER_PAID"]
    shipping_labels = {
        "UNSPECIFIED": "Not decided",
        "FREE": "Free shipping",
        "BUYER_PAID": "Buyer-paid shipping",
    }
    ending_options = ["END_99", "EXACT_CENTS"]

    with st.container(border=True):
        st.markdown("#### Listing assumptions and costs")
        st.caption(
            "Enter zero when a cost truly does not apply. Leave it blank when unknown; "
            "the app will show profitability as UNKNOWN."
        )
        with st.form("pricing_assumptions_form"):
            condition_tier = st.selectbox(
                "Condition pricing tier",
                tier_options,
                index=_select_index(tier_options, pricing.get("condition_tier")),
                format_func=lambda value: tier_labels[value],
            )
            shipping_mode = st.selectbox(
                "Shipping method",
                shipping_options,
                index=_select_index(shipping_options, ebay.get("shipping_mode")),
                format_func=lambda value: shipping_labels[value],
            )
            price_ending = st.selectbox(
                "Price ending",
                ending_options,
                index=_select_index(ending_options, ebay.get("price_ending_policy")),
                format_func=lambda value: {
                    "END_99": "End in .99 without exceeding the market ceiling",
                    "EXACT_CENTS": "Use the exact calculated cents",
                }[value],
            )
            first_row = st.columns(3)
            with first_row[0]:
                initial_price = st.text_input(
                    "Initial item price (USD)",
                    value=format_cents(ebay.get("operator_initial_price_cents")),
                    help="Must be at least $10 before shipping to pass the listing rule.",
                )
                acquisition_cost = st.text_input(
                    "Acquisition cost (USD)",
                    value=format_cents(ebay.get("acquisition_cost_cents")),
                    help="ROI is net profit divided by this inventory cost.",
                )
                packaging_cost = st.text_input(
                    "Packaging cost (USD)",
                    value=format_cents(ebay.get("packaging_cost_cents")),
                )
            with first_row[1]:
                buyer_shipping = st.text_input(
                    "Buyer shipping charge (USD)",
                    value=format_cents(ebay.get("buyer_shipping_charge_cents")),
                    help="Enter 0 for free shipping. This does not help satisfy the $10 rule.",
                )
                seller_shipping = st.text_input(
                    "Estimated seller shipping cost (USD)",
                    value=format_cents(ebay.get("seller_shipping_cost_cents")),
                )
                other_costs = st.text_input(
                    "Other selling costs (USD)",
                    value=format_cents(ebay.get("other_costs_cents")),
                )
            with first_row[2]:
                marketplace_fee = st.text_input(
                    "Marketplace fee (%)",
                    value=format_bps(ebay.get("marketplace_fee_bps")),
                    help="Use the effective category and Store rate for this item.",
                )
                promotion_fee = st.text_input(
                    "Promotion fee (%)",
                    value=format_bps(ebay.get("promotion_fee_bps")),
                )
                fixed_order_fee = st.text_input(
                    "Fixed order fee (USD)",
                    value=format_cents(ebay.get("fixed_order_fee_cents")),
                )
            submitted = st.form_submit_button(
                "Save pricing assumptions",
                type="primary",
                icon=":material/calculate:",
            )
    if submitted:
        try:
            saved_initial = parse_money_to_cents(initial_price, label="Initial item price")
            saved_buyer_shipping = parse_money_to_cents(
                buyer_shipping,
                label="Buyer shipping charge",
            )
            saved_seller_shipping = parse_money_to_cents(
                seller_shipping,
                label="Seller shipping cost",
            )
            saved_acquisition = parse_money_to_cents(
                acquisition_cost,
                label="Acquisition cost",
            )
            saved_packaging = parse_money_to_cents(packaging_cost, label="Packaging cost")
            saved_other = parse_money_to_cents(other_costs, label="Other selling costs")
            saved_marketplace_fee = parse_percent_to_bps(
                marketplace_fee,
                label="Marketplace fee",
            )
            saved_promotion_fee = parse_percent_to_bps(
                promotion_fee,
                label="Promotion fee",
            )
            saved_fixed_fee = parse_money_to_cents(fixed_order_fee, label="Fixed order fee")
            if shipping_mode == "FREE":
                if saved_buyer_shipping not in {None, 0}:
                    raise PricingInputError(
                        "FREE_SHIPPING_CHARGE",
                        "Free shipping cannot have a nonzero buyer shipping charge.",
                    )
                saved_buyer_shipping = 0
            elif shipping_mode == "BUYER_PAID" and (
                saved_buyer_shipping is None or saved_buyer_shipping <= 0
            ):
                raise PricingInputError(
                    "BUYER_PAID_SHIPPING_CHARGE",
                    "Buyer-paid shipping needs a positive buyer shipping charge.",
                )
            elif shipping_mode == "UNSPECIFIED" and saved_buyer_shipping is not None:
                raise PricingInputError(
                    "UNSPECIFIED_SHIPPING_CHARGE",
                    "Choose a shipping method before entering a buyer shipping charge.",
                )
        except PricingInputError as error:
            st.error(f"Could not save pricing assumptions ({error.code}): {error}")
        else:
            saved_pricing = _ensure_pricing(package)
            saved_pricing["condition_tier"] = condition_tier
            channels = _ensure_object(saved_pricing, "channels")
            saved_ebay = _ensure_object(channels, "ebay")
            saved_ebay["shipping_mode"] = shipping_mode
            saved_ebay["buyer_shipping_charge_cents"] = saved_buyer_shipping
            saved_ebay["seller_shipping_cost_cents"] = saved_seller_shipping
            saved_ebay["acquisition_cost_cents"] = saved_acquisition
            saved_ebay["packaging_cost_cents"] = saved_packaging
            saved_ebay["other_costs_cents"] = saved_other
            saved_ebay["marketplace_fee_bps"] = saved_marketplace_fee
            saved_ebay["promotion_fee_bps"] = saved_promotion_fee
            saved_ebay["fixed_order_fee_cents"] = saved_fixed_fee
            saved_ebay["price_ending_policy"] = price_ending
            saved_ebay["operator_initial_price_cents"] = saved_initial
            _mark_pricing_needs_review(saved_pricing)
            _recompute_pricing_analysis(package)
            _set_flash("Pricing assumptions saved and results recalculated.")
            st.rerun()


def _render_pricing_results(package: dict[str, Any]) -> None:
    analysis = build_analysis_snapshot(package)
    scenarios = analysis.get("scenarios")
    with st.container(border=True):
        st.markdown("#### List and offer scenarios")
        st.caption(
            "Fees apply to item price plus buyer shipping. Offers reduce the item price only; "
            "shipping stays unchanged."
        )
        if not isinstance(scenarios, list) or not scenarios:
            st.info("Save an initial or market-supported price to calculate scenarios.")
        else:
            rows = []
            for scenario in scenarios:
                if not isinstance(scenario, dict):
                    continue
                discount = scenario.get("discount_bps")
                rows.append(
                    {
                        "Scenario": "List price"
                        if discount == 0
                        else f"{_percentage_label(discount)} off",
                        "Item price": _money_label(scenario.get("item_price_cents")),
                        "Buyer shipping": _money_label(scenario.get("buyer_shipping_charge_cents")),
                        "Buyer total": _money_label(scenario.get("buyer_total_cents")),
                        "Marketplace fee": _money_label(scenario.get("marketplace_fee_cents")),
                        "Promotion fee": _money_label(scenario.get("promotion_fee_cents")),
                        "Net profit": _money_label(scenario.get("net_profit_cents")),
                        "Margin": _percentage_label(scenario.get("margin_bps")),
                        "ROI": _percentage_label(scenario.get("roi_bps")),
                        "Health": str(scenario.get("profitability_status", "UNKNOWN"))
                        .replace("_", " ")
                        .title(),
                    }
                )
            st.dataframe(rows, hide_index=True, width="stretch")

        with st.expander("Market calculation details"):
            details = [
                {
                    "Statistic": "Sold P20",
                    "Delivered price": _money_label(analysis.get("sold_p20_cents")),
                },
                {
                    "Statistic": "Sold P25",
                    "Delivered price": _money_label(analysis.get("sold_p25_cents")),
                },
                {
                    "Statistic": "Sold P37.5",
                    "Delivered price": _money_label(analysis.get("sold_p375_cents")),
                },
                {
                    "Statistic": "Sold median",
                    "Delivered price": _money_label(analysis.get("sold_p50_cents")),
                },
                {
                    "Statistic": "Credible active P25",
                    "Delivered price": _money_label(analysis.get("active_p25_cents")),
                },
                {
                    "Statistic": "Market delivered ceiling",
                    "Delivered price": _money_label(analysis.get("market_delivered_ceiling_cents")),
                },
            ]
            st.dataframe(details, hide_index=True, width="stretch")


def _render_pricing_review(package: dict[str, Any]) -> None:
    pricing = _pricing_for_display(package)
    review = _object_view(pricing, "review")
    disposition_options = [
        "UNDECIDED",
        "LIST",
        "LIVE_SELL",
        "HOLD_FOR_SEASON",
        "MANUAL_REVIEW",
    ]
    current_disposition = review.get("operator_disposition") or "UNDECIDED"
    with st.container(border=True):
        st.markdown("#### Operator decision")
        st.caption("The application recommends; you make and record the final decision.")
        with st.form("pricing_review_form"):
            disposition = st.selectbox(
                "Pricing disposition",
                disposition_options,
                index=_select_index(disposition_options, current_disposition),
                format_func=lambda value: value.replace("_", " ").title(),
            )
            note = st.text_area(
                "Pricing decision note",
                value=str(review.get("note") or ""),
            )
            reviewed = st.checkbox(
                "I reviewed the MSRP, market evidence, costs, and offer scenarios",
                value=review.get("status") == "REVIEWED",
            )
            submitted = st.form_submit_button(
                "Save pricing decision",
                type="primary",
                icon=":material/fact_check:",
            )
    if submitted:
        saved_disposition = None if disposition == "UNDECIDED" else disposition
        ebay = _object_view(_object_view(pricing, "channels"), "ebay")
        operator_price = ebay.get("operator_initial_price_cents")
        if reviewed and saved_disposition is None:
            st.error("Choose a pricing disposition before marking the decision reviewed.")
        elif saved_disposition == "LIST" and (
            isinstance(operator_price, bool)
            or not isinstance(operator_price, int)
            or operator_price < MIN_INITIAL_ITEM_PRICE_CENTS
        ):
            st.error("LIST requires a saved initial item price of at least $10 before shipping.")
        else:
            saved_pricing = _ensure_pricing(package)
            saved_review = _ensure_object(saved_pricing, "review")
            saved_review["status"] = "REVIEWED" if reviewed else "NEEDS_REVIEW"
            saved_review["operator_disposition"] = saved_disposition
            saved_review["note"] = _optional_text(note)
            _recompute_pricing_analysis(package)
            _set_flash("Pricing decision saved.")
            st.rerun()


def _render_pricing_editor(package: dict[str, Any]) -> None:
    st.subheader("Pricing")
    st.caption(
        "Local, operator-controlled eBay pricing · no marketplace connection · no automatic listing"
    )
    _render_pricing_summary(package)
    _render_research_plan(package)
    _render_research_editor(package)
    _render_pricing_assumptions(package)
    _render_pricing_results(package)
    _render_pricing_review(package)


def _render_review(package: dict[str, Any]) -> None:
    st.subheader("What to review")
    st.caption("A plain-English summary of what the GPT completed and what you may want to check.")
    review = package.get("review")
    if isinstance(review, dict):
        st.info(
            plain_review_status(review.get("status")),
            icon=":material/fact_check:",
        )
        if review.get("manual_review_required") is True:
            st.write("You should still review the listing before publishing it.")
        warnings = review.get("warnings")
        missing = review.get("missing_information")
        if isinstance(warnings, list) and warnings:
            st.markdown(f"#### Things to double-check ({len(warnings)})")
            for warning in warnings:
                st.warning(plain_review_note(warning))
        if isinstance(missing, list) and missing:
            st.markdown(f"#### Information that may still be needed ({len(missing)})")
            for item in missing:
                st.error(plain_review_note(item))
    images = package.get("images")
    if isinstance(images, list):
        with st.expander(f"Images included ({len(images)})"):
            st.dataframe(images, hide_index=True, width="stretch")
    sources = package.get("sources")
    if isinstance(sources, list):
        with st.expander(f"Where the product information came from ({len(sources)})"):
            st.dataframe(sources, hide_index=True, width="stretch")


def _render_advanced_editor(package: dict[str, Any]) -> None:
    st.subheader("Full listing data")
    st.warning(
        "This is an advanced tool. Applying this box replaces the entire working copy of the listing."
    )
    with st.form("advanced_json_form"):
        raw_json = st.text_area(
            "Complete listing JSON",
            value=dump_listing_package(package),
            height=520,
            key="advanced_json_editor",
        )
        submitted = st.form_submit_button(
            "Apply complete JSON",
            icon=":material/data_object:",
        )
    if submitted:
        try:
            updated = load_listing_package(raw_json.encode("utf-8"))
        except ListingPackageError as error:
            st.error(f"JSON not applied ({error.code}): {error}")
        else:
            st.session_state[_PACKAGE_KEY] = updated
            _clear_editor_state()
            _set_flash("Complete JSON applied.")
            st.rerun()


st.set_page_config(
    page_title="GoodeDeals listing workspace",
    page_icon=":material/inventory_2:",
    layout="wide",
)


@st.dialog("Listing check", width="large", icon=":material/fact_check:")
def _show_listing_check() -> None:
    _render_review(_package())


@st.dialog("Full JSON editor", width="large", icon=":material/data_object:")
def _show_full_json_editor() -> None:
    _render_advanced_editor(_package())


st.title("GoodeDeals listing workspace")
st.caption("Local JSON review and copy workspace · no marketplace connection · nothing publishes")

flash_message = st.session_state.pop(_FLASH_KEY, None)
if isinstance(flash_message, str):
    st.success(flash_message, icon=":material/check_circle:")

uploaded = st.file_uploader(
    "Choose a GoodeDeals listing JSON file",
    type=("json",),
    accept_multiple_files=False,
    max_upload_size=5,
    help="The file is processed in memory and is not uploaded to eBay or Shopify.",
)

if uploaded is None:
    for state_key in (
        _PACKAGE_KEY,
        _ORIGINAL_KEY,
        _DIGEST_KEY,
        _FILENAME_KEY,
        _DOWNLOAD_FILENAME_KEY,
    ):
        st.session_state.pop(state_key, None)
    _clear_editor_state()
    st.info(
        "Choose the JSON produced by the GoodeDeals Listing Builder to begin.",
        icon=":material/upload_file:",
    )
    st.stop()

try:
    _load_upload(uploaded)
except ListingPackageError as error:
    st.error(f"File rejected ({error.code}): {error}")
    st.stop()

package = _package()
st.success(f"Loaded `{st.session_state[_FILENAME_KEY]}` for this browser session.")
_render_validation(package)

with st.container(horizontal=True):
    if st.button("Reset uploaded file", icon=":material/restart_alt:"):
        st.session_state[_PACKAGE_KEY] = copy.deepcopy(st.session_state[_ORIGINAL_KEY])
        _clear_editor_state()
        _set_flash("The original uploaded file has been restored.")
        st.rerun()
    st.download_button(
        "Download revised JSON",
        data=dump_listing_package(package).encode("utf-8"),
        file_name=st.session_state[_DOWNLOAD_FILENAME_KEY],
        mime="application/json",
        type="primary",
        icon=":material/download:",
    )
    with st.popover("More tools", icon=":material/more_horiz:"):
        st.caption("Optional details and advanced controls.")
        if st.button("Listing check", icon=":material/fact_check:"):
            _show_listing_check()
        if st.button("Full JSON editor", icon=":material/data_object:"):
            _show_full_json_editor()

shared_tab, ebay_tab, shopify_tab, pricing_tab = st.tabs(
    ("Shared data", "eBay", "Shopify", "Pricing")
)
with shared_tab:
    _render_shared_editor(package)
    _render_msrp_editor(package)
with ebay_tab:
    _render_ebay_editor(package)
with shopify_tab:
    _render_shopify_editor(package)
with pricing_tab:
    _render_pricing_editor(package)
