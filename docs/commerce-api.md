# Commerce API and invariants

Requirements: F05–F13, N02–N04, A03–A18, A20. V02–V08 are implemented as user-created, versioned profiles and policies under the explicit user decision of 13 September 2026. No real tax rates or company details are seeded.

All paths start with `/api/v1`. Command bodies carry `idempotency_key`; mutable commands also carry `version`. Decimal numbers are JSON strings. Lists return `{items: [...]}`. Access checks run per request and financial fields are stripped unless explicitly permitted.

| Domain | Endpoints |
| --- | --- |
| Catalog | GET /catalog/products, POST /catalog/products, POST /catalog/products/{id}/verify |
| Quotes | GET/POST /requests/{id}/quotes; POST /quotes/{id}/revise |
| Supplier requests | GET/POST /requests/{id}/rfqs; GET /rfqs/{id}/file; POST /rfqs/{id}/sent |
| Profiles | GET/POST /profiles; GET /profiles/example (synthetic, never activated automatically) |
| Calculation | POST /requests/{id}/calculations/preview; GET/POST /requests/{id}/calculations |
| Proposal | GET/POST /requests/{id}/proposals; POST /proposals/{id}/accept; POST /proposals/{id}/sent |
| Invoice | GET/POST /requests/{id}/invoices; GET /documents/{id}/file?format=pdf or xlsx |
| Payments | GET/POST /requests/{id}/payments; POST /payments/{id}/confirm; POST /payments/{id}/allocate; POST /payments/{id}/reverse |
| Execution | GET /requests/{id}/executions; GET/POST /requests/{id}/approvals; POST /approvals/{id}/decision |
| Waves | GET/POST /waves; PATCH /waves/{id}; POST /waves/{id}/allocations; POST /allocations/{id}/transfer; POST /allocations/{id}/events |

Profiles contain versioned safe arithmetic formulas (`+ - * /`, Decimal constants, named previous outputs; no calls, attributes or arbitrary code), explicit currency precision, rounding, validity dates, tax profile fields, funding ratio and document template. Exact settings schema is published in OpenAPI. Calculations snapshot all inputs and results. Profile examples are arithmetic demonstrations only; operational use requires the user to create their own profile.

Product fingerprints include substance, manufacturer, article, purity, packaging and unit, never supplier. Unique constraints and transaction advisory locks serialize deduplication. CAS validates syntax/checksum. Unit conversion stays within mass, volume or count dimensions unless an explicit supported conversion factor and basis are supplied.

Accepted quantities never exceed request demand. Invoice quantities never exceed accepted unbilled quantities. Payments are declared then confirmed; only confirmed allocations reduce balances. Reversal is append-only and flags financing deficits. Approval is bound to execution revision and funding profile; quantities in active waves cannot exceed approved quantities. A shipped allocation cannot transfer; delivery cannot exceed shipment. Document files and snapshots are generated once and checksum-verified on download.
