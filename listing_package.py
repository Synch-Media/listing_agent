"""Pure loading, validation, editing, and copy helpers for listing packages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, NoReturn

from pricing import MAX_MONEY_CENTS, MAX_OFFER_DISCOUNT_BPS, build_analysis_snapshot

MAX_PACKAGE_BYTES = 5 * 1024 * 1024


class ListingPackageError(ValueError):
    """A listing package could not be read safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class IssueLevel(StrEnum):
    """Severity of one local package-validation finding."""

    ERROR = "Error"
    WARNING = "Warning"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One readable validation result for the operator."""

    level: IssueLevel
    path: str
    message: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ListingPackageError(
                "DUPLICATE_JSON_KEY",
                f"The JSON contains a duplicate object key: {key}",
            )
        result[key] = value
    return result


def _reject_non_finite_number(value: str) -> NoReturn:
    raise ListingPackageError(
        "NON_FINITE_NUMBER",
        f"The JSON contains the unsupported numeric value {value}.",
    )


def load_listing_package(raw: bytes) -> dict[str, Any]:
    """Decode one UTF-8 JSON object while rejecting oversized or ambiguous input."""

    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if not raw:
        raise ListingPackageError("EMPTY_FILE", "The uploaded file is empty.")
    if len(raw) > MAX_PACKAGE_BYTES:
        raise ListingPackageError(
            "FILE_TOO_LARGE",
            "The uploaded JSON exceeds the 5 MB local safety limit.",
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ListingPackageError(
            "INVALID_ENCODING",
            "The uploaded file must be UTF-8 JSON.",
        ) from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_number,
        )
    except ListingPackageError:
        raise
    except json.JSONDecodeError as error:
        raise ListingPackageError(
            "INVALID_JSON",
            f"The uploaded file is not valid JSON near line {error.lineno}, column {error.colno}.",
        ) from error
    if not isinstance(value, dict):
        raise ListingPackageError(
            "ROOT_NOT_OBJECT",
            "The listing package must be one JSON object.",
        )
    return value


def _value_at(package: dict[str, Any], *path: str) -> Any:
    current: Any = package
    for segment in path:
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _issue(level: IssueLevel, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(level=level, path=path, message=message)


def _validate_attribute_map(
    value: Any,
    path: str,
    issues: list[ValidationIssue],
) -> None:
    if not isinstance(value, dict):
        issues.append(_issue(IssueLevel.ERROR, path, "Must be a JSON object."))
        return
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            issues.append(
                _issue(IssueLevel.ERROR, path, "Every attribute must have a nonblank name.")
            )
        if item is not None and not isinstance(item, str):
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    f"{path}.{key}",
                    "Attribute values must be text or null.",
                )
            )


def _validate_optional_nonnegative_int(
    value: Any,
    path: str,
    issues: list[ValidationIssue],
    *,
    positive: bool = False,
    maximum: int | None = None,
) -> None:
    if value is None:
        return
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if positive else "nonnegative"
        issues.append(
            _issue(IssueLevel.ERROR, path, f"Must be a {qualifier} whole number or null.")
        )
    elif maximum is not None and value > maximum:
        issues.append(
            _issue(
                IssueLevel.ERROR,
                path,
                f"Cannot exceed {maximum}.",
            )
        )


def _validate_enum(
    value: Any,
    path: str,
    allowed: set[str],
    issues: list[ValidationIssue],
) -> None:
    if value not in allowed:
        issues.append(
            _issue(
                IssueLevel.ERROR,
                path,
                f"Must be one of: {', '.join(sorted(allowed))}.",
            )
        )


def _validate_iso_date(value: Any, path: str, issues: list[ValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        issues.append(_issue(IssueLevel.ERROR, path, "Must be an ISO date (YYYY-MM-DD) or null."))
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        issues.append(_issue(IssueLevel.ERROR, path, "Must be an ISO date (YYYY-MM-DD) or null."))


def _validate_msrp(
    package: dict[str, Any], product: dict[str, Any], issues: list[ValidationIssue]
) -> None:
    if "msrp" not in product:
        return
    msrp = product.get("msrp")
    if not isinstance(msrp, dict):
        issues.append(_issue(IssueLevel.ERROR, "product.msrp", "Must be a JSON object."))
        return
    _validate_optional_nonnegative_int(
        msrp.get("amount_cents"),
        "product.msrp.amount_cents",
        issues,
        maximum=MAX_MONEY_CENTS,
    )
    if msrp.get("currency") != "USD":
        issues.append(_issue(IssueLevel.ERROR, "product.msrp.currency", "Expected USD."))
    _validate_enum(
        msrp.get("basis"),
        "product.msrp.basis",
        {"CURRENT", "HISTORICAL", "APPROXIMATE", "UNKNOWN"},
        issues,
    )
    _validate_enum(
        msrp.get("confidence"),
        "product.msrp.confidence",
        {"EXACT", "PROXY", "INSUFFICIENT"},
        issues,
    )
    source_ids = msrp.get("source_ids")
    if not isinstance(source_ids, list) or any(
        not isinstance(source_id, str) or not source_id.strip() for source_id in source_ids
    ):
        issues.append(
            _issue(
                IssueLevel.ERROR,
                "product.msrp.source_ids",
                "Must be an array of nonblank source IDs.",
            )
        )
    elif len(source_ids) != len(set(source_ids)):
        issues.append(
            _issue(
                IssueLevel.ERROR,
                "product.msrp.source_ids",
                "Source IDs must not repeat.",
            )
        )
    _validate_iso_date(msrp.get("observed_on"), "product.msrp.observed_on", issues)
    if not isinstance(msrp.get("operator_reviewed"), bool):
        issues.append(
            _issue(
                IssueLevel.ERROR,
                "product.msrp.operator_reviewed",
                "Must be true or false.",
            )
        )
    note = msrp.get("note")
    if note is not None and not isinstance(note, str):
        issues.append(_issue(IssueLevel.ERROR, "product.msrp.note", "Must be text or null."))
    if msrp.get("amount_cents") is not None:
        if not source_ids:
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    "product.msrp.source_ids",
                    "An MSRP amount requires at least one supporting source ID.",
                )
            )
        if msrp.get("basis") == "UNKNOWN" or msrp.get("confidence") == "INSUFFICIENT":
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    "product.msrp",
                    "An MSRP amount requires supported evidence rather than UNKNOWN or INSUFFICIENT.",
                )
            )
        available_source_ids = (
            {
                source.get("source_id")
                for source in package.get("sources", [])
                if isinstance(source, dict)
                and isinstance(source.get("source_id"), str)
                and source.get("source_id").strip()
            }
            if isinstance(package.get("sources"), list)
            else set()
        )
        if isinstance(source_ids, list):
            missing_ids = [
                source_id for source_id in source_ids if source_id not in available_source_ids
            ]
            if missing_ids:
                issues.append(
                    _issue(
                        IssueLevel.ERROR,
                        "product.msrp.source_ids",
                        "Every MSRP source ID must exist in the package Sources list.",
                    )
                )
        if msrp.get("operator_reviewed") is not True:
            issues.append(
                _issue(
                    IssueLevel.WARNING,
                    "product.msrp.operator_reviewed",
                    "Review the MSRP evidence before using its condition target.",
                )
            )


def _validate_comps(
    value: Any,
    path: str,
    issues: list[ValidationIssue],
    *,
    active: bool,
) -> None:
    if not isinstance(value, list):
        issues.append(_issue(IssueLevel.ERROR, path, "Must be a JSON array."))
        return
    seen_ids: set[str] = set()
    for index, comp in enumerate(value):
        comp_path = f"{path}[{index}]"
        if not isinstance(comp, dict):
            issues.append(_issue(IssueLevel.ERROR, comp_path, "Must be a JSON object."))
            continue
        comp_id = comp.get("comp_id")
        if not isinstance(comp_id, str) or not comp_id.strip():
            issues.append(
                _issue(IssueLevel.ERROR, f"{comp_path}.comp_id", "A nonblank ID is required.")
            )
        elif comp_id in seen_ids:
            issues.append(
                _issue(IssueLevel.ERROR, f"{comp_path}.comp_id", "Comparable IDs must be unique.")
            )
        else:
            seen_ids.add(comp_id)
        _validate_enum(
            comp.get("match_tier"),
            f"{comp_path}.match_tier",
            {"EXACT", "NEAR_EXACT", "FALLBACK", "CONTEXT_ONLY"},
            issues,
        )
        _validate_enum(
            comp.get("sale_format"),
            f"{comp_path}.sale_format",
            {"FIXED_PRICE", "AUCTION", "UNKNOWN"},
            issues,
        )
        _validate_optional_nonnegative_int(
            comp.get("item_price_cents"),
            f"{comp_path}.item_price_cents",
            issues,
            maximum=MAX_MONEY_CENTS,
        )
        _validate_optional_nonnegative_int(
            comp.get("shipping_cents"),
            f"{comp_path}.shipping_cents",
            issues,
            maximum=MAX_MONEY_CENTS,
        )
        _validate_optional_nonnegative_int(
            comp.get("quantity"), f"{comp_path}.quantity", issues, positive=True
        )
        if not isinstance(comp.get("included"), bool):
            issues.append(
                _issue(IssueLevel.ERROR, f"{comp_path}.included", "Must be true or false.")
            )
        event_date = comp.get("event_date")
        _validate_iso_date(event_date, f"{comp_path}.event_date", issues)
        if comp.get("included") is True and (
            comp.get("item_price_cents") is None or comp.get("shipping_cents") is None
        ):
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    comp_path,
                    "Included comps need separate item-price and shipping amounts to enter statistics.",
                )
            )
        if comp.get("included") is True:
            transaction_id = comp.get("transaction_id")
            item_id = comp.get("item_id")
            url = comp.get("url")
            has_transaction = isinstance(transaction_id, str) and bool(transaction_id.strip())
            has_item = isinstance(item_id, str) and bool(item_id.strip())
            has_url = isinstance(url, str) and bool(url.strip())
            has_valid_date = False
            if isinstance(event_date, str):
                try:
                    date.fromisoformat(event_date)
                except ValueError:
                    pass
                else:
                    has_valid_date = True
            if active and not (has_item or has_url):
                issues.append(
                    _issue(
                        IssueLevel.ERROR,
                        comp_path,
                        "An included active comp requires an item ID or URL.",
                    )
                )
            if not active and not (
                has_transaction
                or (
                    has_item
                    and has_valid_date
                    and isinstance(comp.get("quantity"), int)
                    and not isinstance(comp.get("quantity"), bool)
                    and comp["quantity"] > 0
                )
            ):
                issues.append(
                    _issue(
                        IssueLevel.ERROR,
                        comp_path,
                        "An included sold comp requires a transaction ID, or item ID plus valid sold date, price, shipping, and quantity.",
                    )
                )


def _validate_pricing(package: dict[str, Any], issues: list[ValidationIssue]) -> None:
    if "pricing" not in package:
        return
    pricing = package.get("pricing")
    if not isinstance(pricing, dict):
        issues.append(_issue(IssueLevel.ERROR, "pricing", "Must be a JSON object."))
        return
    if pricing.get("pricing_schema_version") != "1.0":
        issues.append(_issue(IssueLevel.ERROR, "pricing.pricing_schema_version", "Expected 1.0."))
    if pricing.get("currency") != "USD":
        issues.append(_issue(IssueLevel.ERROR, "pricing.currency", "Expected USD."))
    _validate_enum(
        pricing.get("condition_tier"),
        "pricing.condition_tier",
        {"UNSPECIFIED", "NWT", "NWOT", "USED"},
        issues,
    )

    research = pricing.get("research")
    if not isinstance(research, dict):
        issues.append(_issue(IssueLevel.ERROR, "pricing.research", "Required object is missing."))
    else:
        if research.get("source") != "EBAY_PRODUCT_RESEARCH":
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    "pricing.research.source",
                    "Expected EBAY_PRODUCT_RESEARCH.",
                )
            )
        _validate_optional_nonnegative_int(
            research.get("window_days"), "pricing.research.window_days", issues, positive=True
        )
        _validate_iso_date(research.get("observed_on"), "pricing.research.observed_on", issues)
        _validate_optional_nonnegative_int(
            research.get("sell_through_rate_bps"),
            "pricing.research.sell_through_rate_bps",
            issues,
        )
        if (
            isinstance(research.get("sell_through_rate_bps"), int)
            and not isinstance(research.get("sell_through_rate_bps"), bool)
            and research["sell_through_rate_bps"] > 10_000
        ):
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    "pricing.research.sell_through_rate_bps",
                    "Cannot exceed 100%.",
                )
            )
        for field in ("sold_count", "active_count"):
            _validate_optional_nonnegative_int(
                research.get(field), f"pricing.research.{field}", issues
            )
        _validate_enum(
            research.get("demand_position"),
            "pricing.research.demand_position",
            {"UNSPECIFIED", "WEAK_HIGH_COMPETITION", "BALANCED", "STRONG_LOW_COMPETITION"},
            issues,
        )
        _validate_comps(
            research.get("sold_comps"),
            "pricing.research.sold_comps",
            issues,
            active=False,
        )
        _validate_comps(
            research.get("active_comps"),
            "pricing.research.active_comps",
            issues,
            active=True,
        )

    channels = pricing.get("channels")
    ebay = channels.get("ebay") if isinstance(channels, dict) else None
    if not isinstance(ebay, dict):
        issues.append(
            _issue(IssueLevel.ERROR, "pricing.channels.ebay", "Required object is missing.")
        )
    else:
        _validate_enum(
            ebay.get("shipping_mode"),
            "pricing.channels.ebay.shipping_mode",
            {"UNSPECIFIED", "FREE", "BUYER_PAID"},
            issues,
        )
        money_fields = (
            "buyer_shipping_charge_cents",
            "seller_shipping_cost_cents",
            "acquisition_cost_cents",
            "packaging_cost_cents",
            "other_costs_cents",
            "fixed_order_fee_cents",
            "operator_initial_price_cents",
        )
        for field in money_fields:
            _validate_optional_nonnegative_int(
                ebay.get(field),
                f"pricing.channels.ebay.{field}",
                issues,
                maximum=MAX_MONEY_CENTS,
            )
        for field in ("marketplace_fee_bps", "promotion_fee_bps"):
            _validate_optional_nonnegative_int(
                ebay.get(field), f"pricing.channels.ebay.{field}", issues
            )
            if (
                isinstance(ebay.get(field), int)
                and not isinstance(ebay.get(field), bool)
                and ebay[field] > 10_000
            ):
                issues.append(
                    _issue(
                        IssueLevel.ERROR,
                        f"pricing.channels.ebay.{field}",
                        "Cannot exceed 100%.",
                    )
                )
        _validate_enum(
            ebay.get("price_ending_policy"),
            "pricing.channels.ebay.price_ending_policy",
            {"EXACT_CENTS", "END_99"},
            issues,
        )
        discounts = ebay.get("offer_discounts_bps")
        if not isinstance(discounts, list) or any(
            isinstance(item, bool)
            or not isinstance(item, int)
            or not 0 < item <= MAX_OFFER_DISCOUNT_BPS
            for item in discounts
        ):
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    "pricing.channels.ebay.offer_discounts_bps",
                    "Must be an array of discount rates above 0% and no more than 20%.",
                )
            )
        elif len(discounts) != len(set(discounts)):
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    "pricing.channels.ebay.offer_discounts_bps",
                    "Offer discount rates must not repeat.",
                )
            )
        shipping_mode = ebay.get("shipping_mode")
        buyer_charge = ebay.get("buyer_shipping_charge_cents")
        if shipping_mode == "FREE" and buyer_charge != 0:
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    "pricing.channels.ebay.buyer_shipping_charge_cents",
                    "Free shipping requires a $0 buyer shipping charge.",
                )
            )
        if shipping_mode == "BUYER_PAID" and (
            isinstance(buyer_charge, bool) or not isinstance(buyer_charge, int) or buyer_charge <= 0
        ):
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    "pricing.channels.ebay.buyer_shipping_charge_cents",
                    "Buyer-paid shipping requires a positive known charge.",
                )
            )
        if shipping_mode == "UNSPECIFIED" and buyer_charge is not None:
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    "pricing.channels.ebay.buyer_shipping_charge_cents",
                    "Choose a shipping method before saving a buyer shipping charge.",
                )
            )

    review = pricing.get("review")
    if not isinstance(review, dict):
        issues.append(_issue(IssueLevel.ERROR, "pricing.review", "Required object is missing."))
    else:
        _validate_enum(
            review.get("status"),
            "pricing.review.status",
            {"DRAFT", "NEEDS_REVIEW", "REVIEWED"},
            issues,
        )
        disposition = review.get("operator_disposition")
        if disposition is not None:
            _validate_enum(
                disposition,
                "pricing.review.operator_disposition",
                {"LIST", "LIVE_SELL", "HOLD_FOR_SEASON", "MANUAL_REVIEW"},
                issues,
            )
        if review.get("status") == "REVIEWED" and disposition is None:
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    "pricing.review.operator_disposition",
                    "A reviewed pricing decision requires an operator disposition.",
                )
            )
        operator_price = (
            ebay.get("operator_initial_price_cents") if isinstance(ebay, dict) else None
        )
        if disposition == "LIST" and (
            isinstance(operator_price, bool)
            or not isinstance(operator_price, int)
            or operator_price < 1_000
        ):
            issues.append(
                _issue(
                    IssueLevel.ERROR,
                    "pricing.review.operator_disposition",
                    "LIST requires a saved initial item price of at least $10 before shipping.",
                )
            )

    analysis = pricing.get("analysis")
    if analysis is not None and not isinstance(analysis, dict):
        issues.append(_issue(IssueLevel.ERROR, "pricing.analysis", "Must be a JSON object."))
    elif isinstance(analysis, dict) and analysis:
        expected = build_analysis_snapshot(package)
        if not _analysis_matches_snapshot(analysis, expected):
            issues.append(
                _issue(
                    IssueLevel.WARNING,
                    "pricing.analysis",
                    "Saved pricing results are stale; save the pricing inputs to recompute them.",
                )
            )


def _analysis_matches_snapshot(saved: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Compare derived fields while allowing unfamiliar future fields to survive."""

    for key, expected_value in expected.items():
        if key != "scenarios":
            if saved.get(key) != expected_value:
                return False
            continue
        saved_scenarios = saved.get("scenarios")
        if not isinstance(saved_scenarios, list) or not isinstance(expected_value, list):
            return False
        if len(saved_scenarios) != len(expected_value):
            return False
        saved_by_discount = {
            scenario.get("discount_bps"): scenario
            for scenario in saved_scenarios
            if isinstance(scenario, dict)
        }
        for expected_scenario in expected_value:
            if not isinstance(expected_scenario, dict):
                return False
            saved_scenario = saved_by_discount.get(expected_scenario.get("discount_bps"))
            if not isinstance(saved_scenario, dict) or any(
                saved_scenario.get(field) != value for field, value in expected_scenario.items()
            ):
                return False
    return True


