"""Client WebSocket minimal, stdlib seule. Pour la RESOLUTION, pas le confort.

POURQUOI CE MODULE EXISTE. Le test de propagation demande des delais de 0,5 s.
Le collecteur sonde `/depth` toutes les 5 secondes et une requete met 400 a
900 ms : le sondage ne peut structurellement pas mesurer un delai de 0,5 s.
Conclure « pas de propagation » avec cet instrument reviendrait a mesurer la
cadence de mon sondage et a l'appeler une propriete du marche.

POURQUOI IL EST ECRIT A LA MAIN. Une garde d'architecture du depot interdit
toute dependance tierce (`test_v2_imports_only_stdlib_and_itself`), pour que
les tests tournent partout a l'identique. Un client WebSocket tient en une
centaine de lignes ; l'ecrire coute moins cher que de percer la garde.

CE QU'IL FAIT, ET RIEN DE PLUS : poignee de main HTTP/1.1, lecture de trames
texte, reponse aux pings. Pas de reconnexion automatique, pas de compression,
pas de fragmentation sortante — le seul message emis est un abonnement court.
Une coupure remonte comme une exception : la collecte la verra et l'inscrira
comme une donnee manquante, plutot que de la combler en silence.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
from typing import Iterator, Optional, Sequence

WS_HOST = "ws.backpack.exchange"
WS_PORT = 443

#: Opcodes RFC 6455 utilises ici.
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


class WebSocket:
    """Connexion TLS + WebSocket vers un flux public. Aucun secret transite."""

    def __init__(self, host: str = WS_HOST, port: int = WS_PORT,
                 path: str = "/", timeout: float = 30.0):
        self.host, self.port, self.path = host, port, path
        ctx = ssl.create_default_context()
        raw = socket.create_connection((host, port), timeout=timeout)
        self.sock = ctx.wrap_socket(raw, server_hostname=host)
        self._buf = b""
        self._handshake()

    def _handshake(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {self.path} HTTP/1.1\r\n"
               f"Host: {self.host}\r\n"
               "Upgrade: websocket\r\n"
               "Connection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        while b"\r\n\r\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("connexion fermee pendant la poignee de main")
            self._buf += chunk
        head, _, rest = self._buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise ConnectionError(f"poignee de main refusee : "
                                  f"{head.split(chr(13).encode())[0][:80]!r}")
        self._buf = rest

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("connexion fermee par la venue")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_text(self, text: str) -> None:
        """Emet une trame texte MASQUEE — le masque est obligatoire cote client."""
        data = text.encode()
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            head = struct.pack("!BB", 0x80 | _OP_TEXT, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | _OP_TEXT, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | _OP_TEXT, 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(head + mask + masked)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        head = struct.pack("!BB", 0x80 | opcode, 0x80 | len(payload))
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    def messages(self) -> Iterator[str]:
        """Rend les trames TEXTE. Repond aux pings, ignore le binaire.

        Un `close` recu termine l'iteration proprement. Toute autre coupure
        remonte en exception : la collecte doit VOIR le trou, pas le combler.
        """
        while True:
            b1, b2 = struct.unpack("!BB", self._recv_exact(2))
            opcode = b1 & 0x0F
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            payload = self._recv_exact(length) if length else b""

            if opcode == _OP_CLOSE:
                return
            if opcode == _OP_PING:
                self._send_frame(_OP_PONG, payload)
                continue
            if opcode in (_OP_PONG, _OP_BINARY):
                continue
            if opcode == _OP_TEXT:
                yield payload.decode("utf-8", "replace")

    def subscribe(self, streams: Sequence[str]) -> None:
        self.send_text(json.dumps({"method": "SUBSCRIBE",
                                   "params": list(streams)}))

    def close(self) -> None:
        try:
            self._send_frame(_OP_CLOSE, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def __enter__(self) -> "WebSocket":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
