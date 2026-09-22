from pydantic import BaseModel, Field
class Patch(BaseModel):
    name: str = Field(default=None) # type is str, not str|None

try:
    p = Patch.model_validate({"name": None})
    print("Allowed null:", p)
except Exception as e:
    print("Error:", e)

try:
    p = Patch.model_validate({})
    print("Omitted:", p, p.model_dump(exclude_unset=True))
except Exception as e:
    print("Error:", e)
