from pydantic import BaseModel, AwareDatetime
class Model(BaseModel):
    dt: AwareDatetime
try:
    Model.model_validate({"dt": "2024-01-01T12:00:00"})
    print("Unaware allowed?")
except Exception as e:
    print("Unaware error:", e)

try:
    m = Model.model_validate({"dt": "2024-01-01T12:00:00Z"})
    print("Aware allowed:", m.dt)
except Exception as e:
    print("Aware error:", e)
