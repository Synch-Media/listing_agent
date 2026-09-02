"""Deterministic, offline pricing calculations for listing packages."""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
from numbers import Integral, Real
from typing import Any

MIN_INITIAL_ITEM_PRICE_CENTS = 1_000
PREFERRED_MARGIN_BPS = 3_000
PREFERRED_ROI_BPS = 10_000
MAX_MONEY_CENTS = 99_999_999_999
MAX_OFFER_DISCOUNT_BPS = 2_000
DEFAULT_OFFER_DISCOUNTS_BPS = (1_000, 1_500, 2_000)

_MONEY_PATTERN = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d{1,2})?$")
_PERCENT_PATTERN = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d{1,2})?$")


class PricingInputError(ValueError):
    """One operator-entered pricing value could not be parsed safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ConditionTier(StrEnum):
    """Condition groups that have a GoodeDeals MSRP reference rate."""

    UNSPECIFIED = "UNSPECIFIED"
    NWT = "NWT"
    NWOT = "NWOT"
    USED = "USED"


class DemandPosition(StrEnum):
    """Operator-selected market position; no hidden thresholds are inferred."""

    UNSPECIFIED = "UNSPECIFIED"
    WEAK_HIGH_COMPETITION = "WEAK_HIGH_COMPETITION"
    BALANCED = "BALANCED"
    STRONG_LOW_COMPETITION = "STRONG_LOW_COMPETITION"


class PriceEndingPolicy(StrEnum):
    """How to present a recommendation without exceeding its ceiling."""

    EXACT_CENTS = "EXACT_CENTS"
    END_99 = "END_99"


class Recommendation(StrEnum):
    """The local market recommendation."""

    LIST = "LIST"
    LIVE_SELL = "LIVE_SELL"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class ProfitabilityStatus(StrEnum):
    """Whether the user's preferred margin and ROI levels are met."""

    PREFERRED = "PREFERRED"
    MIXED = "MIXED"
    BELOW_PREFERENCES = "BELOW_PREFERENCES"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """One list-price or offer scenario in integer cents and basis points."""

    discount_bps: int
    item_price_cents: int
    buyer_shipping_charge_cents: int | None
    buyer_total_cents: int | None
    marketplace_fee_cents: int | None
    promotion_fee_cents: int | None
    net_profit_cents: int | None
    margin_bps: int | None
    roi_bps: int | None
    profitability_status: str


@dataclass(frozen=True, slots=True)
class PopulationSummary:
    """Qualified comp population and its Type-7 percentile statistics."""

    population_tier: str
    sample_strength: str
    count: int
    p20_cents: int | None
    p25_cents: int | None
    p375_cents: int | None
    p50_cents: int | None
    outlier_comp_ids: tuple[str, ...]
    duplicate_comp_ids: tuple[str, ...]


