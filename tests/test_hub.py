import io
import json
from pathlib import Path
import socket
import struct
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import zipfile

import mc_hub as hub

ADMIN = '16dbe74b-f98f-42d8-8850-9d9d07fe0997'
GUEST = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'


def entity(identity):
    words = struct.unpack('>iiii', uuid.UUID(identity).bytes)
    return 'Alice has the following entity data: [I; ' + ', '.join(map(str, words)) + ']'


def container(name='target', *, active=False, port=25565, host='0.0.0.0', image='itzg/minecraft-server', version='26.3'):
    return {'Name': '/' + name, 'Config': {'Image': image, 'Env': [
        'VERSION=' + version, 'TYPE=VANILLA', 'ONLINE_MODE=true', 'OVERRIDE_SERVER_PROPERTIES=false']},
        'State': {'Running': active, 'Status': 'running' if active else 'exited'},
        'HostConfig': {'PortBindings': {'25565/tcp': [{'HostIp': host, 'HostPort': str(port)}]}}}


def settings(*, transfers='true', online='true'):
    return hub.core.ServerSettings('/data/server.properties', b'', {
        'accepts-transfers': transfers, 'online-mode': online, 'rcon.password': 'secret', 'rcon.port': '25575'}, None)


class GameClient:
    def __init__(self, identity=ADMIN):
        self.identity = identity
        self.commands = []
        self.players = ['Alice']
        self.scores = {action: 0 for action in hub.ACTIONS}

    def command(self, command):
        self.commands.append(command)
        if command == 'list':
            return f'There are {len(self.players)} of a max of 20 players online: ' + ', '.join(self.players)
        if command.startswith('data get entity '):
            return entity(self.identity)
        if command.startswith('scoreboard players get '):
            action = command.rsplit('_', 1)[1]
            return f'Alice has {self.scores[action]} [hub_{action}]'
        if command.startswith('scoreboard players set '):
            objective, value = command.split()[-2:]
            self.scores[objective.removeprefix('hub_')] = int(value)
        if ' run transfer ' in command:
            parts = command.split()
            return f'Transferring {parts[2]} to {parts[-2]}:{parts[-1]}'
        return 'OK'

    def close(self):
        pass


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.path.write_text(json.dumps({'slots': {'target': 1}, 'generated': [1]}))
        self.client = GameClient()
        self.now = 100.0
        self.config = {'hub': {'container': 'mc_hub', 'rcon_host': '127.0.0.1', 'rcon_port': 25579, 'rcon_password': 'secret'},
                       'address': '10.1.1.232', 'admins': [ADMIN], 'targets': {'target': {'control': True}}, 'scan_seconds': 10}
        self.controller = hub.Controller(self.config, self.path, client=self.client, clock=lambda: self.now)
        self.controller.next_scan = float('inf')
        self.addCleanup(self.controller.close)

    def complete(self, name='target'):
        future = self.controller.jobs[name][0]
        try:
            future.result(timeout=5)
        except Exception:
            pass
        self.controller._drain()

    def test_denied_invalid_and_unenrolled_requests_never_touch_podman(self):
        with patch.object(hub.core, 'inspect_container') as inspect, patch.object(hub.core, 'list_containers') as listing:
            for name, identity, action, slot in [
                ('Alice', GUEST, 'stop', 1), ('Alice', ADMIN, 'shell', 1),
                ('Alice; stop', ADMIN, 'start', 1), ('@a', ADMIN, 'join', 1),
                ('Alice', 'not-a-uuid', 'start', 1), ('Alice', ADMIN, 'start', True),
                ('Alice', ADMIN, 'stop', 999), ('Alice', ADMIN, 'join', -1),
            ]:
                outcome = self.controller.handle_request(name, identity, action, slot)
                self.assertNotIn(outcome, ('Starting destination', 'Stopping destination', 'Checking destination readiness'))
            self.controller.targets['target']['control'] = False
            self.assertIn('Denied', self.controller.handle_request('Alice', ADMIN, 'start', 1))
            inspect.assert_not_called()
            listing.assert_not_called()
            self.assertEqual(self.client.commands, [])

    def test_admin_uuid_cannot_be_claimed_by_another_online_player(self):
        self.client.identity = GUEST
        with patch.object(hub.core, 'inspect_container') as inspect:
            self.assertIn('does not match', self.controller.handle_request('Alice', ADMIN, 'start', 1))
            inspect.assert_not_called()
        self.assertFalse(self.controller.jobs)

    def test_hub_cannot_acquire_a_bay_even_from_corrupt_state(self):
        self.path.write_text(json.dumps({'slots': {'mc_hub': 1}, 'generated': []}))
        with self.assertRaisesRegex(ValueError, 'mapping'):
            hub.Controller(self.config, self.path, client=self.client)

    def test_discovery_keeps_removed_ids_and_never_execs_stopped_containers(self):
        found = {'zebra': container('zebra'), 'target': container(), 'rollback': container('rollback'),
                 'mc_hub': container('mc_hub', active=True), 'redis': container('redis', image='redis')}
        with patch.object(hub.core, 'list_containers', return_value=[{'Names': [name]} for name in found]) as listing, \
             patch.object(hub.core, 'inspect_container', side_effect=lambda runtime, name: found[name]) as inspect, \
             patch.object(hub.core, 'load_server_settings') as metadata:
            self.controller.refresh()
            metadata.assert_not_called()
            self.assertEqual(listing.call_args.args, ('podman',))
            self.assertTrue(all(call.args[0] == 'podman' for call in inspect.call_args_list))
        first = dict(self.controller.slots)
        self.assertNotIn('mc_hub', first)
        self.assertNotIn('redis', first)
        self.assertIn('rollback', first)
        self.controller._apply_scan({'alpha': container('alpha'), 'zebra': found['zebra']})
        self.assertEqual(self.controller.slots['target'], 1)
        self.assertEqual(self.controller.slots['zebra'], first['zebra'])
        self.assertGreater(self.controller.slots['alpha'], max(first.values()))
        self.assertEqual(self.controller.records['target']['state'], 'Missing')
        replacement = hub.Controller(self.config, self.path, client=GameClient())
        self.addCleanup(replacement.close)
        self.assertEqual(replacement.slots, self.controller.slots)
        self.assertEqual(self.controller.status()['hub']['state'], 'Unavailable')

    def test_startup_and_reconnect_discard_scores_before_admission(self):
        self.client.scores['start'] = 1
        with patch.object(hub.core, 'inspect_container') as inspect:
            self.controller.poll()
            self.assertEqual(self.client.scores['start'], 0)
            inspect.assert_not_called()
            self.assertIn('tag Alice add hub_admin', self.client.commands)
            self.client.identity = GUEST
            self.client.scores['start'] = 1
            self.controller.poll()
            self.assertIn('tag Alice remove hub_admin', self.client.commands)
            self.assertEqual(self.client.scores['start'], 0)
            inspect.assert_not_called()
            self.controller.observed.clear()
            self.client.identity = ADMIN
            self.client.scores['stop'] = 1
            self.controller.poll()
            self.assertEqual(self.client.scores['stop'], 0)
            self.assertFalse(self.controller.confirmations)

    def test_poll_resets_all_requests_before_dispatch(self):
        self.controller.poll()
        self.client.scores.update(join=1, start=1, stop=1)
        observed = []
        def dispatch(*args):
            observed.append(dict(self.client.scores))
            return 'Denied for test'
        with patch.object(self.controller, 'handle_request', side_effect=dispatch):
            self.controller.poll()
        self.assertEqual(len(observed), 3)
        self.assertTrue(all(all(score == 0 for score in values.values()) for values in observed))

    def test_stop_confirmation_is_per_actor_and_expires(self):
        with patch.object(hub.core, 'inspect_container') as inspect:
            self.assertIn('confirm', self.controller.handle_request('Alice', ADMIN, 'stop', 1))
            self.now += 11
            self.assertIn('confirm', self.controller.handle_request('Alice', ADMIN, 'stop', 1))
            inspect.assert_not_called()
        self.assertFalse(self.controller.jobs)

    def test_stop_refuses_occupied_or_unavailable_count(self):
        for response in ('There are 1 of a max of 20 players online: Bob', 'RCON unavailable'):
            with self.subTest(response=response), patch.object(hub.core, 'inspect_container', return_value=container(active=True)), \
                 patch.object(hub.core, 'load_server_settings', return_value=settings()), \
                 patch.object(hub.core, 'rcon', return_value=response), patch.object(hub.core, 'stop_container') as stop:
                self.controller.confirmations.clear()
                self.assertIn('confirm', self.controller.handle_request('Alice', ADMIN, 'stop', 1))
                self.assertEqual(self.controller.handle_request('Alice', ADMIN, 'stop', 1), 'Stopping destination')
                self.complete()
                stop.assert_not_called()
                self.assertEqual(self.controller.records['target']['state'], 'Error')
                self.assertFalse(self.controller.records['target']['joinable'])

    def test_stop_checks_for_a_late_join_and_uses_graceful_core_stop(self):
        empty = 'There are 0 of a max of 20 players online: '
        for outputs, allowed in (([empty, 'There are 1 of a max of 20 players online: Bob'], False), ([empty, empty], True)):
            with self.subTest(allowed=allowed), patch.object(hub.core, 'inspect_container', return_value=container(active=True)), \
                 patch.object(hub.core, 'load_server_settings', return_value=settings()), \
                 patch.object(hub.core, 'rcon', side_effect=outputs), patch.object(hub.core, 'stop_container') as stop:
                self.controller.handle_request('Alice', ADMIN, 'stop', 1)
                self.controller.handle_request('Alice', ADMIN, 'stop', 1)
                self.complete()
                self.assertEqual(stop.call_count, int(allowed))
                if allowed:
                    self.assertEqual(self.controller.records['target']['state'], 'Offline')

    def test_active_server_with_transfers_disabled_is_not_restarted(self):
        with patch.object(hub.core, 'inspect_container', return_value=container(active=True)), \
             patch.object(hub.core, 'load_server_settings', return_value=settings(transfers='false')), \
             patch.object(hub.core, 'rcon', return_value='There are 0 of a max of 20 players online: '), \
             patch.object(hub.core, 'container_command') as start, patch.object(hub.core, 'stop_container') as stop:
            self.controller.handle_request('Alice', ADMIN, 'start', 1)
            self.complete()
            start.assert_not_called()
            stop.assert_not_called()
            self.assertIn('Needs restart', self.controller.records['target']['detail'])
            self.assertFalse(self.controller.records['target']['joinable'])
            self.assertFalse(any(' run transfer ' in cmd for cmd in self.client.commands))

    def test_start_refuses_running_port_conflict_before_property_edit(self):
        target, other = container(), container('other', active=True)
        with patch.object(hub.core, 'inspect_container', side_effect=lambda runtime, name: target if name == 'target' else other), \
             patch.object(hub.core, 'list_containers', return_value=[{'Names': ['target']}, {'Names': ['other']}]), \
             patch('mc_hub_deploy.configure_transfer_target') as configure, patch.object(hub.core, 'container_command') as start:
            self.controller.handle_request('Alice', ADMIN, 'start', 1)
            self.complete()
            configure.assert_not_called()
            start.assert_not_called()
            self.assertIn('conflicts with other', self.controller.records['target']['detail'])

    def test_start_configures_stopped_target_and_waits_for_readiness(self):
        with patch.object(hub.core, 'inspect_container', return_value=container()), \
             patch.object(hub.core, 'list_containers', return_value=[{'Names': ['target']}]), \
             patch('mc_hub_deploy.configure_transfer_target') as configure, patch.object(hub.core, 'container_command') as start:
            self.controller.handle_request('Alice', ADMIN, 'start', 1)
            self.complete()
            configure.assert_called_once_with('target')
            self.assertEqual(start.call_args.args[1:], ('start',))
            self.assertEqual(self.controller.records['target']['state'], 'Starting')
            self.assertFalse(self.controller.records['target']['joinable'])

    def test_changed_image_or_wrong_version_cannot_start(self):
        for info in (container(image='redis'), container(version='1.21.4')):
            with self.subTest(info=info), patch.object(hub.core, 'inspect_container', return_value=info), \
                 patch('mc_hub_deploy.configure_transfer_target') as configure, patch.object(hub.core, 'container_command') as start:
                self.controller.handle_request('Alice', ADMIN, 'start', 1)
                self.complete()
                configure.assert_not_called()
                start.assert_not_called()
                self.assertEqual(self.controller.records['target']['state'], 'Error')

    def test_join_transfers_only_verified_actor_to_ready_published_endpoint(self):
        with patch.object(hub.core, 'inspect_container', return_value=container(active=True, port=25580)), \
             patch.object(hub.core, 'load_server_settings', return_value=settings()), \
             patch.object(hub.core, 'rcon', return_value='There are 0 of a max of 20 players online: '):
            self.controller.handle_request('Alice', ADMIN, 'join', 1)
            self.complete()
        self.assertIn('execute as Alice run transfer 10.1.1.232 25580', self.client.commands)
        self.assertTrue(self.controller.records['target']['joinable'])

    def test_join_rechecks_actor_before_transfer(self):
        with patch.object(hub.core, 'inspect_container', return_value=container(active=True)), \
             patch.object(hub.core, 'load_server_settings', return_value=settings()), \
             patch.object(hub.core, 'rcon', return_value='There are 0 of a max of 20 players online: '):
            self.controller.handle_request('Alice', ADMIN, 'join', 1)
            self.client.identity = GUEST
            self.complete()
        self.assertFalse(any(' run transfer ' in cmd for cmd in self.client.commands))
        self.assertEqual(self.controller.records['target']['state'], 'Error')

    def test_per_target_worker_gate_keeps_poll_responsive(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(name, action):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test worker not released')
            return {'state': 'Offline', 'detail': '', 'joinable': False, 'players': 0}
        with patch.object(self.controller, '_operate', side_effect=blocked), patch.object(hub.core, 'inspect_container') as inspect:
            self.assertEqual(self.controller.handle_request('Alice', ADMIN, 'start', 1), 'Starting destination')
            self.assertTrue(entered.wait(2))
            self.assertIn('busy', self.controller.handle_request('Alice', ADMIN, 'start', 1))
            self.controller.poll()
            inspect.assert_not_called()
            self.assertIn('list', self.client.commands)
            release.set()
            self.complete()

    def test_unacknowledged_transfer_and_offline_authentication_are_not_joinable(self):
        with patch.object(hub.core, 'inspect_container', return_value=container(active=True)), \
             patch.object(hub.core, 'load_server_settings', return_value=settings(online='false')), \
             patch.object(hub.core, 'rcon', return_value='There are 0 of a max of 20 players online: '):
            self.controller.handle_request('Alice', ADMIN, 'join', 1)
            self.complete()
            self.assertFalse(any(' run transfer ' in command for command in self.client.commands))
            self.assertIn('online-mode', self.controller.records['target']['detail'])
        original = self.client.command
        def unacknowledged(command):
            return '' if ' run transfer ' in command else original(command)
        with patch.object(hub.core, 'inspect_container', return_value=container(active=True)), \
             patch.object(hub.core, 'load_server_settings', return_value=settings()), \
             patch.object(hub.core, 'rcon', return_value='There are 0 of a max of 20 players online: '), \
             patch.object(self.client, 'command', side_effect=unacknowledged):
            self.controller.handle_request('Alice', ADMIN, 'join', 1)
            self.complete()
            self.assertEqual(self.controller.records['target']['state'], 'Error')
            self.assertIn('not acknowledged', self.controller.records['target']['detail'])

    def test_recent_start_waits_for_rcon_then_becomes_online(self):
        self.controller.records['target'] = {'state': 'Starting', 'ready_deadline': self.now + 180}
        with patch.object(self.controller, '_probe', side_effect=RuntimeError('Connection refused')):
            self.controller._apply_scan({'target': container(active=True)})
            self.complete()
        self.assertEqual(self.controller.records['target']['state'], 'Starting')
        self.assertFalse(self.controller.records['target']['joinable'])
        with patch.object(hub.core, 'inspect_container', return_value=container(active=True)), \
             patch.object(hub.core, 'load_server_settings', return_value=settings()), \
             patch.object(hub.core, 'rcon', return_value='There are 0 of a max of 20 players online: '):
            self.controller._apply_scan({'target': container(active=True)})
            self.complete()
        self.assertEqual(self.controller.records['target']['state'], 'Online')
        self.assertTrue(self.controller.records['target']['joinable'])

    def test_new_bay_is_not_marked_generated_before_loaded_and_successful_build(self):
        self.controller.generated.clear()
        original = self.client.command
        loaded = False
        fail = True
        def command(request):
            if request.startswith('execute if loaded'):
                return 'Test passed, count: 1' if loaded else 'Test failed'
            if request.startswith('setblock') and fail:
                return 'Cannot place blocks outside of the world'
            return original(request)
        with patch.object(self.client, 'command', side_effect=command), \
             patch('mc_hub_deploy.install_datapack') as install, \
             patch('mc_hub_world.bay_commands', return_value=['setblock 1 64 1 stone']):
            self.controller._generate()
            self.assertEqual(self.controller.generated, [])
            loaded = True
            with self.assertRaisesRegex(RuntimeError, 'generation failed'):
                self.controller._generate()
            self.assertEqual(self.controller.generated, [])
            fail = False
            self.controller._generate()
            self.assertEqual(self.controller.generated, [1])
            install.assert_called_once()
        self.assertIn('setblock 1 64 1 stone', self.client.commands)

    def test_player_leaving_mid_poll_does_not_freeze_other_players(self):
        self.client.players = ['Alice', 'Bob']
        original = self.client.command
        def departing(command):
            if command == 'data get entity Alice UUID':
                return 'No entity was found'
            return original(command)
        with patch.object(self.client, 'command', side_effect=departing):
            self.controller.poll()
        self.assertEqual(self.controller.observed, {'Bob': ADMIN})
        self.assertIn('scoreboard players set Bob hub_join 0', self.client.commands)

    def test_hub_startup_checks_properties_and_actual_jar_version(self):
        info = container('mc_hub', active=True)
        info['HostConfig']['PortBindings']['25575/tcp'] = [{'HostIp': '127.0.0.1', 'HostPort': '25579'}]
        def jar_tar(version):
            jar = io.BytesIO()
            with zipfile.ZipFile(jar, 'w') as archive:
                archive.writestr('version.json', json.dumps({'id': version}))
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w') as archive:
                member = tarfile.TarInfo('server-26.3.jar')
                member.size = len(jar.getvalue())
                archive.addfile(member, io.BytesIO(jar.getvalue()))
            return output.getvalue()
        with patch.object(hub.core, 'inspect_container', return_value=info), \
             patch.object(hub.core, 'load_server_settings', return_value=settings(online='false')), \
             patch.object(hub.core, '_copy_path_stream') as copy:
            with self.assertRaisesRegex(RuntimeError, 'online-mode'):
                self.controller.assert_hub()
            copy.assert_not_called()
        with patch.object(hub.core, 'inspect_container', return_value=info), \
             patch.object(hub.core, 'load_server_settings', return_value=settings()), \
             patch.object(hub.core, '_copy_path_stream', return_value=jar_tar('1.21.4')):
            with self.assertRaisesRegex(RuntimeError, 'Actual hub server jar'):
                self.controller.assert_hub()
        with patch.object(hub.core, 'inspect_container', return_value=info), \
             patch.object(hub.core, 'load_server_settings', return_value=settings()), \
             patch.object(hub.core, '_copy_path_stream', return_value=jar_tar('26.3')):
            self.controller.assert_hub()


class BoundaryTests(unittest.TestCase):
    def test_port_binding_overlap_is_protocol_and_address_aware(self):
        target = container(port=25565, host='10.0.0.1')
        self.assertIsNone(hub.collision(target, [('other', container('other', active=True, host='10.0.0.2'))]))
        self.assertIsNotNone(hub.collision(target, [('other', container('other', active=True, host='0.0.0.0'))]))
        self.assertIsNotNone(hub.collision(target, [('other', container('other', active=True, host='::'))]))
        self.assertIsNone(hub.collision(target, [('other', container('other', active=False))]))
        self.assertFalse(hub.binding_overlap(('0.0.0.0', 25565, 'udp'), ('10.0.0.1', 25565, 'tcp')))
        self.assertTrue(hub.binding_overlap(('0:0:0:0:0:0:0:0', 25565, 'tcp'), ('10.0.0.1', 25565, 'tcp')))
        self.assertTrue(hub.binding_overlap(('::ffff:10.0.0.1', 25565, 'tcp'), ('10.0.0.1', 25565, 'tcp')))
        for address in ('127.0.0.2', '::ffff:127.0.0.1', '0:0:0:0:0:0:0:1'):
            self.assertFalse(hub.player_reachable_binding(address))
        self.assertTrue(hub.player_reachable_binding('0.0.0.0'))

    def test_addresses_and_player_counts_fail_closed(self):
        for address in ('127.0.0.1', '::1', '0.0.0.0', 'localhost', 'host;stop', '@a', 'host port', '-option', '169.254.1.1'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                hub.validate_address(address)
        self.assertEqual(hub.validate_address('minecraft.example.org'), 'minecraft.example.org')
        self.assertEqual(hub.validate_address('10.1.1.232'), '10.1.1.232')
        for output in ('', 'Unknown command', 'There are 2 of a max of 20 players online: Alice', 'There are 1 of a max of 20 players online: @a'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                hub.parse_player_list(output)
        self.assertEqual(hub.parse_entity_uuid(entity(ADMIN)), ADMIN)

    def test_configuration_secrets_require_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'hub.json'
            path.write_text('{}')
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'private'):
                hub.load_config(path)
            path.chmod(0o600)
            self.assertEqual(hub.load_config(path), {})


def frame(request_id, kind, data=b''):
    payload = struct.pack('<ii', request_id, kind) + data + b'\0\0'
    return struct.pack('<i', len(payload)) + payload


class FragmentSocket:
    def __init__(self, response):
        self.response = bytearray(response)
        self.sent = []
        self.closed = False
        self.received = 0
        self.sent_after_reads = []

    def recv(self, count):
        count = min(count, 3, len(self.response))
        result = bytes(self.response[:count])
        del self.response[:count]
        self.received += count
        return result

    def sendall(self, packet):
        self.sent.append(packet)
        self.sent_after_reads.append(self.received)

    def settimeout(self, timeout):
        pass

    def close(self):
        self.closed = True


class NativeRconTests(unittest.TestCase):
    def test_fragmented_auth_and_multipacket_utf8_response_remain_in_sync(self):
        encoded = ('snowman ☃ ' + 'x' * 5000).encode()
        response = frame(1, 0) + frame(1, 2) + frame(2, 0, encoded[:9]) + frame(2, 0, encoded[9:]) + frame(3, 0, b'Unknown command')
        response += frame(4, 0, b'next response') + frame(5, 0, b'Unknown command')
        connection = FragmentSocket(response)
        with patch.object(socket, 'create_connection', return_value=connection) as connect:
            with hub.RconClient('127.0.0.1', 25579, 'secret') as client:
                self.assertEqual(client.command('list'), encoded.decode())
                self.assertEqual(client.command('help'), 'next response')
            connect.assert_called_once()
        self.assertTrue(connection.closed)
        self.assertEqual(len(connection.sent), 5)
        self.assertEqual(connection.sent_after_reads[2],
                         len(frame(1, 0)) + len(frame(1, 2)) + len(frame(2, 0, encoded[:9])))

    def test_auth_rejection_never_sends_command_or_replays_request(self):
        connection = FragmentSocket(frame(-1, 2))
        with patch.object(socket, 'create_connection', return_value=connection):
            client = hub.RconClient('127.0.0.1', 25579, 'wrong')
            with self.assertRaises(PermissionError):
                client.command('stop')
        self.assertEqual(len(connection.sent), 1)
        self.assertTrue(connection.closed)

    def test_framing_and_eof_failures_close_without_replaying(self):
        for malformed in (struct.pack('<i', -1), struct.pack('<i', 9), struct.pack('<i', 5 * 1024 * 1024),
                          frame(2, 0, b'broken')[:-1], frame(2, 0, b'broken')[:-2] + b'xx', frame(999, 0, b'wrong id')):
            with self.subTest(packet=malformed[:20]):
                connection = FragmentSocket(frame(1, 2) + malformed)
                with patch.object(socket, 'create_connection', return_value=connection) as connect:
                    client = hub.RconClient('127.0.0.1', 25579, 'secret')
                    with self.assertRaises(ConnectionError):
                        client.command('save-all')
                    connect.assert_called_once()
                self.assertTrue(connection.closed)
                self.assertEqual(len(connection.sent), 2)


if __name__ == '__main__':
    unittest.main()
