"""Chrome'un uzerinden gectigi filtre vekili.

Neden vekil, neden Playwright'in istek yakalamasi degil:

Playwright `context.route()` ile istekleri yakalayabiliyor ve bu daha az kod
olurdu. Ama o yakalama otomasyon katmanindadir; Chrome'un yaptigi her seyi
degil, Playwright'in gordugu istekleri kapsar. Vekil ag katmanindadir --
sayfa bir baglantiya tikladiginda, bir alt kaynak yuklediginde, bir service
worker istek attiginda hepsi buradan gecer. Bir dil modelinin surdugu
tarayicida sinirin kacilamaz olmasi gerekiyor.

Vekil yalnizca 127.0.0.1'e baglanir ve rastgele bir porta oturur; disaridan
erisilebilir bir acik vekil degildir.
"""

from __future__ import annotations

import selectors
import socket
import threading
from collections.abc import Callable

from ..i18n import t
from ..logging import get_logger
from .policy import UrlBlocked, UrlPolicy

log = get_logger("browser.proxy")

_BUFFER = 65536
_CONNECT_TIMEOUT = 15.0
_IDLE_TIMEOUT = 120.0
# Istek satiri + basliklar icin ust sinir. Bunu asan bir istemci ya bozuk ya
# da kotu niyetli; ikisinde de baglantiyi kapatmak dogru cevap.
_MAX_HEADER_BYTES = 64 * 1024


class FilteringProxy:
    """Politikaya uymayan her istegi reddeden kucuk bir HTTP vekili."""

    def __init__(
        self,
        policy: UrlPolicy,
        *,
        on_request: Callable[[str, bool], None] | None = None,
    ) -> None:
        self.policy = policy
        self._on_request = on_request
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.port = 0

    # ------------------------------------------------------------------ #
    # Yasam dongusu
    # ------------------------------------------------------------------ #
    def start(self) -> int:
        if self._server is not None:
            return self.port
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(64)
        server.settimeout(0.5)
        self._server = server
        self.port = server.getsockname()[1]
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="deerx-proxy", daemon=True)
        self._thread.start()
        log.info("Filtre vekili 127.0.0.1:%d uzerinde", self.port)
        return self.port

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        if self._server is not None:
            self._server.close()
            self._server = None

    def __enter__(self) -> FilteringProxy:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ------------------------------------------------------------------ #
    # Baglanti isleme
    # ------------------------------------------------------------------ #
    def _serve(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:  # pragma: no cover - kapanis sirasinda
                break
            threading.Thread(
                target=self._handle, args=(client,), name="deerx-proxy-conn", daemon=True
            ).start()

    def _handle(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            client.settimeout(_IDLE_TIMEOUT)
            head, rest = _read_head(client)
            if not head:
                return
            line, _, header_block = head.partition("\r\n")
            method, _, target = line.partition(" ")
            target = target.split(" ")[0]
            method = method.upper()

            if method == "CONNECT":
                upstream = self._do_connect(client, target)
            else:
                upstream = self._do_plain(client, method, target, header_block, rest)
            if upstream is not None:
                _pump(client, upstream)
        except Exception:  # noqa: BLE001
            # Bir baglanti is parcaciginda yakalanmamis istisnanin hicbir
            # faydasi yok: kimse gormez, yalnizca gurultu uretir. Karsi taraf
            # her an kapanabilir; olan biteni gunluge birakip baglantiyi
            # kapatmak dogru davranis.
            log.debug(t("setup.proxy_error"), exc_info=True)
        finally:
            _close(client)
            _close(upstream)

    # ------------------------------------------------------------------ #

    def _check(self, url: str) -> str | None:
        """Politikaya sorar; reddedilirse gerekce metnini doner."""
        try:
            self.policy.check(url)
        except UrlBlocked as exc:
            if self._on_request:
                self._on_request(url, False)
            log.warning("Vekil reddetti: %s (%s)", url, exc)
            return str(exc)
        if self._on_request:
            self._on_request(url, True)
        return None

    def _do_connect(self, client: socket.socket, target: str) -> socket.socket | None:
        """HTTPS tunelini acar. Yalnizca host:port gorunur; politika da host bazli."""
        host, _, port_text = target.rpartition(":")
        try:
            port = int(port_text)
        except ValueError:
            _deny(client, "Gecersiz CONNECT hedefi.")
            return None

        reason = self._check(f"https://{host}:{port}")
        if reason:
            _deny(client, reason)
            return None

        try:
            upstream = socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT)
        except OSError as exc:
            _deny(client, f"Baglanti kurulamadi: {exc}", status="502 Bad Gateway")
            return None
        upstream.settimeout(_IDLE_TIMEOUT)
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        return upstream

    def _do_plain(
        self,
        client: socket.socket,
        method: str,
        target: str,
        header_block: str,
        rest: bytes,
    ) -> socket.socket | None:
        """Duz HTTP istegi. Mutlak adres origin bicimine cevrilip iletilir."""
        if not target.lower().startswith("http://"):
            _deny(client, "Vekile yalnizca mutlak adresli istek gonderilebilir.")
            return None

        reason = self._check(target)
        if reason:
            _deny(client, reason)
            return None

        without_scheme = target[len("http://"):]
        authority, slash, path = without_scheme.partition("/")
        host, _, port_text = authority.partition(":")
        port = int(port_text) if port_text else 80
        path = f"/{path}" if slash else "/"

        try:
            upstream = socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT)
        except OSError as exc:
            _deny(client, f"Baglanti kurulamadi: {exc}", status="502 Bad Gateway")
            return None
        upstream.settimeout(_IDLE_TIMEOUT)

        # Her istek ayri baglantida gider. Kalici baglantida ayni tunelden
        # gelen SONRAKI istekler politikadan gecmezdi; kucuk bir hiz bedeli
        # karsiliginda her istegi denetliyoruz.
        headers = [
            row for row in header_block.split("\r\n")
            if row and not row.lower().startswith(("proxy-connection:", "connection:"))
        ]
        headers.append("Connection: close")
        request = f"{method} {path} HTTP/1.1\r\n" + "\r\n".join(headers) + "\r\n\r\n"
        upstream.sendall(request.encode("latin-1", "replace"))
        if rest:
            upstream.sendall(rest)
        return upstream


