"""storyboard_team — native storyboardTeam rule binding + real readiness verification.

Verifies the installed configured team (team.json, members, knowledge) carries the
appended product-rule references intact and that the live loopback server registry
reports it enabled with no load error. Never invokes the team's LLM (paid, budget 0):
structural/readiness checks only, and the report says so explicitly.
"""
import json
import base64
import hashlib
import math
import os
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
import urllib.request
from pathlib import Path

from agents import koc_rules

TEAM_DIR = koc_rules.ROOT / 'runtime' / 'toonflow-data' / 'agents' / 'storyboardTeam'
AGENTS_API = 'http://127.0.0.1:3000/api/agents/get'
TEAM_READ_API = 'http://127.0.0.1:3000/api/agents/read?name=storyboardTeam'
KNOWLEDGE_MARKER = '商品素材规则'
WORKSPACES_ROOT = koc_rules.ROOT / 'runtime' / 'toonflow-data' / 'workspaces'
MCP_CONFIG = koc_rules.ROOT / 'config' / 'mcp.json'
MCP_SETTINGS = koc_rules.ROOT / 'runtime' / 'toonflow-data' / 'settings.json'
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_RPC_BODY_BYTES = 30 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 60


class CommodityBridgeError(RuntimeError):
    pass


def _validate_timeout(timeout):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise CommodityBridgeError('timeout must be positive and at most %d seconds' % MAX_TIMEOUT_SECONDS)
    return float(timeout)


def _validate_mcp_endpoint(endpoint):
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise CommodityBridgeError('MCP endpoint has an invalid port or URL') from exc
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or port is None
            or not 1 <= port <= 65535 or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise CommodityBridgeError('MCP endpoint must be plain loopback HTTP without userinfo')
    return endpoint


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_mcp_request(request, timeout):
    return build_opener(ProxyHandler({}), _NoRedirectHandler()).open(request, timeout=timeout)


