"""Check the built MCP protocol and idle startup without touching user state."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from collaborator import __version__
with tempfile.TemporaryDirectory(prefix='collaborator-packaged-') as temporary:
    home = Path(temporary)
    with socket.socket() as socket_handle:
        socket_handle.bind(('127.0.0.1', 0))
        port = socket_handle.getsockname()[1]
    (home / 'settings.json').write_text(json.dumps({'hub_port': port}), encoding='utf-8')
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2024-11-05', 'clientInfo': {'name': 'packaged-test', 'version': '1'}}},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
    ]
    environment = dict(os.environ, COLLABORATORMCP_HOME=str(home))
    environment.pop('COLLABORATORMCP_PROBE', None)
    result = subprocess.run([str(root / 'dist' / 'collaborator-mcp.exe')],
        input=''.join(json.dumps(message) + '\n' for message in messages),
        text=True, capture_output=True, timeout=30, env=environment)
    assert result.returncode == 0, (result.returncode, result.stderr)
    replies = {row['id']: row for row in map(json.loads, result.stdout.splitlines())}
    assert replies[1]['result']['serverInfo']['version'] == __version__
    assert len(replies[2]['result']['tools']) == 29
    assert 'backend=standby' in result.stderr, result.stderr
    assert not (home / 'collaborator.db').exists(), 'Tool discovery started an engine'

    # The built server must authenticate to a running desktop hub.
    from collaborator.config import Settings
    from collaborator.engine import Engine
    from collaborator.hub import HubServer
    from collaborator.store import Store
    settings = Settings(str(home / 'settings.json'))
    settings.set('workspace', str(home))
    store = Store(str(home / 'desktop.db'))
    engine = Engine(settings, store)
    server = HubServer(engine, port=port)
    assert server.start()
    try:
        call = {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                'params': {'name': 'collaborator_status', 'arguments': {}}}
        result = subprocess.run([str(root / 'dist' / 'collaborator-mcp.exe')],
            input=json.dumps(messages[0]) + '\n' + json.dumps(call) + '\n',
            text=True, capture_output=True, timeout=30, env=environment)
        assert 'backend=hub' in result.stderr, result.stderr
        reply = [row for row in map(json.loads, result.stdout.splitlines()) if row['id'] == 3][0]
        assert not reply['result']['isError'], reply
    finally:
        server.stop()
        engine.shutdown()
        store.close()
    print('PASS packaged MCP %s: handshake, 29 tools, no workers/database on '
          'discovery, authenticated hub connection' % __version__)
