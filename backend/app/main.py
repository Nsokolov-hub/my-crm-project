import json
import logging
import re
import time
from importlib import import_module
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.errors import DomainError
from app.core.service import request_id

logger = logging.getLogger('crm')
logging.basicConfig(level=logging.INFO, format='%(message)s')
REQUESTS = Counter('crm_http_requests_total', 'HTTP requests', ['method', 'route', 'status'])
LATENCY = Histogram('crm_http_duration_seconds', 'API latency', ['method', 'route'])

app = FastAPI(title='Реактив CRM', version='0.1.0', docs_url='/api/docs', redoc_url=None, description='CRM продаж и закупок химической продукции. Настраиваемые финансовые профили, права и шаблоны. Количества и деньги — десятичные строки.', responses={401: {'description': 'Сеанс отсутствует или отозван'}, 403: {'description': 'Недостаточно прав'}, 409: {'description': 'Конфликт версии или повторяемости'}, 422: {'description': 'Ошибка предметной валидации'}})
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins.split(','), allow_credentials=True, allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], allow_headers=['Content-Type', 'X-CSRF-Token', 'Idempotency-Key'], expose_headers=['X-Request-ID', 'Content-Disposition'])


@app.middleware('http')
async def context(request: Request, call_next):
    correlation = request.headers.get('X-Request-ID', '')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', correlation):
        correlation = str(uuid4())
    token = request_id.set(correlation)
    request.state.request_id = correlation
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.error(json.dumps({'event': 'unhandled_error', 'requestId': correlation}))
        response = JSONResponse({'code': 'INTERNAL_ERROR', 'message': 'Операция не выполнена. Передайте код ошибки ответственному за сопровождение.', 'field': None, 'requestId': correlation}, status_code=500)
    duration = time.perf_counter() - start
    route = getattr(request.scope.get('route'), 'path', 'unmatched')
    REQUESTS.labels(request.method, route, response.status_code).inc()
    LATENCY.labels(request.method, route).observe(duration)
    response.headers.update({'X-Request-ID': correlation, 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'same-origin', 'X-Frame-Options': 'DENY', 'Cache-Control': 'no-store'})
    if settings.environment == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    logger.info(json.dumps({'event': 'http_request', 'requestId': correlation, 'method': request.method, 'route': route, 'status': response.status_code, 'duration_ms': round(duration * 1000, 2)}))
    request_id.reset(token)
    return response


@app.exception_handler(DomainError)
async def business_error(request: Request, exc: DomainError):
    return JSONResponse({'code': exc.code, 'message': exc.message, 'field': exc.field, 'requestId': request.state.request_id}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    fields = [{'field': '.'.join(str(x) for x in error['loc'][1:]), 'message': error['msg']} for error in exc.errors()]
    return JSONResponse({'code': 'VALIDATION_ERROR', 'message': 'Проверьте отмеченные поля формы', 'field': fields[0]['field'] if fields else None, 'fields': fields, 'requestId': request.state.request_id}, status_code=422)


@app.exception_handler(IntegrityError)
async def constraint_error(request: Request, exc: IntegrityError):
    return JSONResponse({'code': 'DATA_CONFLICT', 'message': 'Данные уже существуют или нарушают ограничения. Обновите запись и проверьте связанные объекты.', 'field': None, 'requestId': request.state.request_id}, status_code=409)


@app.exception_handler(OperationalError)
async def transaction_error(request: Request, exc: OperationalError):
    return JSONResponse({'code': 'TRANSACTION_RETRY', 'message': 'Операция временно недоступна. Повторите её с тем же ключом.', 'field': None, 'requestId': request.state.request_id}, status_code=503)


@app.get('/health', include_in_schema=False)
def health():
    return {'status': 'ok'}


@app.get('/ready', include_in_schema=False)
def ready():
    try:
        with SessionLocal() as db:
            db.execute(text('SELECT 1'))
        return {'status': 'ready'}
    except Exception:
        return JSONResponse({'status': 'not_ready'}, status_code=503)


@app.get('/metrics', include_in_schema=False)
def metrics(request: Request):
    # Network-only metrics. Public production reverse proxy does not expose this route.
    if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
        return Response(status_code=404)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


for module in ['core.routes', 'crm.routes', 'crm.imports', 'analytics.routes', 'commerce.routes', 'communication.routes']:
    app.include_router(import_module(f'app.{module}').router, prefix='/api/v1')