def validate_listing_package(package: dict[str, Any]) -> tuple[ValidationIssue, ...]:
    """Validate the editable contract without discarding unfamiliar fields."""

    if not isinstance(package, dict):
        raise TypeError("package must be a dictionary")
    issues: list[ValidationIssue] = []

    if package.get("schema_version") != "1.0":
        issues.append(_issue(IssueLevel.ERROR, "schema_version", "Expected schema version 1.0."))
    if package.get("package_type") != "goodeals_listing_content":
        issues.append(
            _issue(
                IssueLevel.ERROR,
                "package_type",
                "Expected goodeals_listing_content.",
            )
        )

    for path in ("input", "product", "platforms", "review"):
        if not isinstance(package.get(path), dict):
            issues.append(_issue(IssueLevel.ERROR, path, "Required object is missing."))

    ebay = _value_at(package, "platforms", "ebay")
    shopify = _value_at(package, "platforms", "shopify")
    if not isinstance(ebay, dict):
        issues.append(_issue(IssueLevel.ERROR, "platforms.ebay", "Required object is missing."))
        ebay = {}
    if not isinstance(shopify, dict):
        issues.append(_issue(IssueLevel.ERROR, "platforms.shopify", "Required object is missing."))
        shopify = {}

    for platform_name, platform in (("ebay", ebay), ("shopify", shopify)):
        for field in ("title", "description"):
            value = platform.get(field)
            if not isinstance(value, str) or not value.strip():
                issues.append(
                    _issue(
                        IssueLevel.ERROR,
                        f"platforms.{platform_name}.{field}",
                        "Required text is missing.",
                    )
                )

    ebay_specifics = ebay.get("item_specifics")
    _validate_attribute_map(
        ebay_specifics,
        "platforms.ebay.item_specifics",
        issues,
    )
    _validate_attribute_map(
        shopify.get("attributes"),
        "platforms.shopify.attributes",
        issues,
    )

    if (
        not isinstance(_value_at(package, "input", "brand"), str)
        or not _value_at(package, "input", "brand").strip()
    ):
        issues.append(
            _issue(
                IssueLevel.ERROR,
                "input.brand",
                "Brand is required.",
            )
        )

    product = package.get("product")
    if isinstance(product, dict):
        _validate_msrp(package, product, issues)
    _validate_pricing(package, issues)

    tags = shopify.get("tags")
    if not isinstance(tags, list):
        issues.append(
            _issue(
                IssueLevel.ERROR,
                "platforms.shopify.tags",
                "Tags must be a JSON array of text values.",
            )
        )
    else:
        for index, tag in enumerate(tags):
            if not isinstance(tag, str):
                issues.append(
                    _issue(
                        IssueLevel.ERROR,
                        f"platforms.shopify.tags[{index}]",
                        "Each tag must be text.",
                    )
                )

    if not isinstance(ebay_specifics, dict):
        ebay_specifics = {}

    repeated_values = (
        (
            "Brand",
            _value_at(package, "input", "brand"),
            _value_at(package, "product", "brand"),
            ebay_specifics.get("Brand"),
            shopify.get("vendor"),
        ),
        (
            "UPC",
            _value_at(package, "input", "upc"),
            _value_at(package, "product", "upc"),
            ebay_specifics.get("UPC"),
        ),
        (
            "MPN",
            _value_at(package, "input", "mpn"),
            _value_at(package, "product", "mpn"),
            ebay_specifics.get("MPN"),
            shopify.get("mpn_metafield_candidate"),
        ),
    )
    for label, *values in repeated_values:
        if len({json.dumps(value, sort_keys=True) for value in values}) > 1:
            issues.append(
                _issue(
                    IssueLevel.WARNING,
                    label,
                    f"Repeated {label} values do not all match.",
                )
            )

    if ebay.get("title") != shopify.get("title"):
        issues.append(
            _issue(
                IssueLevel.WARNING,
                "platforms.*.title",
                "The eBay and Shopify titles are different.",
            )
        )
    if ebay.get("description") != shopify.get("description"):
        issues.append(
            _issue(
                IssueLevel.WARNING,
                "platforms.*.description",
                "The eBay and Shopify descriptions are different.",
            )
        )

    title = ebay.get("title")
    description = ebay.get("description")
    if isinstance(title, str) and len(title) > 70:
        issues.append(_issue(IssueLevel.WARNING, "platforms.*.title", "Exceeds 70 characters."))
    if isinstance(description, str) and len(description) > 1000:
        issues.append(
            _issue(IssueLevel.WARNING, "platforms.*.description", "Exceeds 1,000 characters.")
        )

    seo_title = shopify.get("seo_title")
    seo_description = shopify.get("seo_description")
    if isinstance(seo_title, str) and len(seo_title) > 70:
        issues.append(
            _issue(
                IssueLevel.WARNING,
                "platforms.shopify.seo_title",
                "Exceeds Shopify's 70-character SEO title allowance.",
            )
        )
    if isinstance(seo_description, str) and len(seo_description) > 160:
        issues.append(
            _issue(
                IssueLevel.WARNING,
                "platforms.shopify.seo_description",
                "Exceeds the practical 160-character SEO description target.",
            )
        )

    customer_facing_fields = (
        ("platforms.ebay.title", ebay.get("title")),
        ("platforms.ebay.description", ebay.get("description")),
        ("platforms.shopify.title", shopify.get("title")),
        ("platforms.shopify.description", shopify.get("description")),
        ("platforms.shopify.seo_title", seo_title),
        ("platforms.shopify.seo_description", seo_description),
        ("platforms.shopify.url_handle", shopify.get("url_handle")),
    )
    for label, identifier in (
        ("UPC", _value_at(package, "input", "upc")),
        ("MPN", _value_at(package, "input", "mpn")),
    ):
        if not isinstance(identifier, str) or len(identifier.strip()) < 4:
            continue
        normalized_identifier = identifier.strip().casefold()
        for path, value in customer_facing_fields:
            if isinstance(value, str) and normalized_identifier in value.casefold():
                issues.append(
                    _issue(
                        IssueLevel.WARNING,
                        path,
                        f"Remove the {label} from customer-facing listing copy.",
                    )
                )

    filenames = _value_at(package, "input", "image_filenames")
    images = package.get("images")
    if isinstance(filenames, list) and isinstance(images, list):
        image_names = [item.get("filename") for item in images if isinstance(item, dict)]
        if filenames != image_names:
            issues.append(
                _issue(
                    IssueLevel.WARNING,
                    "images",
                    "Image filenames or order differ from input.image_filenames.",
                )
            )

    if _value_at(package, "review", "manual_review_required") is not True:
        issues.append(
            _issue(
                IssueLevel.WARNING,
                "review.manual_review_required",
                "Manual review should remain required.",
            )
        )
    return tuple(issues)


