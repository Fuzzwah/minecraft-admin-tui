#!/usr/bin/env python3
"""Host-side, Podman-only controller for the vanilla Minecraft transfer hub."""
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import io
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import socket
import struct
import tempfile
import threading
import time
import uuid
import zipfile

import mc_admin_core as core

DEFAULT_CONFIG = Path.home() / '.config/mc-admin-tui/hub.json'
DEFAULT_STATE = Path.home() / '.local/share/mc-admin-tui/hub-state.json'
LOG = logging.getLogger(__name__)
PLAYER = re.compile(r'[A-Za-z0-9_]{1,16}\Z')
CONTAINER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*\Z')
ACTIONS = ('join', 'start', 'stop')


class RconClient:
    """Persistent, serialized RCON with an ordered response delimiter.

    Minecraft replies to every type-2 request, including an empty command. Sending
    a second request with a distinct ID avoids timeout/packet-size heuristics when
    the first response spans several packets. A failed request is never replayed.
    """
    def __init__(self, host: str, port: int, password: str, timeout: float = 8):
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self._socket = None
        self._lock = threading.RLock()
        self._id = 0

    def _next_id(self):
        self._id = self._id % 2147483646 + 1
        return self._id

    def _send(self, request_id, kind, text):
        payload = text.encode('utf-8')
        if b'\0' in payload or len(payload) > 1024 * 1024:
            raise ValueError('Invalid RCON request')
        packet = struct.pack('<ii', request_id, kind) + payload + b'\0\0'
        self._socket.sendall(struct.pack('<i', len(packet)) + packet)

    def _exact(self, count):
        parts = bytearray()
        while len(parts) < count:
            data = self._socket.recv(count - len(parts))
            if not data:
                raise ConnectionError('RCON connection closed within a packet')
            parts.extend(data)
        return bytes(parts)

    def _receive(self):
        size, = struct.unpack('<i', self._exact(4))
        if not 10 <= size <= 4 * 1024 * 1024:
            raise ConnectionError('Invalid RCON packet length')
        packet = self._exact(size)
        if packet[-2:] != b'\0\0':
            raise ConnectionError('Invalid RCON packet terminator')
        request_id, kind = struct.unpack('<ii', packet[:8])
        return request_id, kind, packet[8:-2]

    def _connect(self):
        self._socket = socket.create_connection((self.host, self.port), self.timeout)
        self._socket.settimeout(self.timeout)
        request_id = self._next_id()
        self._send(request_id, 3, self.password)
        for _ in range(4):
            response_id, kind, _ = self._receive()
            if response_id == -1:
                raise PermissionError('RCON authentication failed')
            if response_id != request_id:
                raise ConnectionError('Unexpected RCON authentication response')
            if kind == 2:
                return
            if kind != 0:
                raise ConnectionError('Invalid RCON authentication packet')
        raise ConnectionError('Missing RCON authentication response')

    def command(self, command: str) -> str:
        with self._lock:
            try:
                if self._socket is None:
                    self._connect()
                request_id, delimiter = self._next_id(), self._next_id()
                self._send(request_id, 2, command)
                parts, length = [], 0
                delimited = False
                while True:
                    response_id, kind, payload = self._receive()
                    if response_id == -1:
                        raise PermissionError('RCON authentication failed')
                    if kind != 0:
                        raise ConnectionError('Unexpected RCON response type')
                    if response_id == delimiter and delimited:
                        return b''.join(parts).decode('utf-8', errors='replace')
                    if response_id != request_id:
                        raise ConnectionError('Unexpected RCON response ID')
                    length += len(payload)
                    if length > 16 * 1024 * 1024:
                        raise ConnectionError('RCON response exceeds safety limit')
                    parts.append(payload)
                    if not delimited:
                        # Vanilla's request reader does not reliably retain a
                        # coalesced second frame. Wait for the first reply before
                        # sending the delimiter, while still draining all parts.
                        self._send(delimiter, 2, '')
                        delimited = True
            except Exception:
                self.close()
                raise

    def close(self):
        with self._lock:
            if self._socket is not None:
                self._socket.close()
                self._socket = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def load_config(path=DEFAULT_CONFIG):
    path = Path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError('Hub configuration must be private (chmod 600)')
    return json.loads(path.read_text())


