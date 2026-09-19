# Контракт API v1

База `/api/v1`; OpenAPI `/openapi.json`, интерактивная справка `/api/docs`. JSON. Количества, цены и курсы — decimal strings. UTC ISO8601 в БД; UI Europe/Moscow по настройке.

Ошибки: `{code,message,field,requestId}`; 401 сессия, 403 разрешение, 404 недоступный объект, 409 устаревшая версия/повтор с иными данными, 422 предметная валидация. POST/PATCH require X-CSRF-Token кроме входа. Критические команды требуют `Idempotency-Key`; повтор возвращает прежний результат. Изменения передают `version`.

Списки возвращают `{items:[],total,page,page_size}`. Параметры: page=1,page_size=25,q,sort,direction; page_size<=100. GET detail возвращает объект. Создание возвращает объект с id,created_at,version.

| Операция | Контракт |
|---|---|
| POST /auth/login | email,password,otp? → user,csrf_token |
| GET /auth/me | user,csrf_token,permissions |
| POST /auth/logout | завершить сеанс |
| GET/POST /counterparties | name,kind(client/supplier/both),country?,tax_id?,email?,phone?,owner_id?,details? |
| PATCH /counterparties/{id} | поля + version |
| GET/POST /counterparties/{id}/contacts | name,position?,email?,phone? |
| GET/POST /calls | client_id,contact_id?,result,comment?,next_at?,next_assignee_id?,reason? |
| GET/POST /tasks | title,entity_type?,entity_id?,assignee_id?,due_at,priority? |
| PATCH /tasks/{id} | status,title,due_at,result + version |
| GET/POST /requests | title,client_id,seller_id?,contact_id?,source_call_id?,owner_id?,due_at?,is_test? |
| GET/PATCH /requests/{id} | объект / поля+version; commercial_stage через проверку переходов |
| GET/POST /requests/{id}/items | description,cas?,quantity?,unit?,purity?,packaging?,allow_analogue? |
| PATCH /request-items/{id} | поля+version+reason; создаёт редакцию |
| GET /requests/{id}/history | доступные события |
| GET/POST /sellers | name,currency,details |
| GET/POST /settings | настраиваемые значения |
| GET/POST /admin/users | приглашённые пользователи; email,name,password,role_ids |
| PATCH /admin/users/{id} | active,name,role_ids,version,reassign_to?,reason |
| GET/POST /admin/roles | name,grants:[{code,scope,allow}] |
| GET /admin/permissions | каталог кодов |
| GET /admin/audit | журнал с фильтром entity_id |
| GET /analytics/dashboard | from?,to?,owner_id?,include_test=false |
| GET /notifications | уведомления текущего пользователя |
| POST /notifications/{id}/read | прочитать |
| GET/POST /chats | title,kind(direct/group/request/wave),member_ids,entity_id? |
| GET/POST /chats/{id}/messages | текст,after?; content,file_ids?; Idempotency-Key |
| POST /files | multipart file,entity_type,entity_id,classification |
| GET /files/{id}/download | проверка доступа и сканирования |
| POST /imports/preview | multipart XLSX + mapping JSON; только preview |
| POST /imports/{id}/confirm | decisions? + Idempotency-Key |
| GET /imports/{id}/errors.xlsx | XLSX ошибок |

Коммерческие операции документируются в commerce-api.md до реализации экранов. Структуры данных и ER: data-model.md. Машиночитаемая спецификация выгружается из фактических Pydantic-схем в docs/openapi.json и сверяется в CI.
