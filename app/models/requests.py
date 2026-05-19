from pydantic import BaseModel, field_validator
from typing import Literal

AGE_GROUPS = ["neonate", "infant", "pediatric", "adolescent", "adult", "geriatric", "any"]


class DrugRequest(BaseModel):
    drug_id_1mg: str
    age_group: str = "adult"

    @field_validator("age_group")
    @classmethod
    def validate_age_group(cls, v):
        if v not in AGE_GROUPS:
            raise ValueError(f"age_group must be one of: {', '.join(AGE_GROUPS)}")
        return v
