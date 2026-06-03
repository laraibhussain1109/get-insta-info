from __future__ import annotations

import asyncio
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.models import BatchRequest, ValidationError
from app.providers import MockProvider, PublicInstagramCrawlerProvider
from app.repositories import InsightsRepository
from app.services.estimator import InsightsEstimator
from app.services.orchestrator import InsightsOrchestrator

_repository = InsightsRepository(ttl_hours=24)
_estimator = InsightsEstimator()


def build_provider() -> MockProvider | PublicInstagramCrawlerProvider:
    provider_name = os.getenv("INSIGHTS_PROVIDER", "crawler").lower()
    if provider_name == "mock":
        return MockProvider()
    return PublicInstagramCrawlerProvider()


_provider = build_provider()
_orchestrator = InsightsOrchestrator(_provider, _estimator, _repository)


def get_orchestrator() -> InsightsOrchestrator:
    return _orchestrator


class InsightsRequestHandler(BaseHTTPRequestHandler):
    server_version = "InstagramInsightsAPI/0.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/healthz":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) in {3, 4} and parts[:2] == ["v1", "profiles"]:
            username = parts[2]
            if len(parts) == 4 and parts[3] != "insights":
                self._write_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
                return
            try:
                window = int(query.get("window", ["30"])[0])
            except ValueError:
                self._write_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": "window must be an integer"})
                return
            refresh = query.get("refresh", ["false"])[0].lower() == "true"
            self._run_insights(username, window, refresh)
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/v1/profiles/batch":
            self._write_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length) or b"{}")
            request = BatchRequest(usernames=payload.get("usernames", []), window=payload.get("window", 30))
            response = asyncio.run(get_orchestrator().create_batch(request))
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            self._write_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": str(exc)})
            return

        self._write_json(HTTPStatus.ACCEPTED, response.to_dict())

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _run_insights(self, username: str, window: int, refresh: bool) -> None:
        try:
            insights = asyncio.run(get_orchestrator().get_insights(username, window, refresh=refresh))
        except ValidationError as exc:
            self._write_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": str(exc)})
            return
        except RuntimeError as exc:
            self._write_json(HTTPStatus.BAD_GATEWAY, {"detail": str(exc)})
            return
        self._write_json(HTTPStatus.OK, insights.to_dict())

    def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_server(host: str = "0.0.0.0", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), InsightsRequestHandler)


def main() -> None:
    server = create_server()
    print("Serving on http://0.0.0.0:8000")
    server.serve_forever()


if __name__ == "__main__":
    main()
