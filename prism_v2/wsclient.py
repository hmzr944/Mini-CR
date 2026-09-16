"""Client WebSocket minimal (RFC 6455), stdlib pure.

Existe parce que V2 refuse les dependances tierces et que la mesure de
microstructure exige un flux temps reel : le REST ne donne ni sequence
continue, ni horodatage d'evenement, ni liquidations au fil de l'eau.

Traverse un proxy HTTP CONNECT quand HTTPS_PROXY est defini.

Ce module ne connait NI le marche NI les opportunites : il transporte des
trames et rapporte des horodatages. Toute interpretation est ailleurs.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlparse

OPCODE_CONT, OPCODE_TEXT, OPCODE_BIN = 0x0, 0x1, 0x2
OPCODE_CLOSE, OPCODE_PING, OPCODE_PONG = 0x8, 0x9, 0xA

#: OKX expose le flux public sur 8443 ET 443. Certains reseaux (dont des
#: proxys CONNECT) ne laissent passer que 443 : on essaie dans cet ordre.
OKX_PUBLIC_WS_ENDPOINTS = (("ws.okx.com", 443, "/ws/v5/public"),
                           ("ws.okx.com", 8443, "/ws/v5/public"))


class WebSocketError(RuntimeError):
    pass


class WebSocketClosed(WebSocketError):
    pass


@dataclass
class RawMessage:
    """Message brut + les deux horodatages qui permettent de mesurer le delai.

    `exchange_ts_ms` vient du payload (horloge de l'exchange).
    `local_recv_ts_ms` est pose a la reception de la trame (horloge locale).
    Leur difference est un DELAI DE TRANSPORT apparent, soumis a la derive
    des horloges : ce n'est PAS la latence aller-retour d'un ordre.
    """

    payload: Dict[str, Any]
    local_recv_ts_ms: int
    exchange_ts_ms: Optional[int] = None

    @property
    def transport_delay_ms(self) -> Optional[int]:
        if self.exchange_ts_ms is None:
            return None
        return self.local_recv_ts_ms - self.exchange_ts_ms


@dataclass
class ConnectionStats:
    connects: int = 0
    reconnects: int = 0
    messages: int = 0
    pings: int = 0
    errors: int = 0
    last_error: str = ""
    endpoint: str = ""
    connected_at_ms: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class WebSocketClient:
    """Client WS lecture seule. Aucune methode d'ordre, aucune authentification."""

    def __init__(self, host: str, port: int, path: str,
                 ca_bundle: Optional[str] = None, timeout: float = 30.0):
        self.host, self.port, self.path = host, port, path
        self.ca_bundle = ca_bundle or os.environ.get("SSL_CERT_FILE")
        self.timeout = timeout
        self._sock: Optional[ssl.SSLSocket] = None
        self._buf = b""
        self.stats = ConnectionStats(endpoint=f"{host}:{port}{path}")

    # ---- transport -----------------------------------------------------------
    def _raw_socket(self) -> socket.socket:
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if not proxy:
            return socket.create_connection((self.host, self.port), timeout=self.timeout)
        p = urlparse(proxy)
        s = socket.create_connection((p.hostname, p.port), timeout=self.timeout)
        s.sendall(f"CONNECT {self.host}:{self.port} HTTP/1.1\r\n"
                  f"Host: {self.host}:{self.port}\r\n\r\n".encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = s.recv(4096)
            if not chunk:
                raise WebSocketError("proxy a ferme pendant CONNECT")
            resp += chunk
        status = resp.split(b"\r\n", 1)[0]
        if b" 200" not in status:
            raise WebSocketError(f"proxy a refuse CONNECT: {status!r}")
        return s

    def connect(self) -> None:
        raw = self._raw_socket()
        ctx = ssl.create_default_context(cafile=self.ca_bundle)
        sock = ctx.wrap_socket(raw, server_hostname=self.host)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            f"GET {self.path} HTTP/1.1\r\nHost: {self.host}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise WebSocketError("connexion fermee pendant le handshake")
            head += chunk
        header, rest = head.split(b"\r\n\r\n", 1)
        if b"101" not in header.split(b"\r\n", 1)[0]:
            raise WebSocketError(f"upgrade refuse: {header.split(chr(13).encode())[0]!r}")
        self._sock, self._buf = sock, rest
        sock.settimeout(self.timeout)
        self.stats.connects += 1
        self.stats.connected_at_ms = int(time.time() * 1000)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._send_frame(OPCODE_CLOSE, b"")
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    @property
    def is_connected(self) -> bool:
        return self._sock is not None

    # ---- trames --------------------------------------------------------------
    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._sock is None:
            raise WebSocketClosed("socket fermee")
        mask = os.urandom(4)
        n = len(payload)
        frame = bytearray([0x80 | opcode])
        if n < 126:
            frame += bytes([0x80 | n])
        elif n < 65536:
            frame += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            frame += bytes([0x80 | 127]) + struct.pack(">Q", n)
        frame += mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(frame))

    def send_json(self, obj: Any) -> None:
        self._send_frame(OPCODE_TEXT, json.dumps(obj).encode())

    def _recv_frame(self) -> tuple[int, bytes]:
        while True:
            if len(self._buf) >= 2:
                b0, b1 = self._buf[0], self._buf[1]
                opcode = b0 & 0x0F
                masked = bool(b1 & 0x80)
                ln, off = b1 & 0x7F, 2
                if ln == 126:
                    if len(self._buf) < 4:
                        ln, off = None, None
                    else:
                        ln, off = struct.unpack(">H", self._buf[2:4])[0], 4
                elif ln == 127:
                    if len(self._buf) < 10:
                        ln, off = None, None
                    else:
                        ln, off = struct.unpack(">Q", self._buf[2:10])[0], 10
                if ln is not None:
                    total = off + ln + (4 if masked else 0)
                    if len(self._buf) >= total:
                        mask = self._buf[off:off + 4] if masked else None
                        start = off + (4 if masked else 0)
                        data = self._buf[start:start + ln]
                        if mask:
                            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
                        self._buf = self._buf[total:]
                        return opcode, bytes(data)
            if self._sock is None:
                raise WebSocketClosed("socket fermee")
            chunk = self._sock.recv(65536)
            if not chunk:
                raise WebSocketClosed("flux termine par le pair")
            self._buf += chunk

    def recv(self) -> Optional[RawMessage]:
        """Lit une trame. Repond aux ping, retourne None pour les trames de
        controle. Leve WebSocketClosed si le pair ferme."""
        opcode, data = self._recv_frame()
        recv_ms = int(time.time() * 1000)
        if opcode == OPCODE_PING:
            self._send_frame(OPCODE_PONG, data)
            self.stats.pings += 1
            return None
        if opcode == OPCODE_PONG:
            return None
        if opcode == OPCODE_CLOSE:
            raise WebSocketClosed("trame CLOSE recue")
        if opcode not in (OPCODE_TEXT, OPCODE_BIN, OPCODE_CONT):
            return None
        try:
            payload = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self.stats.errors += 1
            self.stats.last_error = f"payload malforme: {exc}"
            return None
        self.stats.messages += 1
        rows = payload.get("data") if isinstance(payload, dict) else None
        ex_ts = None
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            raw_ts = rows[0].get("ts")
            if raw_ts is not None:
                try:
                    ex_ts = int(raw_ts)
                except (TypeError, ValueError):
                    ex_ts = None
        return RawMessage(payload=payload, local_recv_ts_ms=recv_ms, exchange_ts_ms=ex_ts)


def connect_okx_public(timeout: float = 30.0) -> WebSocketClient:
    """Ouvre le flux public OKX en essayant les endpoints connus dans l'ordre.

    Leve WebSocketError en listant chaque echec : aucun repli silencieux.
    """
    failures: List[str] = []
    for host, port, path in OKX_PUBLIC_WS_ENDPOINTS:
        client = WebSocketClient(host, port, path, timeout=timeout)
        try:
            client.connect()
            return client
        except Exception as exc:
            failures.append(f"{host}:{port} -> {type(exc).__name__}: {exc}")
    raise WebSocketError("aucun endpoint WS OKX joignable: " + " | ".join(failures))
