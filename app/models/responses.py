from pydantic import BaseModel
from typing import Any, Optional, Dict, List


class MetaResponse(BaseModel):
    source: str
    cached: bool
    response_time_ms: float
    product_count: Optional[int] = None


class DrugResponse(BaseModel):
    success: bool
    drug_id_1mg: str
    generic_name: Optional[str] = None
    data: Any
    meta: MetaResponse


class ErrorResponse(BaseModel):
    success: bool = False
    error_code: str
    message: str
    request_id: str


class TableData(BaseModel):
    caption: Optional[str] = None
    headers: Optional[List[str]] = None
    rows: Optional[List[List[str]]] = None


class SubSection(BaseModel):
    section_title: Optional[str] = None
    content: Optional[str] = None


class LabelData(BaseModel):
    text: Optional[str] = None
    table: Optional[List[TableData]] = None
    subsections: Optional[List[SubSection]] = None


class ProductRow(BaseModel):
    generic_name: str = ""
    dosage_form: Optional[str] = None
    route_of_administration: Optional[str] = None
    color: Optional[str] = None
    shape: Optional[str] = None
    imprint: Optional[str] = None
    size_mm: Optional[str] = None


class ActiveIngredientItem(BaseModel):
    name: Optional[str] = None
    strength: Optional[str] = None


class ProductIngredients(BaseModel):
    product: str = ""
    ingredients: List[ActiveIngredientItem] = []


class InactiveProductIngredients(BaseModel):
    product: str = ""
    ingredients: List[str] = []


class IngredientsData(BaseModel):
    active: List[ProductIngredients] = []
    inactive: List[InactiveProductIngredients] = []
