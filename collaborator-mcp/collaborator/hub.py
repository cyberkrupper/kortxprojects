"""Loopback hub so the MCP stdio process and the desktop UI share one engine.

Whoever binds the port owns the engine. The other side connects as a client.
If nothing is listening, a caller can start its own embedded engine instead.
Protocol: newline-delimited JSON over TCP on 127.0.0.1. The first line of
every connection must be {"method": "auth", "params": {"token": ...}} carrying
the secret stored beside the settings file. Anything else closes the
connection, so a web page posting to 127.0.0.1 cannot drive the engine.
"""

import hmac
import json
import os
import secrets
import socket
import socketserver
import threading
import time
import uuid

from . import api


TOKEN_FILE = "hub.token"


def load_token(directory):
    """The hub secret shared by every local process, created on first use."""
    path = os.path.join(directory, TOKEN_FILE)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(secrets.token_hex(32))
    # A creator in another process may not have finished writing yet.
    for _ in range(50):
        with open(path, "r", encoding="ascii") as fh:
            token = fh.read().strip()
        if token:
            return token
        time.sleep(0.02)
    raise RuntimeError("The hub token file %s is empty." % path)


def _token_dir(engine):
    from . import config
    path = getattr(engine.settings, "path", None)
    return os.path.dirname(path) if path else config.app_dir()


class _Handler(socketserver.StreamRequestHandler):
    def _authenticate(self):
        """Accept the connection only if its first line carries the token."""
        try:
            self.connection.settimeout(10)
            msg = json.loads(self.rfile.readline().decode("utf-8"))
            self.connection.settimeout(None)
            token = (msg.get("params") or {}).get("token") or ""
            ok = (msg.get("method") == "auth" and isinstance(token, str)
                  and hmac.compare_digest(token.encode("utf-8"),
                                          self.server.token.encode("utf-8")))
        except Exception:
            return False
        if ok:
            self.wfile.write((json.dumps({"id": msg.get("id"), "ok": True,
                                          "result": {"auth": True}})
                              + "\n").encode("utf-8"))
            self.wfile.flush()
        return ok

    def handle(self):
        if not self._authenticate():
            return
        engine = self.server.engine
        subscribed = [False]
        lock = threading.Lock()
        conn_id = "conn_%s" % uuid.uuid4().hex[:8]
        identified = [False]

        def push(event):
            if not subscribed[0]:
                return
            try:
                with lock:
                    self.wfile.write(
                        (json.dumps({"event": event}) + "\n").encode("utf-8"))
                    self.wfile.flush()
            except Exception:
                pass

        def send(reply):
            try:
                with lock:
                    self.wfile.write(
                        (json.dumps(reply, default=str) + "\n").encode("utf-8"))
                    self.wfile.flush()
            except Exception:
                pass

        def run(req_id, method, params, source):
            try:
                reply = {"id": req_id, "ok": True,
                         "result": api.dispatch(engine, method, params, source)}
            except Exception as exc:
                reply = {"id": req_id, "ok": False, "error": str(exc)}
            send(reply)

        engine.subscribe(push)
        try:
            for raw in self.rfile:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                req_id = msg.get("id")
                method = msg.get("method") or ""
                params = msg.get("params") or {}
                if method == "subscribe":
                    subscribed[0] = True
                    send({"id": req_id, "ok": True,
                          "result": {"subscribed": True}})
                elif method == "hello":
                    # An MCP orchestrator identifying itself.
                    identified[0] = True
                    engine.register_client(conn_id,
                                           params.get("name") or "",
                                           params.get("version") or "")
                    send({"id": req_id, "ok": True,
                          "result": {"registered": conn_id}})
                else:
                    source = "mcp" if identified[0] else "local"
                    if identified[0]:
                        engine.note_client_call(conn_id, method)
                    # Each call on its own thread: a long task.wait must not
                    # stall the calls queued behind it on this connection.
                    threading.Thread(target=run,
                                     args=(req_id, method, params, source),
                                     name="hub-call", daemon=True).start()
        except Exception:
            pass
        finally:
            engine.unsubscribe(push)
            if identified[0]:
                engine.unregister_client(conn_id)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # SO_REUSEADDR permits two Windows listeners to bind the same port,
    # splitting clients between competing engines. Require exclusive ownership.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, addr, engine, token):
        self.engine = engine
        self.token = token
        socketserver.ThreadingTCPServer.__init__(self, addr, _Handler)