# ---------------------------------------------------------------------- #
# Soket yardimcilari
# ---------------------------------------------------------------------- #
def _read_head(sock: socket.socket) -> tuple[str, bytes]:
    """Istek satiri + basliklari okur; govdenin okunan ilk parcasini da doner."""
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        if len(buffer) > _MAX_HEADER_BYTES:
            raise OSError("baslik bloku fazla buyuk")
        chunk = sock.recv(_BUFFER)
        if not chunk:
            return "", b""
        buffer += chunk
    head, _, rest = buffer.partition(b"\r\n\r\n")
    return head.decode("latin-1", "replace"), rest


def _deny(client: socket.socket, reason: str, *, status: str = "403 Forbidden") -> None:
    body = (
        "DeerX tarayici politikasi bu adresi reddetti.\r\n\r\n"
        f"{reason}\r\n"
    ).encode()
    client.sendall(
        f"HTTP/1.1 {status}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n".encode("latin-1") + body
    )


def _pump(a: socket.socket, b: socket.socket) -> None:
    """Iki soket arasinda bayt tasir; biri kapanana kadar."""
    sel = selectors.DefaultSelector()
    sel.register(a, selectors.EVENT_READ)
    sel.register(b, selectors.EVENT_READ)
    try:
        while True:
            events = sel.select(timeout=_IDLE_TIMEOUT)
            if not events:
                return
            for key, _ in events:
                source = key.fileobj
                target = b if source is a else a
                try:
                    data = source.recv(_BUFFER)  # type: ignore[union-attr]
                except OSError:
                    return
                if not data:
                    return
                try:
                    target.sendall(data)
                except OSError:
                    return
    finally:
        sel.close()


def _close(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()
