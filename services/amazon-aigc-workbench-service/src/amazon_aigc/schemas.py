from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Component(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(default="", max_length=2000)
    specification: str = Field(min_length=1, max_length=2000)
    quantity: float = Field(gt=0, le=100000)
    unit: str = Field(min_length=1, max_length=30)
    confirmed: bool = False
    source_id: str = Field(default="", max_length=80)
    supplier_sku_id: str = Field(default="", max_length=80)
    image_asset_ids: list[str] = Field(default_factory=list, max_length=5)


class Facts(StrictModel):
    product_type: Literal["ordinary", "bundle"] = "ordinary"
    sales_name: str = Field(min_length=1, max_length=300)
    quantity: str = Field(min_length=1, max_length=500)
    specification: str = Field(default="", max_length=2000)
    unit: str = Field(default="", max_length=30)
    source_text: str = Field(default="", max_length=12000)
    verification_basis: str = Field(default="", max_length=4000)
    packaging_dimensions: str = Field(default="", max_length=1000)
    material: str = Field(default="", max_length=2000)
    dimensions: str = Field(default="", max_length=2000)
    included: str = Field(min_length=1, max_length=4000)
    excluded: str = Field(default="", max_length=4000)
    uses: str = Field(default="", max_length=4000)
    components: list[Component] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def check_components(self):
        if self.product_type == "bundle" and (not self.components or not all(x.confirmed for x in self.components)):
            raise ValueError("组合商品须逐项确认组成、规格与数量")
        if len({x.id for x in self.components}) != len(self.components):
            raise ValueError("组成项ID不能重复")
        return self


class Listing(StrictModel):
    title: str = Field(min_length=1, max_length=75)
    highlights: str = Field(min_length=1, max_length=125)
    bullets: list[str] = Field(min_length=5, max_length=5)
    description: str = Field(min_length=1, max_length=12000)
    terms: str = Field(max_length=249)
    # Read/write compatibility for historical versions only; not generated or shown in the editor.
    translations: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_copy(self):
        if any(not b.strip() or len(b) > 2000 for b in self.bullets):
            raise ValueError("请填写五条完整描述")
        if len(self.terms.encode()) > 249:
            raise ValueError("Search Terms不能超过249字节")
        return self


class ImagePlan(StrictModel):
    purpose: str = Field(min_length=1, max_length=200)
    composition: str = Field(min_length=1, max_length=4000)
    text: str = Field(default="", max_length=2000)


class Brief(StrictModel):
    product: str = Field(min_length=1, max_length=5000)
    instructions: str = Field(default="", max_length=5000)
    selling_points: str = Field(min_length=1, max_length=5000)
    visual_focus: str = Field(min_length=1, max_length=5000)
    requirements: str = Field(default="", max_length=5000)
    references: list[str] = Field(default_factory=list, max_length=100)
    # Count-only versions remain readable; edited plans define the delivery count.
    plans: list[ImagePlan] = Field(default_factory=list, max_length=12)
    image_count: int | None = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def resolve_count(self):
        if self.plans or self.image_count is None:
            self.image_count = len(self.plans) or 7
        return self


class DocumentWrite(StrictModel):
    revision: int = Field(ge=0)
    section: Literal["facts", "listing", "brief", "keywords", "sources", "selling_points"]
    body: dict | list | str
    confirm: bool = False


class Command(StrictModel):
    revision: int = Field(ge=0)
    data: dict = Field(default_factory=dict)


class JobRequest(StrictModel):
    kind: Literal["source", "model"]
    revision: int = Field(ge=0)
    payload: dict
