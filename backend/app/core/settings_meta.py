from fastapi import APIRouter
from app.core.settings_registry import SETTING_META

router = APIRouter()

@router.get("/settings/meta")
def get_settings_meta():
    res = {}
    for key, meta in SETTING_META.items():
        res[key] = {
            "consumer": meta.consumer,
            "effect": meta.effect,
            "is_supported": meta.is_supported,
            "schema": meta.schema.model_json_schema()
        }
    return res