def canonical_uuid(value):
    if not isinstance(value, str):
        raise ValueError('Invalid player UUID')
    parsed = uuid.UUID(value)
    if str(parsed) != value.lower():
        raise ValueError('Player UUID must use canonical dashed notation')
    return str(parsed)


def parse_entity_uuid(output):
    match = re.search(r'\[I;\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\]', output)
    if not match:
        raise ValueError('Cannot authenticate player UUID')
    words = [int(n) for n in match.groups()]
    if any(not -(2**31) <= n < 2**31 for n in words):
        raise ValueError('Invalid entity UUID')
    return str(uuid.UUID(bytes=struct.pack('>IIII', *(n & 0xffffffff for n in words))))


def parse_player_list(output):
    match = re.search(r'There are (\d+) of a max of \d+ players online:\s*(.*?)\s*\Z', output, re.I)
    if not match:
        raise ValueError('Player count unavailable')
    count = int(match[1])
    names = [name.strip() for name in match[2].split(',') if name.strip()]
    if len(names) != count or len(set(names)) != count or any(not PLAYER.fullmatch(n) for n in names):
        raise ValueError('Invalid player list')
    return names


def validate_address(address):
    if not isinstance(address, str) or not address or len(address) > 253:
        raise ValueError('Invalid destination address')
    try:
        value = ipaddress.ip_address(address)
    except ValueError:
        labels = address.rstrip('.').split('.')
        if (address.casefold().rstrip('.') in ('localhost', 'localhost.localdomain')
            or address.casefold().rstrip('.').endswith('.localhost')
            or all(label.isdigit() for label in labels) or any(
            not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
            for label in labels
        )):
            raise ValueError('Invalid or local-only destination address')
    else:
        if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped:
            value = value.ipv4_mapped
        if value.is_loopback or value.is_unspecified or value.is_multicast or value.is_link_local:
            raise ValueError('Destination address is not player-reachable')
    return address


def running(info):
    state = info.get('State') or {}
    return state.get('Running') is True or str(state.get('Status', '')).lower() == 'running'


def port_bindings(info):
    result = []
    bindings = (info.get('HostConfig') or {}).get('PortBindings') or {}
    for spec, entries in bindings.items():
        protocol = str(spec).split('/')[-1] if '/' in str(spec) else 'tcp'
        for entry in entries or []:
            port = str(entry.get('HostPort', ''))
            if port.isdigit() and 0 < int(port) <= 65535:
                result.append((entry.get('HostIp') or '0.0.0.0', int(port), protocol))
    return result


def player_reachable_binding(address):
    try:
        value = ipaddress.ip_address(address or '0.0.0.0')
    except ValueError:
        return False
    if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped:
        value = value.ipv4_mapped
    return not (value.is_loopback or value.is_link_local or value.is_multicast)


def binding_overlap(left, right):
    if left[1:] != right[1:]:
        return False
    try:
        addresses = [ipaddress.ip_address(binding[0] or '0.0.0.0') for binding in (left, right)]
    except ValueError:
        return True  # Unknown binding semantics cannot safely authorize a start.
    addresses = [address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
                 for address in addresses]
    # An IPv6 wildcard may be dual stack. Refusing overlap is safer than assuming
    # IPV6_V6ONLY or Podman's forwarder implementation.
    return any(address.is_unspecified for address in addresses) or addresses[0] == addresses[1]


def collision(target, others):
    wanted = port_bindings(target)
    for name, info in others:
        if running(info):
            for bound in port_bindings(info):
                if any(binding_overlap(bound, candidate) for candidate in wanted):
                    return f'Host port {bound[1]}/{bound[2]} conflicts with {name}'
    return None


