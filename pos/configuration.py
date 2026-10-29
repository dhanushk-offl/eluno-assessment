"""Strict schemas at the editable JSON boundary; invalid data never reaches payment."""
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (BaseModel, ConfigDict, Field, StrictBool, StrictInt,
                      StringConstraints, ValidationError, field_validator, model_validator)

from .domain import RuleError

Nonnegative = Annotated[StrictInt, Field(ge=0)]
BasisPoints = Annotated[StrictInt, Field(ge=0, le=10000)]
Text = Annotated[str, StringConstraints(min_length=1)]


class Schema(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class Dated(Schema):
    @field_validator('valid_from', 'valid_to', 'effective_from', 'effective_to', check_fields=False)
    @classmethod
    def iso_date(cls, value):
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError('Use an ISO date in YYYY-MM-DD format')
        return value


class Discount(Schema):
    type: Literal['percent', 'flat']
    value_bps: BasisPoints | None = None
    value_bps_max: BasisPoints | None = None
    value_paise: Nonnegative | None = None
    on: Literal['lowest_priced_lens_line'] | None = None

    @model_validator(mode='after')
    def appropriate_values(self):
        if self.type == 'percent':
            if self.value_paise is not None or (self.value_bps is None) == (self.value_bps_max is None):
                raise ValueError('Percent discount requires exactly one of value_bps or value_bps_max')
        elif self.value_paise is None or self.value_bps is not None or self.value_bps_max is not None:
            raise ValueError('Flat discount requires value_paise only')
        return self


class Condition(Schema):
    min_pairs_in_cart: Nonnegative | None = None
    customer_tag: Text | None = None
    lens_type: Text | None = None
    index_min: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None
    coating: Text | None = None
    has_frame_and_lens: StrictBool | None = None


class Authorization(Schema):
    role: Literal['store_manager']
    reason_code: Literal[True]

    @field_validator('reason_code', mode='before')
    @classmethod
    def strict_true(cls, value):
        if value is not True:
            raise ValueError('Manager authorization requires reason_code: true')
        return value


class OfferRule(Schema):
    applies_to: Literal['frame', 'lens', 'pair', 'cart']
    discount: Discount
    condition: Condition | None = None
    max_discount_paise: Nonnegative | None = None
    requires: Authorization | None = None

    @model_validator(mode='after')
    def supported_combinations(self):
        if self.discount.on and self.applies_to != 'lens':
            raise ValueError('lowest_priced_lens_line requires lens scope')
        if self.condition and self.condition.has_frame_and_lens is not None and self.applies_to != 'pair':
            raise ValueError('has_frame_and_lens requires pair scope')
        if self.discount.value_bps_max is not None and self.requires is None:
            raise ValueError('Discretionary percentages require manager authorization')
        return self


class Offer(Dated):
    offer_id: Text
    name: Text
    active: StrictBool
    valid_from: str
    valid_to: str
    stackable: StrictBool
    priority: Nonnegative
    rule: OfferRule

    @model_validator(mode='after')
    def valid_dates_and_authorization(self):
        if self.valid_from > self.valid_to:
            raise ValueError('valid_from must be on or before valid_to')
        if self.offer_id == 'MANAGER_OVERRIDE':
            if self.rule.requires is None or self.rule.discount.value_bps_max is None:
                raise ValueError('MANAGER_OVERRIDE requires authorization and value_bps_max')
        elif self.rule.requires is not None or self.rule.discount.value_bps_max is not None:
            raise ValueError('Only MANAGER_OVERRIDE supports discretionary authorization')
        return self


class TaxRate(Dated):
    hsn: Text
    description: str | None = None
    rate_bps: Nonnegative
    effective_from: str
    effective_to: str | None = None

    @model_validator(mode='after')
    def valid_dates(self):
        if self.effective_to is not None and self.effective_from > self.effective_to:
            raise ValueError('effective_from must be on or before effective_to')
        return self


class TaxConfiguration(Schema):
    about: str | None = None
    rates: Annotated[list[TaxRate], Field(min_length=1)]

    @model_validator(mode='after')
    def unique_effective_dates(self):
        keys = [(r.hsn, r.effective_from) for r in self.rates]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate effective date for HSN')
        return self


def validate_offer(value):
    try:
        return Offer.model_validate(value).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise RuleError('invalid_configuration', f'Invalid offer configuration: {exc}') from exc


def load_configuration(directory: Path):
    try:
        raw = json.loads((directory/'offers.json').read_text())
        if not isinstance(raw, list):
            raise ValueError('offers.json must contain a list')
        offers = [validate_offer(o) for o in raw]
        ids = [o['offer_id'] for o in offers]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate offer IDs')
        tax = TaxConfiguration.model_validate(json.loads((directory/'tax_rates.json').read_text()))
        return offers, [r.model_dump(exclude_none=True) for r in tax.rates]
    except (OSError, ValueError, ValidationError) as exc:
        raise RuleError('invalid_configuration', f'Invalid pricing configuration: {exc}') from exc
