"""OpenTelemetry SDK setup for infrastructure and manually-created Agent spans."""

import json
from typing import Literal
from urllib.parse import unquote

import structlog
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from app.core.config import settings
from app.core.database import engine

log = structlog.get_logger()
_provider: TracerProvider | None = None
_instrumented = False


def _otlp_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.rstrip("/")
    return normalized if normalized.endswith("/v1/traces") else f"{normalized}/v1/traces"


def _otlp_headers(value: str) -> dict[str, str]:
    if not value.strip():
        return {}
    if value.lstrip().startswith("{"):
        parsed = json.loads(value)
        return {str(key): str(item) for key, item in parsed.items()}
    headers = {}
    for item in value.split(","):
        key, separator, header_value = item.partition("=")
        if separator and key.strip():
            headers[unquote(key.strip())] = unquote(header_value.strip())
    return headers


def setup_telemetry(app: FastAPI) -> None:
    global _instrumented, _provider
    if not settings.observability_enabled or _instrumented:
        return

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.otel_service_name,
                "service.version": app.version,
                "deployment.environment.name": settings.environment,
            }
        ),
        sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio)),
    )
    exporter: Literal["none", "otlp", "console"] = settings.otel_traces_exporter
    if exporter == "otlp":
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=_otlp_endpoint(settings.otel_exporter_otlp_endpoint),
                    headers=_otlp_headers(settings.otel_exporter_otlp_headers),
                )
            )
        )
    elif exporter == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=r".*/health(?:/.*)?$",
        exclude_spans=["receive", "send"],
    )
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=provider)
    _provider = provider
    _instrumented = True
    log.info(
        "telemetry.configured",
        exporter=exporter,
        service=settings.otel_service_name,
        sample_ratio=settings.otel_sample_ratio,
    )


def shutdown_telemetry() -> None:
    if _provider is not None:
        _provider.shutdown()


def tracer():
    return trace.get_tracer("teacher-agent.agents", "0.1.0")