def mapping_to_rows(mapping: dict[str, str | None]) -> list[dict[str, str]]:
    """Convert an attribute object into editable display rows."""

    if not isinstance(mapping, dict):
        raise TypeError("mapping must be a dictionary")
    return [
        {"Attribute": key, "Value": "" if value is None else value}
        for key, value in mapping.items()
    ]


def rows_to_mapping(rows: list[dict[str, Any]]) -> dict[str, str | None]:
    """Convert edited rows back to a JSON attribute object."""

    result: dict[str, str | None] = {}
    for row in rows:
        key_value = row.get("Attribute")
        if key_value is None or not str(key_value).strip():
            continue
        key = str(key_value).strip()
        if key in result:
            raise ListingPackageError(
                "DUPLICATE_ATTRIBUTE",
                f"The edited table contains the attribute more than once: {key}",
            )
        raw_value = row.get("Value")
        value = None if raw_value is None or not str(raw_value).strip() else str(raw_value).strip()
        result[key] = value
    return result


def parse_shopify_tags(value: str) -> list[str]:
    """Convert comma- or line-separated Shopify tags into JSON tag values."""

    if not isinstance(value, str):
        raise TypeError("value must be text")
    comma_separated = value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", ",")
    return [tag.strip() for tag in comma_separated.split(",") if tag.strip()]