class HubServer(object):
    def __init__(self, engine, host="127.0.0.1", port=8787):
        self.engine = engine
        self.host = host
        self.port = port
        self._server = None
        self._thread = None

    def start(self):
        """Bind and serve. Returns True on success, False if the port is taken."""
        try:
            token = load_token(_token_dir(self.engine))
            self._server = _Server((self.host, self.port), self.engine, token)
        except OSError:
            return False
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="hub", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass


class HubClient(object):
    """Client for a running hub. Thread-safe; calls may overlap.

    A reader thread routes each reply to its caller by id, so a long call
    (task.wait) does not block shorter ones issued meanwhile.
    """

    def __init__(self, host="127.0.0.1", port=8787, timeout=None,
                 token_dir=None):
        self.host = host
        self.port = port
        self.token_dir = token_dir
        # Per-call ceiling in seconds; None waits as long as the hub is up.
        self.timeout = timeout
        self._sock = None
        self._fh = None
        self._write_lock = threading.Lock()
        self._pending = {}          # req_id -> [threading.Event, reply]
        self._pending_lock = threading.Lock()
        self._next_id = 1
        self._closed = False

    def connect(self):
        from . import config
        token = load_token(self.token_dir or config.app_dir())
        sock = socket.create_connection((self.host, self.port), timeout=5)
        fh = sock.makefile("rwb")
        try:
            fh.write((json.dumps({"id": 0, "method": "auth",
                                  "params": {"token": token}})
                      + "\n").encode("utf-8"))
            fh.flush()
            reply = json.loads(fh.readline().decode("utf-8") or "{}")
        except Exception:
            reply = {}
        if not reply.get("ok"):
            sock.close()
            raise ConnectionError("The hub rejected this connection's token.")
        sock.settimeout(None)
        self._sock = sock
        self._fh = fh
        threading.Thread(target=self._read_loop, name="hub-client",
                         daemon=True).start()
        return self

    def close(self):
        self._closed = True
        # Shut the socket down first: that ends the reader thread's blocked
        # readline. Closing the buffered file while it is mid-read would
        # deadlock on the buffer's lock.
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass

    def _read_loop(self):
        try:
            while True:
                line = self._fh.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    continue
                if "event" in msg:
                    continue          # this client does not subscribe
                with self._pending_lock:
                    slot = self._pending.get(msg.get("id"))
                if slot is not None:
                    slot[1] = msg
                    slot[0].set()
        except Exception:
            pass
        finally:
            # Wake every waiter; with no reply they raise ConnectionError.
            self._closed = True
            try:
                self._fh.close()
            except Exception:
                pass
            with self._pending_lock:
                slots = list(self._pending.values())
            for slot in slots:
                slot[0].set()

    def call(self, method, params=None):
        if self._closed:
            raise ConnectionError("Hub closed the connection.")
        slot = [threading.Event(), None]
        with self._pending_lock:
            self._next_id += 1
            req_id = self._next_id
            self._pending[req_id] = slot
        try:
            payload = json.dumps({"id": req_id, "method": method,
                                  "params": params or {}}) + "\n"
            try:
                with self._write_lock:
                    self._fh.write(payload.encode("utf-8"))
                    self._fh.flush()
            except (OSError, ValueError) as exc:
                raise ConnectionError("Hub connection lost: %s" % exc)
            if not slot[0].wait(self.timeout):
                raise TimeoutError("Hub call %s timed out." % method)
            msg = slot[1]
            if msg is None:
                raise ConnectionError("Hub closed the connection.")
            if msg.get("ok"):
                return msg.get("result")
            raise RuntimeError(msg.get("error") or "Hub call failed.")
        finally:
            with self._pending_lock:
                self._pending.pop(req_id, None)


def try_connect(host="127.0.0.1", port=8787, timeout=None, token_dir=None):
    """Return a connected HubClient, or None if no hub is listening."""
    try:
        return HubClient(host, port, timeout, token_dir).connect()
    except Exception:
        return None


def hub_listening(host="127.0.0.1", port=8787):
    """Whether something owns the hub port, without authenticating."""
    try:
        socket.create_connection((host, port), timeout=1).close()
        return True
    except OSError:
        return False


def wait_for_hub(host="127.0.0.1", port=8787, seconds=5.0, token_dir=None):
    deadline = time.time() + seconds
    while time.time() < deadline:
        client = try_connect(host, port, token_dir=token_dir)
        if client:
            return client
        time.sleep(0.25)
    return None
