import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse


class LocalTestResponse:
    """Lightweight test response object matching requests/httpx Response interface."""

    def __init__(self, status_code: int, content: bytes, headers: List[Tuple[bytes, bytes]]):
        self.status_code = status_code
        self.content = content
        self.headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in headers}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.text)


class LocalTestClient:
    """
    Offline ASGI in-process test client for FastAPI.
    Executes HTTP requests directly against FastAPI/Starlette ASGI applications
    without requiring external network packages like httpx.
    """

    def __init__(self, app: Any):
        self.app = app

    def request(
        self,
        method: str,
        path: str,
        json_data: Optional[Any] = None,
        data: Optional[bytes] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> LocalTestResponse:
        query_string = b""
        if params:
            query_string = urllib.parse.urlencode(params).encode("utf-8")
        elif "?" in path:
            path, qs = path.split("?", 1)
            query_string = qs.encode("utf-8")

        raw_headers: List[Tuple[bytes, bytes]] = []
        if headers:
            for k, v in headers.items():
                raw_headers.append((k.lower().encode("latin1"), v.encode("latin1")))

        body = b""
        if json_data is not None:
            body = json.dumps(json_data).encode("utf-8")
            raw_headers.append((b"content-type", b"application/json"))
            raw_headers.append((b"content-length", str(len(body)).encode("ascii")))
        elif data is not None:
            body = data
            raw_headers.append((b"content-length", str(len(body)).encode("ascii")))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string,
            "headers": raw_headers,
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }

        response_status: int = 500
        response_headers: List[Tuple[bytes, bytes]] = []
        response_body: List[bytes] = []

        async def receive() -> Dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: Dict[str, Any]) -> None:
            nonlocal response_status, response_headers, response_body
            if message["type"] == "http.response.start":
                response_status = message["status"]
                response_headers = message.get("headers", [])
            elif message["type"] == "http.response.body":
                response_body.append(message.get("body", b""))

        asyncio.run(self.app(scope, receive, send))

        return LocalTestResponse(response_status, b"".join(response_body), response_headers)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> LocalTestResponse:
        return self.request("GET", path, params=params, headers=headers)

    def post(self, path: str, json: Optional[Any] = None, data: Optional[bytes] = None, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> LocalTestResponse:
        return self.request("POST", path, json_data=json, data=data, params=params, headers=headers)

    def put(self, path: str, json: Optional[Any] = None, params: Optional[Dict[str, Any]] = None) -> LocalTestResponse:
        return self.request("PUT", path, json_data=json, params=params)

    def delete(self, path: str, params: Optional[Dict[str, Any]] = None) -> LocalTestResponse:
        return self.request("DELETE", path, params=params)
