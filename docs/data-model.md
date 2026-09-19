# Модель данных до реализации UI

Технический ключ всех сущностей UUID (строковое представление), created_at timestamptz, version int. Деньги numeric(24,8), количества numeric(24,6). У каждого изменения mutable объекта проверяется version.

```mermaid
erDiagram
  USER ||--o{ USER_ROLE : has
  ROLE ||--o{ USER_ROLE : assigns
  ROLE ||--o{ PERMISSION_GRANT : grants
  USER ||--o{ SESSION : authenticates
  USER ||--o{ PERMISSION_GRANT : override
  SELLER ||--o{ REQUEST : sells
  COUNTERPARTY ||--o{ CONTACT : contacts
  COUNTERPARTY ||--o{ CALL : history
  COUNTERPARTY ||--o{ REQUEST : requests
  REQUEST ||--o{ REQUEST_MEMBER : shares
  REQUEST ||--o{ REQUEST_ITEM : needs
  REQUEST_ITEM ||--o{ ITEM_REVISION : versions
  REQUEST_ITEM ||--o{ QUOTE : offers
  SUBSTANCE ||--o{ PRODUCT : variants
  MANUFACTURER ||--o{ PRODUCT : makes
  PRODUCT ||--o{ QUOTE : quoted
  COUNTERPARTY ||--o{ QUOTE : supplies
  QUOTE ||--o{ QUOTE_VERSION : versions
  REQUEST ||--o{ RFQ : asks
  PROFILE ||--o{ CALCULATION : algorithm
  CALCULATION ||--o{ CALCULATION_LINE : snapshot
  QUOTE_VERSION ||--o{ CALCULATION_LINE : source
  CALCULATION ||--o{ PROPOSAL : offers
  PROPOSAL ||--o{ PROPOSAL_LINE : contains
  PROPOSAL_LINE ||--o{ EXECUTION : accepted
  PROPOSAL ||--o{ INVOICE : billed
  INVOICE ||--o{ INVOICE_LINE : contains
  PAYMENT ||--o{ PAYMENT_ALLOCATION : allocates
  INVOICE ||--o{ PAYMENT_ALLOCATION : receives
  PAYMENT ||--o{ PAYMENT_REVERSAL : corrects
  EXECUTION ||--o{ APPROVAL : submits
  APPROVAL ||--o{ APPROVAL_DECISION : decides
  EXECUTION ||--o{ WAVE_ALLOCATION : schedules
  WAVE ||--o{ WAVE_ALLOCATION : groups
  WAVE_ALLOCATION ||--o{ FULFILLMENT_EVENT : moves
  CHAT ||--o{ CHAT_MEMBER : restricts
  CHAT ||--o{ MESSAGE : contains
  FILE ||--o{ MESSAGE_FILE : attached
  MESSAGE ||--o{ MESSAGE_FILE : attaches
  USER ||--o{ NOTIFICATION : receives
  USER ||--o{ AUDIT_EVENT : authors
  USER ||--o{ TASK : assigned
  IMPORT_BATCH ||--o{ IMPORT_ROW : previews
```

Снимки расчётов, документов, редакций квот/потребностей, платёжные события и аудит не удаляются и не меняются штатным API. Связи документов ведут к конкретной версии; counterparty.details и seller.details копируются при выпуске. Общие суммы и количества защищаются транзакционными родительскими блокировками и проверками остатков; FK и CHECK защищают структуру. Персональные и ролевые разрешения хранятся отдельно. Поставщик — роль контрагента, производитель — отдельная сущность. Товар идентифицируется составным fingerprint, не поставщиком и не одним CAS.
