"""Base of the request bodies of the core, CRM and communication endpoints."""

from pydantic import BaseModel, ConfigDict


# Unknown fields are rejected and surrounding whitespace is stripped. (A docstring would become the
# schema description in OpenAPI.)
class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