def plain_review_note(value: object) -> str:
    """Turn one technical GPT review note into an operator-friendly sentence."""

    text = str(value).strip()
    technical_fields, separator, explanation = text.partition(": ")
    if not separator:
        return text

    first_path = technical_fields.split(",", maxsplit=1)[0].strip()
    field_name = first_path.rsplit(".", maxsplit=1)[-1].replace("_", " ").strip()
    field_label = {"upc": "UPC", "mpn": "MPN"}.get(field_name.casefold(), field_name.title())

    replacements = (
        ("null because", "Left blank because"),
        ("PROXY best-fit", "Best available match for"),
        ("PROXY family-level", "Uses closely related product"),
        ("PROXY value", "Suggested value"),
        ("PROXY", "Suggested"),
    )
    for technical, plain in replacements:
        if explanation.startswith(technical):
            explanation = plain + explanation[len(technical) :]
            break
    explanation = explanation.replace("PROXY", "suggested")
    return f"{field_label}: {explanation}"


def plain_review_status(value: object) -> str:
    """Translate the GPT's review status into a plain-English summary."""

    statuses = {
        "ready_for_import": "The GPT finished preparing this listing. It is ready for your review.",
        "needs_user_input": "The GPT needs your input before it can finish this listing.",
        "identity_unresolved": "The GPT could not confirm the product identity. Review it before use.",
        "needs_review": "The GPT found details that need your attention before you use the listing.",
        "incomplete": "The GPT could not finish every part of the listing.",
    }
    return statuses.get(str(value), "The GPT did not provide a recognized preparation status.")