def _read_bounded_response(response, max_bytes):
    length = response.headers.get('Content-Length')
    if length is not None:
        try:
            if int(length) > max_bytes:
                raise CommodityBridgeError('MCP response exceeds the configured byte limit')
        except ValueError:
            raise CommodityBridgeError('MCP response has an invalid Content-Length')
    parts = []
    total = 0
    while True:
        chunk = response.read(min(65536, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise CommodityBridgeError('MCP response exceeds the configured byte limit')
        parts.append(chunk)
    return b''.join(parts)


def _decode_workspace_base64(value):
    if not isinstance(value, str):
        raise CommodityBridgeError('workspaceFiles readBinary base64 is missing')
    max_encoded_bytes = 4 * ((MAX_FILE_BYTES + 2) // 3)
    if len(value) > max_encoded_bytes:
        raise CommodityBridgeError('workspaceFiles readBinary exceeds the 20 MiB file limit')
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception as exc:  # noqa: BLE001 — malformed provider response
        raise CommodityBridgeError('workspaceFiles readBinary returned invalid base64') from exc
    if len(decoded) > MAX_FILE_BYTES:
        raise CommodityBridgeError('workspaceFiles readBinary exceeds the 20 MiB file limit')
    return decoded


def _validate_json_value(value, active=None):
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CommodityBridgeError('shots JSON contains a non-finite number')
        return
    if active is None:
        active = set()
    if isinstance(value, (list, dict)):
        marker = id(value)
        if marker in active:
            raise CommodityBridgeError('shots JSON contains a circular reference')
        active.add(marker)
        try:
            if isinstance(value, list):
                for item in value:
                    _validate_json_value(item, active)
            else:
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise CommodityBridgeError('shots JSON object keys must be strings')
                    _validate_json_value(item, active)
        finally:
            active.remove(marker)
        return
    raise CommodityBridgeError('shots contains a non-JSON value: %s' % type(value).__name__)


def _parse_mcp_response(response):
    text = _read_bounded_response(response, MAX_RPC_BODY_BYTES).decode('utf-8', errors='replace')
    if 'text/event-stream' in response.headers.get('Content-Type', ''):
        data = [line[5:].strip() for line in text.splitlines() if line.startswith('data:')]
        text = data[-1] if data else ''
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CommodityBridgeError('MCP returned a non-JSON response') from exc


class _McpWorkspaceClient:
    """Small, loopback-only Streamable HTTP client for the official workspaceFiles tool."""

    is_mock = False

    def __init__(self, timeout):
        self.timeout = _validate_timeout(timeout)
        config = json.loads(MCP_CONFIG.read_text(encoding='utf-8'))
        endpoint = _validate_mcp_endpoint(config['server']['endpoint'])
        settings = json.loads(MCP_SETTINGS.read_text(encoding='utf-8'))
        token = ((settings.get('settings') or {}).get('mcp') or {}).get('token')
        if not isinstance(token, str) or len(token) < 32:
            raise CommodityBridgeError('MCP token is unavailable in target-local settings')
        self.endpoint = endpoint
        self.token = token
        self.session_id = None
        self.protocol_calls = []
        initialized = self._rpc('initialize', {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 'laox-commodity-material-bridge', 'version': '1.0'},
        }, request_id=1)
        if not initialized or 'result' not in initialized:
            raise CommodityBridgeError('MCP initialize did not return a result')
        self._rpc('notifications/initialized', {}, request_id=None)

    def _rpc(self, method, params, request_id):
        headers = {
            'Authorization': 'Bearer ' + self.token,
            'Accept': 'application/json, text/event-stream',
            'Content-Type': 'application/json',
        }
        if self.session_id:
            headers['mcp-session-id'] = self.session_id
        payload = {'jsonrpc': '2.0', 'method': method}
        if request_id is not None:
            payload['id'] = request_id
        if params is not None:
            payload['params'] = params
        self.protocol_calls.append(method)
        body = json.dumps(payload, allow_nan=False).encode('utf-8')
        if len(body) > MAX_RPC_BODY_BYTES:
            raise CommodityBridgeError('MCP request exceeds the configured byte limit')
        request = urllib.request.Request(
            self.endpoint, data=body,
            headers=headers, method='POST')
        try:
            with _open_mcp_request(request, timeout=self.timeout) as response:
                self.session_id = self.session_id or response.headers.get('mcp-session-id')
                decoded = _parse_mcp_response(response)
                if response.status not in (200, 202):
                    raise CommodityBridgeError('MCP %s HTTP %d' % (method, response.status))
        except CommodityBridgeError:
            raise
        except Exception as exc:  # noqa: BLE001 — report transport refusal as blocked
            raise CommodityBridgeError('MCP %s transport failed: %s' % (method, str(exc)[:180])) from exc
        if decoded and decoded.get('error'):
            raise CommodityBridgeError('MCP %s returned a protocol error' % method)
        return decoded

    def get_tools(self, timeout):
        result = self._rpc('tools/list', {}, request_id=2)
        return ((result or {}).get('result') or {}).get('tools') or []

    def call_tool(self, name, arguments, timeout):
        result = self._rpc('tools/call', {'name': name, 'arguments': arguments}, request_id=3 + len(self.protocol_calls))
        tool_result = (result or {}).get('result') or {}
        if tool_result.get('isError'):
            raise CommodityBridgeError('MCP tool %s returned isError' % name)
        content = tool_result.get('content')
        if not isinstance(content, list) or not content or not isinstance(content[0].get('text'), str):
            raise CommodityBridgeError('MCP tool %s returned no text result' % name)
        try:
            return json.loads(content[0]['text'])
        except ValueError:
            raise CommodityBridgeError('MCP tool %s returned non-JSON text' % name)


def _target_for_commodity_import(target):
    if not isinstance(target, dict) or set(target) != {'directory', 'path'}:
        raise CommodityBridgeError('target must explicitly contain directory and path')
    if not isinstance(target['directory'], str):
        raise CommodityBridgeError('target.directory must be an absolute path string')
    raw_directory = Path(target['directory'])
    if not raw_directory.is_absolute():
        raise CommodityBridgeError('target.directory must be absolute')
    root_literal = os.path.normcase(os.path.abspath(str(WORKSPACES_ROOT)))
    target_literal = os.path.normcase(os.path.abspath(target['directory']))
    if '..' in raw_directory.parts or os.path.commonpath([root_literal, target_literal]) != root_literal:
        raise CommodityBridgeError('target must be lexically inside Toonflow workspaces')
    if os.path.dirname(target_literal) != root_literal:
        raise CommodityBridgeError('target must be a new direct child of Toonflow workspaces')
    project_root = koc_rules.ROOT.resolve(strict=True)
    root = WORKSPACES_ROOT.resolve(strict=True)
    parent = Path(os.path.dirname(target_literal)).resolve(strict=True)
    project_root_norm = os.path.normcase(str(project_root))
    for canonical_path in (root, parent):
        canonical_norm = os.path.normcase(str(canonical_path))
        try:
            inside_project = os.path.commonpath([project_root_norm, canonical_norm]) == project_root_norm
        except ValueError:
            inside_project = False
        if not inside_project:
            raise CommodityBridgeError('canonical Toonflow workspaces path must remain inside project root')
    if os.path.normcase(str(parent)) != os.path.normcase(str(root)):
        raise CommodityBridgeError('target parent must resolve to Toonflow workspaces')
    target_directory = Path(target_literal)
    if target_directory.is_symlink():
        raise CommodityBridgeError('target workspace cannot be a symlink')
    if target_directory.exists():
        raise CommodityBridgeError('target workspace already exists; refusing to overwrite')
    filename = target['path']
    if (not isinstance(filename, str) or not filename or Path(filename).name != filename
            or filename in ('.', '..') or ':' in filename or '/' in filename or '\\' in filename):
        raise CommodityBridgeError('target.path must be one relative filename')
    return str(target_directory), str(parent), filename


def _check_workspace_tool(tools):
    by_name = {tool.get('name'): tool for tool in tools if isinstance(tool, dict)}
    workspace = by_name.get('workspaceFiles')
    state = by_name.get('getAppState')
    if not workspace or not state:
        raise CommodityBridgeError('MCP tools/list lacks workspaceFiles or getAppState')
    schema = workspace.get('inputSchema') or {}
    props = schema.get('properties') or {}
    args_props = ((props.get('args') or {}).get('properties') or {})
    actions = ((args_props.get('action') or {}).get('enum') or [])
    if not {'mkdir', 'writeBinary', 'readBinary'}.issubset(set(actions)):
        raise CommodityBridgeError('workspaceFiles schema does not support required actions')
    if 'directory' not in ((props.get('target') or {}).get('properties') or {}):
        raise CommodityBridgeError('workspaceFiles schema does not accept explicit target.directory')
    if 'base64' not in args_props or 'exclusive' not in args_props:
        raise CommodityBridgeError('workspaceFiles schema lacks base64/exclusive fields')


def import_commodity_shots(shots, target, timeout=15, client=None):
    """Write shot JSON into a new explicit Toonflow workspace and verify an MCP readback.

    This bridges a file only; node display/edit and any LLM/provider execution are separate
    capabilities and are never invoked here. Injected clients must identify as explicit mocks.
    """
    calls = []
    report = {
        'status': 'blocked', 'bridge': 'workspaceFiles-json', 'mock': False,
        'verification_scope': 'live_mcp_file_bridge',
        'input_sha256': None, 'tool_calls': calls,
        'readback_status': 'not_run', 'readback_sha256': None,
        'workspace_file_mutability': 'not_verified',
        'node_display': 'not_run', 'node_edit': 'not_run',
        'llm_invocation': 'not_run', 'paid_calls': 0, 'h3_calls': 0,
    }
    try:
        timeout = _validate_timeout(timeout)
        if not isinstance(shots, (list, dict)):
            raise CommodityBridgeError('shots must be a JSON array or object')
        _validate_json_value(shots)
        raw = json.dumps(shots, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        report['input_sha256'] = hashlib.sha256(raw).hexdigest()
        if len(raw) > MAX_FILE_BYTES:
            raise CommodityBridgeError('shots JSON exceeds Toonflow 20 MiB workspaceFiles limit')
        directory, parent, filename = _target_for_commodity_import(target)
        if client is None:
            client = _McpWorkspaceClient(timeout)
            report['mock'] = False
        else:
            if getattr(client, 'is_mock', False) is not True:
                raise CommodityBridgeError('injected client must explicitly declare is_mock=True')
            report['mock'] = True
            report['verification_scope'] = 'UNIT_ONLY_mock_transport'
        tools = client.get_tools(timeout)
        calls.extend(getattr(client, 'protocol_calls', []) or (['tools/list(mock)'] if report['mock'] else []))
        _check_workspace_tool(tools)

        def call_tool(name, arguments):
            calls.append('tools/call:' + name)
            return client.call_tool(name, arguments, timeout)

        state = call_tool('getAppState', {})
        connections = state.get('connections') if isinstance(state, dict) else None
        if not isinstance(connections, list):
            raise CommodityBridgeError('getAppState did not return a connections list')
        report['connected_page_count'] = len(connections)
        if not connections:
            report['node_display'] = 'blocked_no_connected_page'
            report['node_edit'] = 'blocked_no_connected_page'

        mkdir = call_tool('workspaceFiles', {
            'target': {'directory': parent},
            'args': {'action': 'mkdir', 'path': Path(directory).name},
        })
        if not isinstance(mkdir, dict) or mkdir.get('success') is not True:
            raise CommodityBridgeError('workspaceFiles mkdir did not confirm success')
        report['workspace_created'] = True
        write = call_tool('workspaceFiles', {
            'target': {'directory': directory},
            'args': {'action': 'writeBinary', 'path': filename,
                     'base64': base64.b64encode(raw).decode('ascii'), 'exclusive': True},
        })
        if not isinstance(write, dict) or write.get('success') is not True:
            raise CommodityBridgeError('workspaceFiles writeBinary did not confirm success')
        report['write_status'] = 'verified'
        report['workspace_path'] = str(Path(directory) / filename)
        readback = call_tool('workspaceFiles', {
            'target': {'directory': directory},
            'args': {'action': 'readBinary', 'path': filename},
        })
        if not isinstance(readback, dict) or readback.get('path') != filename or not isinstance(readback.get('base64'), str):
            raise CommodityBridgeError('workspaceFiles readBinary returned an invalid file result')
        decoded = _decode_workspace_base64(readback['base64'])
        readback_hash = hashlib.sha256(decoded).hexdigest()
        report['readback_sha256'] = readback_hash
        if decoded != raw or json.loads(decoded.decode('utf-8')) != shots:
            raise CommodityBridgeError('workspaceFiles readback differs from the input JSON')
        report.update({
            'status': 'verified', 'readback_status': 'verified',
            'workspace_file_mutability': 'not_verified_write_once_exclusive',
            'input_matches_readback': True,
            'node_bridge_status': 'blocked_no_connected_page' if not connections else 'not_run',
        })
    except Exception as exc:  # noqa: BLE001 — every failure is an explicit blocked result
        report['status'] = 'blocked'
        report['blocked_reason'] = (str(exc) or type(exc).__name__)[:300]
        if report.get('workspace_created') and report.get('readback_status') != 'verified':
            report['workspace_state'] = 'created_but_bridge_incomplete'
    return report


def verify_team_files():
    """Structural readiness: team.json parses, entry/members exist, appended product-rule
    marker present in each member, shared knowledge file present AND natively registered
    in each member's knowledge list (coordinator-authorized 28th-file edit, 2026-10-11)."""
    team_policy = koc_rules.storyboard_team_policy()
    marker = team_policy['append_marker']
    report = {'team_json': None, 'entry': None, 'members': {}, 'knowledge_file': None,
              'knowledge_registered': None, 'problems': []}
    team_json = TEAM_DIR / 'team.json'
    if not team_json.is_file():
        report['problems'].append('TEAM_JSON_MISSING')
        return report
    try:
        team = json.loads(team_json.read_text(encoding='utf-8'))
    except ValueError as exc:
        report['problems'].append('TEAM_JSON_UNPARSEABLE:' + str(exc))
        return report
    report['team_json'] = {'name': team.get('name'), 'version': team.get('version'), 'entry': team.get('entry')}
    report['entry'] = team.get('entry')
    for member in ('director', 'writer', 'reviewer'):
        path = TEAM_DIR / 'members' / (member + '.md')
        if not path.is_file():
            report['problems'].append('MEMBER_FILE_MISSING:' + member)
            continue
        text = path.read_text(encoding='utf-8')
        has_marker = marker in text
        report['members'][member] = {'path': str(path), 'product_rules_appended': has_marker,
                                     'bytes': len(text.encode('utf-8'))}
        if not has_marker:
            report['problems'].append('MEMBER_RULE_MARKER_MISSING:' + member)
    knowledge = TEAM_DIR / 'knowledge' / Path(team_policy['knowledge_file']).name
    knowledge_from_root = koc_rules.ROOT / team_policy['knowledge_file']
    target = knowledge_from_root if knowledge_from_root.is_file() else knowledge
    if target.is_file():
        report['knowledge_file'] = {'path': str(target), 'bytes': target.stat().st_size}
    else:
        report['problems'].append('KNOWLEDGE_FILE_MISSING:knowledge/product-materials.md')
    declared = set((team.get('members') or {}).keys())
    if declared != {'director', 'writer', 'reviewer'}:
        report['problems'].append('TEAM_MEMBERS_UNEXPECTED:' + ','.join(sorted(declared)))
    if team.get('entry') != 'director':
        report['problems'].append('TEAM_ENTRY_NOT_DIRECTOR')
    knowledge_lists = [cfg.get('knowledge') or [] for cfg in (team.get('members') or {}).values()]
    report['knowledge_registered'] = all(
        'continuity.md' in kl and 'product-materials.md' in kl for kl in knowledge_lists)
    if not report['knowledge_registered']:
        report['problems'].append('KNOWLEDGE_NOT_REGISTERED_IN_TEAM_JSON')
    return report


def verify_live_registry(timeout=10):
    """Live readiness via the running loopback server registry. The service is NOT
    started/stopped/restarted here; when it is not reachable that fact is reported
    as service_not_running (not a fake pass)."""
    try:
        with urllib.request.urlopen(AGENTS_API, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
    except Exception as exc:  # noqa: BLE001 — any transport failure is an honest status
        return {'reachable': False, 'status': 'service_not_running', 'error': str(exc)[:200],
                'llm_invocation': 'not_run'}
    agents = (payload.get('data') or {}).get('agents') or []
    team = next((a for a in agents if a.get('name') == 'storyboardTeam'), None)
    if team is None:
        return {'reachable': True, 'status': 'not_registered', 'llm_invocation': 'not_run'}
    return {'reachable': True, 'status': 'registered',
            'enabled': bool(team.get('enabled')), 'load_error': team.get('loadError', ''),
            'version': team.get('version'), 'kind': team.get('kind'),
            'llm_invocation': 'not_run',
            'note': 'registry readiness only; no team LLM call was made or claimed'}


def verify_native_read(timeout=10):
    """Verify the product-rules knowledge actually loads through the host's NATIVE team
    resource channel (teamResources read — the only channel generic member `read` cannot
    reach for agentsRoot paths). Same call shape the coordinator proved 200 with:
    GET /api/agents/read?name=storyboardTeam, Origin + x-toonflow-workspace headers.
    Honest fallback when the loopback service is down: service_not_running, not a pass.
    This proves native loading/authorization only — never an LLM invocation."""
    request = urllib.request.Request(TEAM_READ_API, headers={
        'Origin': 'http://127.0.0.1:3000', 'x-toonflow-workspace': '1'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read().decode('utf-8', errors='replace')
    except Exception as exc:  # noqa: BLE001 — transport failure is an honest status
        return {'reachable': False, 'status': 'service_not_running', 'error': str(exc)[:200],
                'llm_invocation': 'not_run'}
    found = KNOWLEDGE_MARKER in body and 'product-materials' in body
    return {'reachable': True, 'status': 'native_read_ok' if found else 'knowledge_not_in_response',
            'knowledge_loadable_natively': found, 'llm_invocation': 'not_run',
            'note': 'teamResources native load/authorization evidence; no team LLM call made or claimed'}


def readiness_report():
    files = verify_team_files()
    live = verify_live_registry()
    native = verify_native_read()
    ok = not files['problems'] and live.get('status') == 'registered' and live.get('enabled') \
        and not live.get('load_error') and native.get('status') == 'native_read_ok'
    return {'ready': ok, 'files': files, 'live_registry': live, 'native_read': native,
            'llm_invocation': 'not_run', 'budget': koc_rules.budget()}
