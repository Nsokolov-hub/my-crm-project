"""Request body base of the core, CRM and communication endpoints, and the typed core responses.

Response models are part of the API contract: docs/openapi.json documents them and the frontend
generates its TypeScript types from it (frontend/src/lib/api-schema.d.ts).
"""

from pydantic import BaseModel, ConfigDict


# Unknown fields are rejected and surrounding whitespace is stripped. (A docstring would become the
# schema description in OpenAPI.)
class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RoleRef(BaseModel):
    id: str
    name: str


class GrantView(BaseModel):
    code: str
    scope: str
    allow: bool


class UserView(BaseModel):
    """An employee with roles, personal grants and the effective scope of each permission."""

    id: str
    email: str
    name: str
    active: bool
    own_requests_only: bool
    version: int
    mfa_enabled: bool
    roles: list[RoleRef]
    grants: list[GrantView]
    permissions: dict[str, str]


class UserPage(BaseModel):
    items: list[UserView]
    total: int
    page: int
    page_size: int


class SessionView(BaseModel):
    """The signed-in user; the CSRF token goes into the X-CSRF-Token header of every command."""

    user: UserView
    csrf_token: str
    permissions: dict[str, str]