def dump_listing_package(package: dict[str, Any]) -> str:
    """Create a readable UTF-8 JSON representation for download or advanced editing."""

    try:
        return json.dumps(package, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    except (TypeError, ValueError) as error:
        raise ListingPackageError(
            "INVALID_JSON_VALUE",
            "The listing package contains a value that JSON cannot represent.",
        ) from error


def _render_mapping(mapping: Any) -> list[str]:
    if not isinstance(mapping, dict):
        return []
    return [f"{key}: {'' if value is None else value}" for key, value in mapping.items()]


def platform_copy_text(package: dict[str, Any], platform_name: str) -> str:
    """Render one platform section as plain text for manual copy and paste."""

    platforms = package.get("platforms")
    if not isinstance(platforms, dict) or platform_name not in {"ebay", "shopify"}:
        raise ValueError("platform_name must be ebay or shopify")
    platform = platforms.get(platform_name)
    if not isinstance(platform, dict):
        raise ListingPackageError(
            "PLATFORM_SECTION_MISSING",
            f"The {platform_name} section is missing.",
        )

    lines = [
        "TITLE",
        str(platform.get("title") or ""),
        "",
        "DESCRIPTION",
        str(platform.get("description") or ""),
        "",
        "CATEGORY",
        str(platform.get("category_suggestion") or ""),
    ]
    if platform_name == "ebay":
        lines.extend(("", "ITEM SPECIFICS"))
        lines.extend(_render_mapping(platform.get("item_specifics")))
    else:
        barcode = _value_at(package, "product", "upc")
        if barcode is None:
            barcode = _value_at(package, "input", "upc")
        lines.extend(
            (
                "",
                "VENDOR",
                str(platform.get("vendor") or ""),
                "",
                "PRODUCT TYPE",
                str(platform.get("product_type") or ""),
                "",
                "BARCODE (UPC)",
                str(barcode or ""),
                "",
                "MPN METAFIELD",
                str(platform.get("mpn_metafield_candidate") or ""),
                "",
                "ATTRIBUTES",
            )
        )
        lines.extend(_render_mapping(platform.get("attributes")))
        tags = platform.get("tags")
        tag_text = (
            ", ".join(str(tag) for tag in tags if tag is not None) if isinstance(tags, list) else ""
        )
        lines.extend(("", "TAGS", tag_text))
        lines.extend(
            (
                "",
                "SEO TITLE",
                str(platform.get("seo_title") or ""),
                "",
                "SEO DESCRIPTION",
                str(platform.get("seo_description") or ""),
                "",
                "URL HANDLE",
                str(platform.get("url_handle") or ""),
            )
        )
    return "\n".join(lines).rstrip() + "\n"
