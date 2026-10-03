"""Explicitly approved HTTPS for managed identity/provider requests.

No environment proxy, redirect following, ambient credential forwarding, or
automatic retry. A fresh quota reservation is required for each provider attempt.
This is a host-service transport, not an OS firewall for project shell commands.
"""

import http.client
import json
from pathlib import Path
import socket
import ssl

from .enterprise_policy import _url


def _tls_context():
    # Use the interpreter's installed trust store, not SSL_CERT_FILE/SSL_CERT_DIR
    # inherited from a caller. IT must protect both interpreter and CA store.
    defaults = ssl.get_default_verify_paths()
    cafile = defaults.openssl_cafile if defaults.openssl_cafile and Path(defaults.openssl_cafile).is_file() else None
    capath = defaults.openssl_capath if defaults.openssl_capath and Path(defaults.openssl_capath).is_dir() else None
    if cafile is None and capath is None:
        raise RuntimeError("IT must provision the managed interpreter's TLS trust store.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=cafile, capath=capath)
    return context


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address, *, context, timeout):
        super().__init__(host, port=port, context=context, timeout=timeout)
        self.address = address

    def connect(self):
        # Connect to the address resolved for this request, with the approved host
        # still used for certificate verification and SNI. Never resolve it again.
        self.sock = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)
        except BaseException:
            self.sock.close()
            self.sock = None
            raise


class ApprovedHTTPS:
    def __init__(self, policy, *, timeout=120, max_response_bytes=16_777_216):
        self.policy = policy
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def request(self, method, url, body=None, *, headers=None, accepted_status=(200,), reader=None):
        self.policy.allow_url(url)  # Before DNS, sockets, or credential-bearing bytes.
        host, port, path = _url(url)
        if method not in {"GET", "POST"}:
            raise ValueError("Managed transport permits only GET and POST.")
        provided = dict(headers or {})
        if any(name.lower() in {"host", "proxy-authorization", "connection", "transfer-encoding", "content-length"}
               for name in provided):
            raise ValueError("Managed transport does not accept routing or framing header overrides.")
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses:
            raise RuntimeError("Approved destination did not resolve.")
        context = _tls_context()
        connection = _PinnedHTTPS(host, port, addresses[0][4][0], context=context, timeout=self.timeout)
        try:
            connection.request(method, path, body=body, headers=provided)
            response = connection.getresponse()
            # Never disclose a response body in errors; it can contain reflected secrets.
            if 300 <= response.status < 400:
                raise PermissionError("Managed HTTPS refuses redirects; IT must approve and configure the final URL.")
            if response.status not in accepted_status:
                raise RuntimeError(f"Approved endpoint returned HTTP {response.status}.")
            if reader is not None:
                if response.headers.get_content_type() != "text/event-stream":
                    raise RuntimeError("Approved endpoint did not return an SSE response.")
                return reader(response)
            result = response.read(self.max_response_bytes + 1)
            if len(result) > self.max_response_bytes:
                raise RuntimeError("Approved endpoint response exceeded its size limit.")
            return result
        finally:
            connection.close()

    def json(self, method, url, payload=None, *, headers=None):
        body = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
        supplied = {"Content-Type": "application/json", **(headers or {})}
        return json.loads(self.request(method, url, body, headers=supplied))

    def stream_json(self, url, payload, on_delta, *, headers=None):
        from .provider import read_stream
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        supplied = {"Content-Type": "application/json", "Accept": "text/event-stream", **(headers or {})}
        return self.request("POST", url, body, headers=supplied,
                            reader=lambda response: read_stream(response, on_delta, self.max_response_bytes))