def _round_fraction(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return sign * quotient


def _blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return not str(value).strip()


def parse_money_to_cents(value: object, *, label: str = "Amount") -> int | None:
    """Parse nonnegative dollar text into cents without binary-float arithmetic."""

    if _blank(value):
        return None
    if isinstance(value, bool):
        raise PricingInputError("INVALID_MONEY", f"{label} must be a dollar amount.")
    text = str(value).strip().replace(",", "")
    if text.startswith("$"):
        text = text[1:].strip()
    if not _MONEY_PATTERN.fullmatch(text):
        raise PricingInputError(
            "INVALID_MONEY",
            f"{label} must be a nonnegative dollar amount with at most two decimals.",
        )
    whole, _, fractional = text.partition(".")
    cents = int(whole) * 100 + int(fractional.ljust(2, "0") or "0")
    if cents > MAX_MONEY_CENTS:
        raise PricingInputError(
            "INVALID_MONEY",
            f"{label} cannot exceed $999,999,999.99.",
        )
    return cents


def parse_percent_to_bps(value: object, *, label: str = "Rate") -> int | None:
    """Parse percentage text such as 13.6 into integer basis points."""

    if _blank(value):
        return None
    if isinstance(value, bool):
        raise PricingInputError("INVALID_PERCENT", f"{label} must be a percentage.")
    text = str(value).strip().removesuffix("%").strip()
    if not _PERCENT_PATTERN.fullmatch(text):
        raise PricingInputError(
            "INVALID_PERCENT",
            f"{label} must be between 0 and 100 with at most two decimals.",
        )
    whole, _, fractional = text.partition(".")
    bps = int(whole) * 100 + int(fractional.ljust(2, "0") or "0")
    if bps > 10_000:
        raise PricingInputError("INVALID_PERCENT", f"{label} cannot exceed 100%.")
    return bps


def format_cents(cents: object, *, currency_symbol: bool = False) -> str:
    """Format integer cents for a user-facing field; unknown values stay blank."""

    if not _is_int(cents):
        return ""
    sign = "-" if cents < 0 else ""
    absolute = abs(cents)
    amount = f"{sign}{absolute // 100}.{absolute % 100:02d}"
    return f"${amount}" if currency_symbol else amount


def format_bps(bps: object) -> str:
    """Format integer basis points as percentage text without a percent sign."""

    if not _is_int(bps):
        return ""
    sign = "-" if bps < 0 else ""
    absolute = abs(bps)
    return f"{sign}{absolute // 100}.{absolute % 100:02d}"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonnegative_int(value: object) -> int | None:
    return value if _is_int(value) and value >= 0 else None


def _money_int(value: object) -> int | None:
    return value if _is_int(value) and 0 <= value <= MAX_MONEY_CENTS else None


def _rate_bps(value: object) -> int | None:
    return value if _is_int(value) and 0 <= value <= 10_000 else None


def _optional_text(value: object) -> str | None:
    return None if _blank(value) else str(value).strip()


def _iso_date_text(value: object) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def infer_condition_tier(condition: object) -> ConditionTier:
    """Infer only narrow, explicit condition phrases; ambiguous 'New' stays unspecified."""

    normalized = " ".join(str(condition or "").strip().casefold().replace("-", " ").split())
    if normalized in {"nwt", "new with tags", "new with original tags"}:
        return ConditionTier.NWT
    if normalized in {"nwot", "new without tags", "new no tags"}:
        return ConditionTier.NWOT
    if normalized in {"used", "pre owned", "preowned"}:
        return ConditionTier.USED
    return ConditionTier.UNSPECIFIED


def msrp_target_cents(msrp_cents: object, condition_tier: object) -> int | None:
    """Return the user's advisory MSRP target; it is never an accepted-price floor."""

    amount = _money_int(msrp_cents)
    rates = {
        ConditionTier.NWT.value: 2_500,
        ConditionTier.NWOT.value: 2_000,
        ConditionTier.USED.value: 1_000,
    }
    rate = rates.get(str(condition_tier))
    return None if amount is None or rate is None else _round_fraction(amount * rate, 10_000)


def type7_percentile_cents(values: Sequence[int], percentile_bps: int) -> int | None:
    """Calculate an inclusive Type-7 percentile and round half-up to one cent."""

    if not values:
        return None
    if not 0 <= percentile_bps <= 10_000:
        raise ValueError("percentile_bps must be between 0 and 10000")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank_numerator = (len(ordered) - 1) * percentile_bps
    lower_index, remainder = divmod(rank_numerator, 10_000)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    numerator = ordered[lower_index] * (10_000 - remainder) + ordered[upper_index] * remainder
    return _round_fraction(numerator, 10_000)


def _event_key(comp: Mapping[str, Any]) -> tuple[object, ...] | None:
    transaction_id = _optional_text(comp.get("transaction_id"))
    if transaction_id:
        return ("transaction", transaction_id.casefold())
    item_id = _optional_text(comp.get("item_id"))
    event_date = _iso_date_text(comp.get("event_date"))
    item_price = _money_int(comp.get("item_price_cents"))
    shipping = _money_int(comp.get("shipping_cents"))
    quantity = _nonnegative_int(comp.get("quantity"))
    if item_id and event_date and item_price is not None and shipping is not None and quantity:
        return (
            "fallback",
            item_id.casefold(),
            event_date,
            item_price,
            shipping,
            quantity,
        )
    return None


def _active_key(comp: Mapping[str, Any]) -> tuple[str, str] | None:
    item_id = _optional_text(comp.get("item_id"))
    if item_id:
        return ("item", item_id.casefold())
    url = _optional_text(comp.get("url"))
    return ("url", url.casefold()) if url else None


def _prepared_comps(
    value: object,
    *,
    active: bool,
) -> tuple[list[tuple[Mapping[str, Any], int]], tuple[str, ...]]:
    if not isinstance(value, list):
        return [], ()
    result: list[tuple[Mapping[str, Any], int]] = []
    duplicates: list[str] = []
    seen: set[tuple[object, ...]] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or item.get("included") is not True:
            continue
        if item.get("sale_format") != "FIXED_PRICE":
            continue
        item_price = _money_int(item.get("item_price_cents"))
        shipping = _money_int(item.get("shipping_cents"))
        if item_price is None or shipping is None:
            continue
        key = _active_key(item) if active else _event_key(item)
        comp_id = _optional_text(item.get("comp_id")) or f"ROW-{index + 1:03d}"
        if key is None:
            continue
        if key is not None and key in seen:
            duplicates.append(comp_id)
            continue
        if key is not None:
            seen.add(key)
        result.append((item, item_price + shipping))
    return result, tuple(duplicates)


def _sample_strength(count: int) -> str:
    if count == 0:
        return "NONE"
    if count < 3:
        return "WEAK"
    if count < 8:
        return "LIMITED"
    return "SUFFICIENT"


def summarize_sold_population(value: object) -> PopulationSummary:
    """Select relevant sold comps, deduplicate events, and flag IQR outliers."""

    prepared, duplicate_ids = _prepared_comps(value, active=False)
    exact = [item for item in prepared if item[0].get("match_tier") == "EXACT"]
    near = [item for item in prepared if item[0].get("match_tier") == "NEAR_EXACT"]
    if len(exact) >= 3:
        population = exact
        tier = "EXACT"
    else:
        population = exact + near
        tier = "EXACT_AND_NEAR" if population else "NONE"

    outlier_ids: list[str] = []
    filtered = list(population)
    if len(population) >= 8:
        totals = [total for _, total in population]
        q1 = type7_percentile_cents(totals, 2_500)
        q3 = type7_percentile_cents(totals, 7_500)
        assert q1 is not None and q3 is not None
        iqr = q3 - q1
        lower_fence = q1 - _round_fraction(iqr * 15, 10)
        upper_fence = q3 + _round_fraction(iqr * 15, 10)
        filtered = []
        for index, (comp, total) in enumerate(population):
            if total < lower_fence or total > upper_fence:
                outlier_ids.append(_optional_text(comp.get("comp_id")) or f"ROW-{index + 1:03d}")
            else:
                filtered.append((comp, total))

    totals = [total for _, total in filtered]
    return PopulationSummary(
        population_tier=tier,
        sample_strength=_sample_strength(len(filtered)),
        count=len(filtered),
        p20_cents=type7_percentile_cents(totals, 2_000),
        p25_cents=type7_percentile_cents(totals, 2_500),
        p375_cents=type7_percentile_cents(totals, 3_750),
        p50_cents=type7_percentile_cents(totals, 5_000),
        outlier_comp_ids=tuple(outlier_ids),
        duplicate_comp_ids=duplicate_ids,
    )


def summarize_active_p25(value: object) -> tuple[int | None, int, tuple[str, ...]]:
    """Return the credible active-listing P25 that may cap a sold-market target."""

    prepared, duplicate_ids = _prepared_comps(value, active=True)
    exact = [total for comp, total in prepared if comp.get("match_tier") == "EXACT"]
    near = [total for comp, total in prepared if comp.get("match_tier") == "NEAR_EXACT"]
    if exact:
        values = exact
    elif len(near) >= 3:
        values = near
    else:
        values = []
    return type7_percentile_cents(values, 2_500), len(values), duplicate_ids


def apply_price_ending(ceiling_cents: int, policy: object) -> int:
    """Apply the selected ending without exceeding the market-derived ceiling."""

    if ceiling_cents < 0:
        raise ValueError("ceiling_cents cannot be negative")
    if policy != PriceEndingPolicy.END_99.value:
        return ceiling_cents
    dollars = ceiling_cents // 100
    candidate = dollars * 100 + 99
    if candidate > ceiling_cents:
        candidate -= 100
    if ceiling_cents >= MIN_INITIAL_ITEM_PRICE_CENTS and candidate < MIN_INITIAL_ITEM_PRICE_CENTS:
        return MIN_INITIAL_ITEM_PRICE_CENTS
    return max(candidate, 0)


def calculate_scenario(
    channel: Mapping[str, Any],
    initial_item_price_cents: int,
    discount_bps: int,
) -> ScenarioResult:
    """Calculate one scenario; missing assumptions remain unknown rather than zero."""

    if not _is_int(initial_item_price_cents) or not 0 <= initial_item_price_cents <= (
        MAX_MONEY_CENTS * 2
    ):
        raise ValueError("initial_item_price_cents is outside the supported range")
    if not _is_int(discount_bps) or not 0 <= discount_bps <= MAX_OFFER_DISCOUNT_BPS:
        raise ValueError("discount_bps must be between 0 and 2000")
    item_price = _round_fraction(initial_item_price_cents * (10_000 - discount_bps), 10_000)
    money_names = (
        "seller_shipping_cost_cents",
        "acquisition_cost_cents",
        "packaging_cost_cents",
        "other_costs_cents",
        "fixed_order_fee_cents",
    )
    values = {name: _money_int(channel.get(name)) for name in money_names}
    values["marketplace_fee_bps"] = _rate_bps(channel.get("marketplace_fee_bps"))
    values["promotion_fee_bps"] = _rate_bps(channel.get("promotion_fee_bps"))
    buyer_shipping = _buyer_shipping_charge(channel)
    if buyer_shipping is None or any(values[name] is None for name in values):
        return ScenarioResult(
            discount_bps=discount_bps,
            item_price_cents=item_price,
            buyer_shipping_charge_cents=buyer_shipping,
            buyer_total_cents=None,
            marketplace_fee_cents=None,
            promotion_fee_cents=None,
            net_profit_cents=None,
            margin_bps=None,
            roi_bps=None,
            profitability_status=ProfitabilityStatus.UNKNOWN.value,
        )

    seller_shipping = values["seller_shipping_cost_cents"]
    acquisition = values["acquisition_cost_cents"]
    packaging = values["packaging_cost_cents"]
    other_costs = values["other_costs_cents"]
    marketplace_rate = values["marketplace_fee_bps"]
    promotion_rate = values["promotion_fee_bps"]
    fixed_fee = values["fixed_order_fee_cents"]
    assert all(
        item is not None
        for item in (
            buyer_shipping,
            seller_shipping,
            acquisition,
            packaging,
            other_costs,
            marketplace_rate,
            promotion_rate,
            fixed_fee,
        )
    )
    buyer_total = item_price + buyer_shipping
    marketplace_fee = _round_fraction(buyer_total * marketplace_rate, 10_000)
    promotion_fee = _round_fraction(buyer_total * promotion_rate, 10_000)
    profit = (
        buyer_total
        - marketplace_fee
        - promotion_fee
        - fixed_fee
        - acquisition
        - seller_shipping
        - packaging
        - other_costs
    )
    margin = _round_fraction(profit * 10_000, buyer_total) if buyer_total else None
    roi = _round_fraction(profit * 10_000, acquisition) if acquisition else None
    if margin is None or roi is None:
        profitability = ProfitabilityStatus.UNKNOWN
    elif margin > PREFERRED_MARGIN_BPS and roi > PREFERRED_ROI_BPS:
        profitability = ProfitabilityStatus.PREFERRED
    elif margin > PREFERRED_MARGIN_BPS or roi > PREFERRED_ROI_BPS:
        profitability = ProfitabilityStatus.MIXED
    else:
        profitability = ProfitabilityStatus.BELOW_PREFERENCES
    return ScenarioResult(
        discount_bps=discount_bps,
        item_price_cents=item_price,
        buyer_shipping_charge_cents=buyer_shipping,
        buyer_total_cents=buyer_total,
        marketplace_fee_cents=marketplace_fee,
        promotion_fee_cents=promotion_fee,
        net_profit_cents=profit,
        margin_bps=margin,
        roi_bps=roi,
        profitability_status=profitability.value,
    )


def _buyer_shipping_charge(channel: Mapping[str, Any]) -> int | None:
    mode = channel.get("shipping_mode")
    charge = _money_int(channel.get("buyer_shipping_charge_cents"))
    if mode == "FREE" and charge == 0:
        return 0
    if mode == "BUYER_PAID" and charge is not None and charge > 0:
        return charge
    return None


def _supported_msrp_cents(package: Mapping[str, Any]) -> int | None:
    product = _object(package.get("product"))
    msrp = _object(product.get("msrp"))
    amount = _money_int(msrp.get("amount_cents"))
    if (
        amount is None
        or msrp.get("basis") not in {"CURRENT", "HISTORICAL", "APPROXIMATE"}
        or msrp.get("confidence") not in {"EXACT", "PROXY"}
        or msrp.get("operator_reviewed") is not True
    ):
        return None
    source_ids = msrp.get("source_ids")
    if (
        not isinstance(source_ids, list)
        or not source_ids
        or any(not isinstance(item, str) or not item.strip() for item in source_ids)
        or len(source_ids) != len(set(source_ids))
    ):
        return None
    requested = set(source_ids)
    sources = package.get("sources")
    available = (
        {
            item.get("source_id")
            for item in sources
            if isinstance(item, Mapping)
            and isinstance(item.get("source_id"), str)
            and item.get("source_id").strip()
        }
        if isinstance(sources, list)
        else set()
    )
    return amount if requested and requested.issubset(available) else None


def _pricing_inputs_invalid(value: object) -> bool:
    if not isinstance(value, Mapping):
        return value is not None
    if value.get("pricing_schema_version") != "1.0" or value.get("currency") != "USD":
        return True
    if value.get("condition_tier") not in {item.value for item in ConditionTier}:
        return True
    research = value.get("research")
    if not isinstance(research, Mapping):
        return True
    if research.get("demand_position") not in {item.value for item in DemandPosition}:
        return True
    for name in ("sold_comps", "active_comps"):
        comps = research.get(name)
        if not isinstance(comps, list) or any(not isinstance(comp, Mapping) for comp in comps):
            return True
        for comp in comps:
            if not isinstance(comp.get("included"), bool):
                return True
            if comp.get("match_tier") not in {
                "EXACT",
                "NEAR_EXACT",
                "FALLBACK",
                "CONTEXT_ONLY",
            }:
                return True
            if comp.get("sale_format") not in {"FIXED_PRICE", "AUCTION", "UNKNOWN"}:
                return True
            if any(
                comp.get(field) is not None and _money_int(comp.get(field)) is None
                for field in ("item_price_cents", "shipping_cents")
            ):
                return True
    channels = value.get("channels")
    ebay = channels.get("ebay") if isinstance(channels, Mapping) else None
    if not isinstance(ebay, Mapping):
        return True
    money_fields = (
        "buyer_shipping_charge_cents",
        "seller_shipping_cost_cents",
        "acquisition_cost_cents",
        "packaging_cost_cents",
        "other_costs_cents",
        "fixed_order_fee_cents",
        "operator_initial_price_cents",
    )
    if any(
        ebay.get(field) is not None and _money_int(ebay.get(field)) is None
        for field in money_fields
    ):
        return True
    if any(
        ebay.get(field) is not None and _rate_bps(ebay.get(field)) is None
        for field in ("marketplace_fee_bps", "promotion_fee_bps")
    ):
        return True
    mode = ebay.get("shipping_mode")
    charge = ebay.get("buyer_shipping_charge_cents")
    if mode not in {"UNSPECIFIED", "FREE", "BUYER_PAID"}:
        return True
    if (
        (mode == "FREE" and charge != 0)
        or (mode == "BUYER_PAID" and (_money_int(charge) is None or charge <= 0))
        or (mode == "UNSPECIFIED" and charge is not None)
    ):
        return True
    if ebay.get("price_ending_policy") not in {item.value for item in PriceEndingPolicy}:
        return True
    discounts = ebay.get("offer_discounts_bps")
    return (
        not isinstance(discounts, list)
        or any(not _is_int(item) or not 0 < item <= MAX_OFFER_DISCOUNT_BPS for item in discounts)
        or len(discounts) != len(set(discounts))
    )


def default_msrp() -> dict[str, Any]:
    """Create the complete factual MSRP evidence object used by the Builder and app."""

    return {
        "amount_cents": None,
        "currency": "USD",
        "basis": "UNKNOWN",
        "confidence": "INSUFFICIENT",
        "source_ids": [],
        "observed_on": None,
        "operator_reviewed": False,
        "note": None,
    }


def default_pricing(condition: object = None) -> dict[str, Any]:
    """Create a complete local pricing extension without making financial assumptions."""

    return {
        "pricing_schema_version": "1.0",
        "currency": "USD",
        "condition_tier": infer_condition_tier(condition).value,
        "research": {
            "source": "EBAY_PRODUCT_RESEARCH",
            "window_days": None,
            "observed_on": None,
            "sell_through_rate_bps": None,
            "sold_count": None,
            "active_count": None,
            "demand_position": DemandPosition.UNSPECIFIED.value,
            "sold_comps": [],
            "active_comps": [],
            "operator_notes": None,
        },
        "channels": {
            "ebay": {
                "shipping_mode": "UNSPECIFIED",
                "buyer_shipping_charge_cents": None,
                "seller_shipping_cost_cents": None,
                "acquisition_cost_cents": None,
                "packaging_cost_cents": None,
                "other_costs_cents": None,
                "marketplace_fee_bps": None,
                "promotion_fee_bps": None,
                "fixed_order_fee_cents": None,
                "price_ending_policy": PriceEndingPolicy.END_99.value,
                "operator_initial_price_cents": None,
                "offer_discounts_bps": list(DEFAULT_OFFER_DISCOUNTS_BPS),
            }
        },
        "analysis": {},
        "review": {
            "status": "DRAFT",
            "operator_disposition": None,
            "note": None,
        },
    }


def _object(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def build_analysis_snapshot(package: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute the complete derived pricing snapshot from saved operator inputs."""

    product = _object(package.get("product"))
    msrp = _object(product.get("msrp"))
    raw_pricing = package.get("pricing")
    pricing = _object(raw_pricing)
    research = _object(pricing.get("research"))
    channels = _object(pricing.get("channels"))
    ebay = _object(channels.get("ebay"))
    sold = summarize_sold_population(research.get("sold_comps"))
    active_p25, active_count, active_duplicate_ids = summarize_active_p25(
        research.get("active_comps")
    )
    warnings: list[str] = []
    if sold.duplicate_comp_ids or active_duplicate_ids:
        warnings.append("Duplicate research records were ignored by event identity.")
    if sold.outlier_comp_ids:
        warnings.append("IQR outliers were flagged and excluded from the market percentiles.")

    demand = str(research.get("demand_position"))
    sold_anchor = {
        DemandPosition.WEAK_HIGH_COMPETITION.value: sold.p20_cents,
        DemandPosition.BALANCED.value: sold.p25_cents,
        DemandPosition.STRONG_LOW_COMPETITION.value: sold.p375_cents,
    }.get(demand)
    delivered_ceiling = sold_anchor
    if delivered_ceiling is not None and active_p25 is not None:
        delivered_ceiling = min(delivered_ceiling, max(active_p25 - 1, 0))

    buyer_shipping = _buyer_shipping_charge(ebay)
    item_ceiling: int | None = None
    recommended_price: int | None = None
    if delivered_ceiling is not None and buyer_shipping is not None:
        if delivered_ceiling >= buyer_shipping:
            item_ceiling = delivered_ceiling - buyer_shipping
            recommended_price = apply_price_ending(
                item_ceiling,
                ebay.get("price_ending_policy"),
            )
        else:
            warnings.append("Buyer-paid shipping exceeds the market delivered-price ceiling.")

    if sold.count < 3 or sold_anchor is None or recommended_price is None or sold.outlier_comp_ids:
        recommendation = Recommendation.MANUAL_REVIEW
    elif recommended_price < MIN_INITIAL_ITEM_PRICE_CENTS:
        recommendation = Recommendation.LIVE_SELL
    else:
        recommendation = Recommendation.LIST

    operator_price = _money_int(ebay.get("operator_initial_price_cents"))
    if operator_price is None:
        operator_status = "UNKNOWN"
    elif operator_price < MIN_INITIAL_ITEM_PRICE_CENTS:
        operator_status = "BELOW_LISTING_MINIMUM"
        recommendation = Recommendation.LIVE_SELL
    else:
        operator_status = "PASSES_LISTING_MINIMUM"

    condition_tier = pricing.get("condition_tier")
    supported_msrp = _supported_msrp_cents(package)
    target = msrp_target_cents(supported_msrp, condition_tier)
    scenario_base = operator_price if operator_price is not None else recommended_price
    discounts = ebay.get("offer_discounts_bps")
    discount_values = [0]
    if isinstance(discounts, list):
        discount_values.extend(
            dict.fromkeys(
                item for item in discounts if _is_int(item) and 0 < item <= MAX_OFFER_DISCOUNT_BPS
            )
        )
    scenarios = (
        [asdict(calculate_scenario(ebay, scenario_base, discount)) for discount in discount_values]
        if scenario_base is not None
        else []
    )
    if _pricing_inputs_invalid(raw_pricing):
        calculation_status = "INVALID"
    elif scenarios and all(item["net_profit_cents"] is not None for item in scenarios):
        calculation_status = "COMPLETE"
    else:
        calculation_status = "INCOMPLETE"
    if scenario_base is not None and target is not None and scenario_base < target:
        warnings.append("The selected initial price is below the condition-based MSRP target.")
    if sold.count and sold.count < 3:
        warnings.append("Fewer than three qualified sold comps require manual review.")
    if sold.outlier_comp_ids:
        warnings.append(
            "Review the flagged outlier comps and uncheck any that should be excluded before listing."
        )
    if _money_int(msrp.get("amount_cents")) is not None and supported_msrp is None:
        warnings.append(
            "MSRP evidence is unresolved, unreviewed, or not linked to package sources."
        )

    return {
        "calculation_version": "1.0",
        "calculation_status": calculation_status,
        "recommendation": recommendation.value,
        "sample_strength": sold.sample_strength,
        "population_tier": sold.population_tier,
        "qualified_sold_count": sold.count,
        "qualified_active_count": active_count,
        "sold_p20_cents": sold.p20_cents,
        "sold_p25_cents": sold.p25_cents,
        "sold_p375_cents": sold.p375_cents,
        "sold_p50_cents": sold.p50_cents,
        "active_p25_cents": active_p25,
        "market_delivered_ceiling_cents": delivered_ceiling,
        "market_item_ceiling_cents": item_ceiling,
        "recommended_initial_item_price_cents": recommended_price,
        "operator_initial_price_status": operator_status,
        "msrp_target_cents": target,
        "outlier_comp_ids": list(sold.outlier_comp_ids),
        "duplicate_comp_ids": list(sold.duplicate_comp_ids + active_duplicate_ids),
        "scenarios": scenarios,
        "warnings": warnings,
    }


_COMP_COLUMNS = (
    ("Comp ID", "comp_id"),
    ("Include", "included"),
    ("Match tier", "match_tier"),
    ("Format", "sale_format"),
    ("Title", "title"),
    ("Item ID", "item_id"),
    ("Transaction ID", "transaction_id"),
    ("Event date", "event_date"),
    ("Item price ($)", "item_price_cents"),
    ("Shipping ($)", "shipping_cents"),
    ("Quantity", "quantity"),
    ("URL", "url"),
    ("Exclusion reason", "exclusion_reason"),
    ("Operator note", "operator_note"),
)

_MATCH_TIERS = {"EXACT", "NEAR_EXACT", "FALLBACK", "CONTEXT_ONLY"}
_SALE_FORMATS = {"FIXED_PRICE", "AUCTION", "UNKNOWN"}


def _coerce_quantity(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Integral):
        quantity = int(value)
    elif isinstance(value, Real):
        numeric = float(value)
        if not math.isfinite(numeric) or not numeric.is_integer():
            return None
        quantity = int(numeric)
    else:
        text = str(value).strip()
        if not re.fullmatch(r"\d+", text):
            return None
        quantity = int(text)
    return quantity if quantity > 0 else None


def comparables_to_rows(value: object) -> list[dict[str, Any]]:
    """Convert comp records into editable, dollar-denominated table rows."""

    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        row: dict[str, Any] = {}
        for label, key in _COMP_COLUMNS:
            raw = item.get(key)
            if key.endswith("_cents"):
                row[label] = format_cents(raw)
            elif key == "quantity":
                row[label] = _coerce_quantity(raw)
            elif key == "included":
                row[label] = raw is True
            elif key == "match_tier":
                row[label] = raw if isinstance(raw, str) and raw in _MATCH_TIERS else "CONTEXT_ONLY"
            elif key == "sale_format":
                row[label] = raw if isinstance(raw, str) and raw in _SALE_FORMATS else "UNKNOWN"
            else:
                row[label] = None if raw is None else str(raw)
        rows.append(row)
    return rows


def _next_comp_id(existing: set[str], prefix: str) -> str:
    index = 1
    while f"{prefix}-{index:03d}" in existing:
        index += 1
    return f"{prefix}-{index:03d}"


def _parse_quantity(value: object, *, comp_id: str) -> int | None:
    if _blank(value):
        return None
    quantity = _coerce_quantity(value)
    if quantity is None or quantity <= 0:
        raise PricingInputError(
            "INVALID_QUANTITY",
            f"{comp_id} quantity must be a positive whole number.",
        )
    return quantity


def merge_comparable_rows(
    original: object,
    rows: Iterable[Mapping[str, Any]],
    *,
    prefix: str,
) -> list[dict[str, Any]]:
    """Merge known table columns while preserving unfamiliar fields in each comp."""

    originals = original if isinstance(original, list) else []
    by_id = {
        str(item.get("comp_id")): item
        for item in originals
        if isinstance(item, Mapping) and _optional_text(item.get("comp_id"))
    }
    used: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        if all(_blank(value) for value in row.values()):
            continue
        requested_id = _optional_text(row.get("Comp ID"))
        comp_id = requested_id or _next_comp_id(used | set(by_id), prefix)
        if comp_id in used:
            raise PricingInputError(
                "DUPLICATE_COMP_ID",
                f"The comparable ID appears more than once: {comp_id}",
            )
        used.add(comp_id)
        source = by_id.get(comp_id)
        item = copy.deepcopy(dict(source)) if isinstance(source, Mapping) else {}
        item["comp_id"] = comp_id
        item["included"] = row.get("Include") is True
        item["match_tier"] = _optional_text(row.get("Match tier")) or "CONTEXT_ONLY"
        item["sale_format"] = _optional_text(row.get("Format")) or "UNKNOWN"
        item["title"] = _optional_text(row.get("Title"))
        item["item_id"] = _optional_text(row.get("Item ID"))
        item["transaction_id"] = _optional_text(row.get("Transaction ID"))
        item["event_date"] = _optional_text(row.get("Event date"))
        item["item_price_cents"] = parse_money_to_cents(
            row.get("Item price ($)"), label=f"{comp_id} item price"
        )
        item["shipping_cents"] = parse_money_to_cents(
            row.get("Shipping ($)"), label=f"{comp_id} shipping"
        )
        item["quantity"] = _parse_quantity(row.get("Quantity"), comp_id=comp_id)
        item["url"] = _optional_text(row.get("URL"))
        item["exclusion_reason"] = _optional_text(row.get("Exclusion reason"))
        item["operator_note"] = _optional_text(row.get("Operator note"))
        if item["included"] is True:
            identity = (
                _active_key(item) if prefix.upper().startswith("ACTIVE") else _event_key(item)
            )
            if identity is None:
                expected = (
                    "an item ID or URL"
                    if prefix.upper().startswith("ACTIVE")
                    else "a transaction ID, or item ID plus valid sold date, price, shipping, and quantity"
                )
                raise PricingInputError(
                    "MISSING_COMP_IDENTITY",
                    f"{comp_id} needs {expected} before it can be included.",
                )
        result.append(item)
    return result


def build_research_queries(package: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Build a narrow-to-broad Product Research search plan for manual execution."""

    input_data = _object(package.get("input"))
    product = _object(package.get("product"))
    brand = _optional_text(product.get("brand")) or _optional_text(input_data.get("brand"))
    upc = _optional_text(product.get("upc")) or _optional_text(input_data.get("upc"))
    mpn = _optional_text(product.get("mpn")) or _optional_text(input_data.get("mpn"))
    name = _optional_text(product.get("name"))
    product_type = _optional_text(product.get("product_type"))
    attributes = _object(product.get("attributes"))
    size = _optional_text(attributes.get("size")) or _optional_text(attributes.get("Size"))
    color = _optional_text(attributes.get("color")) or _optional_text(attributes.get("Color"))

    candidates: list[tuple[str, str | None]] = [
        ("Exact UPC", upc),
        ("Exact MPN", mpn),
        ("Brand and MPN", " ".join(item for item in (brand, mpn) if item)),
        ("Brand and product", " ".join(item for item in (brand, name) if item)),
        (
            "Controlled broad search",
            " ".join(item for item in (brand, product_type, size, color) if item),
        ),
    ]
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, query in candidates:
        normalized = " ".join(str(query or "").split())
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        result.append((label, normalized))
    return tuple(result)
