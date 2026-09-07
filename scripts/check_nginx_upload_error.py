import http.client
import json

HOST = "127.0.0.1"
PORT = 18081
ALLOWED_ORIGIN = "https://upload-ui.example"
DENIED_ORIGIN = "https://untrusted.example"
INTERNAL_PATH = "/api/v1/_internal/upload-too-large"


def check_oversized_request(origin: str | None) -> None:
    connection = http.client.HTTPConnection(HOST, PORT, timeout=10)

    try:
        connection.putrequest("POST", "/api/v1/documents")
        connection.putheader("Content-Length", str(33 * 1024 * 1024))
        connection.putheader(
            "Content-Type",
            "multipart/form-data; boundary=synthetic267",
        )
        connection.putheader("Expect", "100-continue")
        connection.putheader("X-Trace-Id", "untrusted-client-trace")

        if origin is not None:
            connection.putheader("Origin", origin)

        connection.endheaders()

        # Content-Length만으로 제한 초과를 판단할 수 있으므로,
        # 파일 본문을 보내지 않고 오류 응답을 확인합니다.
        response = connection.getresponse()
        payload = response.read()
        assert response.status == 400, f"Expected 400, got {response.status}: {payload[:300]!r}"

        body = json.loads(payload)
        assert set(body) == {"code", "message", "details", "trace_id"}
        assert body["code"] == "UPLOAD_FILE_TOO_LARGE"
        assert body["message"] == "파일 크기는 30MB 이하만 업로드할 수 있습니다."
        assert body["details"] == [
            {
                "field": "file",
                "reason": "TOO_LARGE",
                "rejected_value": None,
            }
        ]

        trace_id = body["trace_id"]
        assert trace_id
        assert trace_id != "untrusted-client-trace"
        assert response.getheader("X-Trace-Id") == trace_id
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("Content-Type", "").startswith("application/json")

        if origin == ALLOWED_ORIGIN:
            assert response.getheader("Access-Control-Allow-Origin") == origin
            assert response.getheader("Access-Control-Allow-Credentials") == "true"
            assert "origin" in response.getheader("Vary", "").lower()
            assert "x-trace-id" in response.getheader("Access-Control-Expose-Headers", "").lower()
        else:
            assert response.getheader("Access-Control-Allow-Origin") is None

        print(f"PASS oversized request: origin={origin!r}")
    finally:
        connection.close()


def check_internal_path_is_blocked() -> None:
    connection = http.client.HTTPConnection(HOST, PORT, timeout=10)

    try:
        connection.request("GET", INTERNAL_PATH)
        response = connection.getresponse()
        response.read()
        assert response.status == 404
        print("PASS direct internal path access: 404")
    finally:
        connection.close()


def check_normal_proxy_request() -> None:
    connection = http.client.HTTPConnection(HOST, PORT, timeout=10)

    try:
        connection.request("GET", "/api/v1/nonexistent-upload-test-267")
        response = connection.getresponse()
        body = json.loads(response.read())

        assert response.status == 404
        assert body["code"] == "HTTP_ERROR"
        assert response.getheader("X-Trace-Id") == body["trace_id"]
        print("PASS normal request reaches Backend")
    finally:
        connection.close()


def main() -> None:
    for origin in (None, ALLOWED_ORIGIN, DENIED_ORIGIN):
        check_oversized_request(origin)

    check_internal_path_is_blocked()
    check_normal_proxy_request()


if __name__ == "__main__":
    main()