class Controller:
    def __init__(self, config, state_path=DEFAULT_STATE, *, client=None, clock=time.monotonic):
        self.config = config
        self.state_path = Path(state_path)
        self.clock = clock
        hub = config.get('hub', {})
        self.hub_name = hub.get('container', 'mc_hub')
        if not isinstance(self.hub_name, str) or not CONTAINER.fullmatch(self.hub_name):
            raise ValueError('Invalid hub container name')
        host = hub.get('rcon_host', '127.0.0.1')
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError('Hub RCON must use a loopback address')
        self.admins = {canonical_uuid(value) for value in config.get('admins', [])}
        self.targets = config.get('targets', {})
        if not isinstance(self.targets, dict) or any(not CONTAINER.fullmatch(n) or not isinstance(v, dict) for n, v in self.targets.items()):
            raise ValueError('Invalid target enrollment')
        self.client = client or RconClient(host, int(hub.get('rcon_port', 25579)), hub.get('rcon_password', ''))
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {'slots': {}, 'generated': []}
        self.slots = self.state.setdefault('slots', {})
        self.generated = self.state.setdefault('generated', [])
        if (not isinstance(self.slots, dict) or not isinstance(self.generated, list)
            or any(not isinstance(n, str) or not CONTAINER.fullmatch(n) or type(i) is not int or not 1 <= i <= 128 for n, i in self.slots.items())
            or len(set(self.slots.values())) != len(self.slots)
            or any(type(i) is not int or i not in self.slots.values() for i in self.generated)
            or self.hub_name in self.slots):
            raise ValueError('Invalid persistent bay mapping')
        self.records = {}
        self.confirmations = {}
        self.observed = {}
        self.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='hub')
        self._start_lock = threading.Lock()
        self.jobs: dict[str, tuple[Future, str, tuple | None]] = {}
        self.scan_job = None
        self.next_scan = 0
        self.last_render = {}
        self.enabled = False
        self.last_error = ''

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix='.hub-', dir=self.state_path.parent)
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump(self.state, handle, sort_keys=True)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(path, self.state_path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @staticmethod
    def _scan():
        result = {}
        for item in core.list_containers('podman'):
            image = core.container_image(item)
            if image and not core.is_minecraft_image(image):
                continue
            name = core.container_name(item)
            if name and CONTAINER.fullmatch(name):
                # Inspect provides current authoritative image, state and ports.
                info = core.inspect_container('podman', name)
                if core.is_minecraft_image(core.container_image(info)):
                    result[name] = info
        return result

    def _apply_scan(self, discovered):
        new = False
        unmapped = [name for name in discovered if name != self.hub_name and name not in self.slots]
        if max(self.slots.values(), default=0) + len(unmapped) > 128:
            raise RuntimeError('Hub has exhausted its 128 stable bay IDs')
        self.hub_info = discovered.get(self.hub_name)
        for name in sorted(discovered):
            if name == self.hub_name:
                continue
            if name not in self.slots:
                slot = max(self.slots.values(), default=0) + 1
                self.slots[name] = slot
                new = True
        if new:
            self.save()
        for name in self.slots:
            old = self.records.get(name, {})
            info = discovered.get(name)
            if name in self.jobs:
                old['info'] = info
                self.records[name] = old
                continue
            state = 'Missing' if info is None else ('Checking' if running(info) else 'Offline')
            if info and running(info) and old.get('ready_deadline', 0) > self.clock():
                state = 'Starting'
            self.records[name] = {**old, 'info': info, 'state': state, 'detail': '', 'joinable': False, 'players': 0}
            if info is not None and running(info):
                self.jobs[name] = (self.executor.submit(self._probe, name), 'probe', None)
        if self.enabled:
            self._generate()

    def refresh(self):
        self._apply_scan(self._scan())
        return self.records

    def _generate(self):
        import mc_hub_world as world
        from mc_hub_deploy import install_datapack
        pending = [dict(id=i, name=n) for n, i in self.slots.items() if i not in self.generated]
        if not pending:
            return
        slots = [dict(id=i, name=n) for n, i in self.slots.items()]
        signature = sorted(self.slots.values())
        if self.state.get('datapack_slots') != signature:
            install_datapack(self.hub_name, world.datapack_files(slots))
            self.client.command('reload')
            self.state['datapack_slots'] = signature
            self.save()
        for slot in pending:
            x = 16 * ((slot['id'] - 1) % 16)
            z = 16 + 16 * ((slot['id'] - 1) // 16)
            self.client.command(f'forceload add {x} {z}')
            if 'Test passed' not in self.client.command(f'execute if loaded {x} 64 {z}'):
                # Chunk tickets load asynchronously; finish on a later scan.
                continue
            for command in world.bay_commands(slot):
                response = self.client.command(command)
                if re.search(r'(outside of the world|not loaded|unknown or incomplete command|incorrect argument)', response, re.I):
                    raise RuntimeError(f'Bay {slot["id"]} generation failed: {response}')
            # Empty vanilla servers can pause before the autosave tick. Persist
            # the world before recording that this bay no longer needs building.
            self.client.command('save-all flush')
            self.generated.append(slot['id'])
            self.save()

    def _inspect_target(self, name):
        if name == self.hub_name or name not in self.slots:
            raise ValueError('Invalid target')
        info = core.inspect_container('podman', name)
        if core.container_name(info) != name or not core.is_minecraft_image(core.container_image(info)):
            raise RuntimeError('Target is no longer the discovered Minecraft container')
        return info

    @staticmethod
    def _server(name, info, properties=None):
        env = core.container_env(info)
        properties = properties or {}
        return core.ServerConfig('podman', name,
            rcon_password=properties.get('rcon.password', env.get('RCON_PASSWORD')),
            rcon_port=int(properties.get('rcon.port', env.get('RCON_PORT', '25575'))),
            working_dir=core.container_workdir(info) or '/data')

    def _metadata(self, name, info):
        server = self._server(name, info)
        settings = core.load_server_settings(server)
        return settings.values, self._server(name, info, settings.values)

    def _endpoint(self, name, info, properties):
        configured = self.targets.get(name, {})
        address = validate_address(configured.get('address', self.config.get('address')))
        game_port = int(properties.get('server-port', '25565'))
        port = configured.get('port', core.container_host_port(info, game_port))
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('No valid published destination game port')
        if 'port' not in configured:
            bindings = (info.get('HostConfig') or {}).get('PortBindings', {}).get(f'{game_port}/tcp', [])
            if not any(str(b.get('HostPort', '')) == str(port) and player_reachable_binding(b.get('HostIp', '')) for b in bindings):
                raise ValueError('Destination game port is only locally published')
        return address, port

    def _probe(self, name):
        info = self._inspect_target(name)
        if not running(info):
            return {'info': info, 'state': 'Offline', 'detail': '', 'players': 0, 'joinable': False}
        properties, server = self._metadata(name, info)
        players = parse_player_list(core.rcon(server, 'list'))
        detail = ''
        env = core.container_env(info)
        if properties.get('online-mode', '').lower() != 'true':
            detail = 'Requires authenticated online-mode=true'
        elif env.get('TYPE', '').upper() != 'VANILLA':
            detail = 'Requires vanilla Minecraft'
        elif properties.get('accepts-transfers', 'false').lower() != 'true':
            detail = 'Needs restart: accepts-transfers=false'
        elif env.get('VERSION') != '26.3':
            detail = 'Requires Minecraft 26.3'
        else:
            try:
                self._endpoint(name, info, properties)
            except ValueError as error:
                detail = str(error)
        return {'info': info, 'state': 'Online', 'detail': detail, 'players': len(players), 'joinable': not detail}

    def assert_hub(self):
        info = core.inspect_container('podman', self.hub_name)
        env = core.container_env(info)
        if (core.container_name(info) != self.hub_name or not core.is_minecraft_image(core.container_image(info))
            or not running(info) or env.get('TYPE', '').upper() != 'VANILLA' or env.get('VERSION') != '26.3'):
            raise RuntimeError('Hub must be a running vanilla Minecraft 26.3 Podman container')
        properties, server = self._metadata(self.hub_name, info)
        if properties.get('online-mode', '').lower() != 'true':
            raise RuntimeError('Hub must actually run with online-mode=true')
        jar, _ = core._property_file_from_tar(core._copy_path_stream(server, '/data/versions/26.3/server-26.3.jar'), 'server-26.3.jar')
        with zipfile.ZipFile(io.BytesIO(jar)) as archive:
            version = json.loads(archive.read('version.json'))
        if version.get('id') != '26.3':
            raise RuntimeError('Actual hub server jar is not Minecraft 26.3')
        published = [b for b in port_bindings(info) if b[1] == int(self.config['hub'].get('rcon_port', 25579))]
        if not published or any(not ipaddress.ip_address(b[0]).is_loopback for b in published):
            raise RuntimeError('Hub RCON must be published only on loopback')
        parse_player_list(self.client.command('list'))

    def _actor(self, name, identity):
        if not isinstance(name, str) or not PLAYER.fullmatch(name):
            raise ValueError('Invalid player name')
        identity = canonical_uuid(identity)
        actual = parse_entity_uuid(self.client.command(f'data get entity {name} UUID'))
        if actual != identity:
            raise PermissionError('Player UUID does not match the authenticated online entity')
        return identity

    def _notify(self, name, message):
        if PLAYER.fullmatch(name):
            self.client.command(f'tellraw {name} ' + json.dumps({'text': message, 'color': 'yellow'}))

    def handle_request(self, player_name, player_uuid, action, slot_id):
        try:
            if action not in ACTIONS or type(slot_id) is not int or slot_id <= 0:
                return 'Invalid hub request'
            if not isinstance(player_name, str) or not PLAYER.fullmatch(player_name):
                return 'Invalid player name'
            identity = canonical_uuid(player_uuid)
            name = next((n for n, i in self.slots.items() if i == slot_id), None)
            if name is None or name == self.hub_name:
                return 'Unknown destination bay'
            if action != 'join' and (identity not in self.admins or self.targets.get(name, {}).get('control') is not True):
                return 'Denied: administrator UUID and explicit target enrollment required'
            self._actor(player_name, identity)
            if name in self.jobs:
                return 'Destination busy; try again shortly'
            if action == 'stop':
                key = identity, name
                expiry = self.confirmations.pop(key, None)
                if expiry is None or expiry < self.clock():
                    self.confirmations[key] = self.clock() + 10
                    return 'Click Stop again within 10 seconds to confirm; occupied servers will not stop'
            future = self.executor.submit(self._operate, name, action)
            self.jobs[name] = future, action, (player_name, identity)
            record = self.records.setdefault(name, {})
            record.update(state={'start': 'Starting', 'stop': 'Stopping', 'join': 'Checking'}[action], detail='', joinable=False)
            return {'start': 'Starting destination', 'stop': 'Stopping destination', 'join': 'Checking destination readiness'}[action]
        except Exception as error:
            LOG.warning('Rejected hub request: %s', error)
            return str(error)

    def _operate(self, name, action):
        if action == 'start':
            # Different targets can share a published port. Serialize the check
            # and start boundary across workers, without blocking the poll loop.
            with self._start_lock:
                return self._operate_unlocked(name, action)
        return self._operate_unlocked(name, action)

    def _operate_unlocked(self, name, action):
        info = self._inspect_target(name)
        if action == 'join':
            result = self._probe(name)
            if not result['joinable']:
                raise RuntimeError(result['detail'] or 'Destination is not running and ready')
            properties, _ = self._metadata(name, info)
            result['endpoint'] = self._endpoint(name, info, properties)
            return result
        if self.targets.get(name, {}).get('control') is not True:
            raise PermissionError('Target is not enrolled for lifecycle controls')
        if action == 'start':
            if running(info):
                result = self._probe(name)
                result['message'] = 'Already running; active servers are never restarted'
                return result
            env = core.container_env(info)
            if env.get('VERSION') != '26.3' or env.get('TYPE', '').upper() != 'VANILLA':
                raise RuntimeError('Starting requires an enrolled vanilla Minecraft 26.3 target')
            others = []
            for item in core.list_containers('podman'):
                other_name = core.container_name(item)
                if other_name and other_name != name:
                    others.append((other_name, core.inspect_container('podman', other_name)))
            conflict = collision(info, others)
            if conflict:
                raise RuntimeError(conflict)
            from mc_hub_deploy import configure_transfer_target
            configure_transfer_target(name)
            # Reinspect after the properties edit; never restart a server that was
            # started independently while this request was being prepared.
            current = self._inspect_target(name)
            if running(current):
                raise RuntimeError('Target started independently; no lifecycle action taken')
            conflict = collision(current, [(n, core.inspect_container('podman', n)) for n, _ in others])
            if conflict:
                raise RuntimeError(conflict)
            core.container_command(self._server(name, current), 'start')
            return {'info': current, 'state': 'Starting', 'detail': 'Waiting for Minecraft RCON readiness', 'players': 0, 'joinable': False,
                    'ready_deadline': self.clock() + 180, 'message': 'Container started; wait for Online before joining'}
        if not running(info):
            return {'info': info, 'state': 'Offline', 'detail': '', 'players': 0, 'joinable': False, 'message': 'Already stopped'}
        properties, server = self._metadata(name, info)
        if parse_player_list(core.rcon(server, 'list')):
            raise RuntimeError('Stop refused: destination has online players')
        # Check immediately before graceful stop, not a stale displayed count.
        if parse_player_list(core.rcon(server, 'list')):
            raise RuntimeError('Stop refused: a player joined the destination')
        core.stop_container(server)
        return {'info': info, 'state': 'Offline', 'detail': '', 'players': 0, 'joinable': False, 'message': 'Destination stopped gracefully'}

    def _drain(self):
        for name, (future, action, actor) in list(self.jobs.items()):
            if not future.done():
                continue
            del self.jobs[name]
            try:
                result = future.result()
                self.records[name] = result
                if actor:
                    player, identity = actor
                    if action == 'join':
                        self._actor(player, identity)
                        address, port = result['endpoint']
                        response = self.client.command(f'execute as {player} run transfer {address} {port}')
                        if response.strip() != f'Transferring {player} to {address}:{port}':
                            raise RuntimeError('Transfer was not acknowledged: ' + (response or 'empty response'))
                    else:
                        self._notify(player, result.get('message', result['state']))
            except Exception as error:
                LOG.warning('%s %s failed: %s', name, action, error)
                record = self.records.setdefault(name, {})
                starting = action == 'probe' and record.get('ready_deadline', 0) > self.clock()
                record.update(state='Starting' if starting else 'Error', detail=str(error), joinable=False)
                if actor:
                    self._notify(actor[0], str(error))
        if self.scan_job is not None and self.scan_job.done():
            job, self.scan_job = self.scan_job, None
            try:
                self._apply_scan(job.result())
            except Exception as error:
                self.last_error = str(error)
                LOG.warning('Hub discovery failed: %s', error)

    def _render(self):
        import mc_hub_world as world
        for name, slot_id in self.slots.items():
            if slot_id not in self.generated:
                continue
            record = self.records.get(name, {})
            state = record.get('state', 'Missing')
            detail = record.get('detail', '')
            players = record.get('players', 0)
            controllable = self.targets.get(name, {}).get('control') is True
            joinable = record.get('joinable', False)
            signature = state, detail, players, controllable, joinable
            if self.last_render.get(name) == signature:
                continue
            for command in world.status_commands({'id': slot_id, 'name': name}, state, detail, players, controllable, joinable):
                self.client.command(command)
            self.last_render[name] = signature

    def poll(self):
        self._drain()
        now = self.clock()
        if self.scan_job is None and now >= self.next_scan:
            self.scan_job = self.executor.submit(self._scan)
            self.next_scan = now + max(1, float(self.config.get('scan_seconds', 10)))
        names = parse_player_list(self.client.command('list'))
        seen = {}
        for name in names:
            try:
                identity = parse_entity_uuid(self.client.command(f'data get entity {name} UUID'))
            except ValueError as error:
                # A player can leave between list and data; do not starve others.
                LOG.warning('Cannot authenticate %s: %s', name, error)
                continue
            self.client.command(f'tag {name} ' + ('add' if identity in self.admins else 'remove') + ' hub_admin')
            seen[name] = identity
            if self.observed.get(name) != identity:
                for action in ACTIONS:
                    self.client.command(f'scoreboard players set {name} hub_{action} 0')
                continue
            requests = []
            for action in ACTIONS:
                response = self.client.command(f'scoreboard players get {name} hub_{action}')
                match = re.fullmatch(rf'{re.escape(name)} has (-?\d+) \[hub_{action}\]\s*', response)
                if not match:
                    # An objective that has no score is safe only if initialized.
                    self.client.command(f'scoreboard players set {name} hub_{action} 0')
                    continue
                score = int(match[1])
                if score:
                    self.client.command(f'scoreboard players set {name} hub_{action} 0')
                    requests.append((action, score))
            # Clear all scores before dispatching even if a request fails.
            for action, score in requests:
                outcome = self.handle_request(name, identity, action, score)
                self._notify(name, outcome)
        self.observed = seen
        if self.enabled:
            self._render()

    def status(self):
        hub_info = getattr(self, 'hub_info', None)
        return {'hub': {'container': self.hub_name, 'state': 'Running' if hub_info and running(hub_info) else 'Unavailable',
                        'version': core.container_env(hub_info).get('VERSION', '') if hub_info else ''},
                'error': self.last_error, 'bays': [
            {'id': slot, 'name': name, 'state': self.records.get(name, {}).get('state', 'Missing'),
             'detail': self.records.get(name, {}).get('detail', ''),
             'players': self.records.get(name, {}).get('players', 0),
             'joinable': self.records.get(name, {}).get('joinable', False),
             'control': self.targets.get(name, {}).get('control') is True}
            for name, slot in sorted(self.slots.items(), key=lambda pair: pair[1])]}

    def run(self):
        self.assert_hub()
        self.enabled = True
        self.refresh()
        while True:
            try:
                self.poll()
            except Exception as error:
                # A broken connection never replays a command. Upon reconnect all
                # scores are discarded again before admitting new requests.
                self.observed.clear()
                self.last_error = str(error)
                self.client.close()
                LOG.warning('Hub poll failed closed: %s', error)
            time.sleep(max(0.1, float(self.config.get('poll_seconds', 1))))

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--start-hub', action='store_true',
                        help='Start the configured hub and wait for RCON before running')
    parser.add_argument('command', choices=('run', 'status'))
    args = parser.parse_args(argv)
    if args.start_hub and args.command != 'run':
        parser.error('--start-hub requires run')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    controller = None
    try:
        controller = Controller(load_config(args.config), args.state)
        if args.command == 'run':
            if args.start_hub:
                from mc_hub_deploy import wait_for_hub
                # Keep rootless networking under the long-lived service, not
                # ExecStartPre, whose children systemd kills before ExecStart.
                core.run_process(['podman', 'start', controller.hub_name], timeout=60)
                wait_for_hub(controller.config['hub']).close()
            controller.run()
        else:
            controller.refresh()
            # Status performs no world writes or lifecycle action. Wait for the
            # finite readiness probes to expose errors instead of just Running.
            for future, _, _ in list(controller.jobs.values()):
                try:
                    future.result()
                except Exception:
                    pass
            controller._drain()
            print(json.dumps(controller.status(), indent=2))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        LOG.error('%s', error)
        return 1
    finally:
        if controller is not None:
            controller.close()


if __name__ == '__main__':
    raise SystemExit(main())
