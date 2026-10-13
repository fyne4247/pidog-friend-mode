#!/usr/bin/env python3
"""One hardware owner; OpenClaw explicitly chooses every motion and utterance."""
import argparse
import fcntl
import importlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time

from agent_events import DurableEventQueue, OpenClawEventWorker, SensorEventPoller

STATE_DIR = Path(os.environ.get('PIDOG_STATE_DIR', Path.home() / '.openclaw/pidog-control'))
SOCKET_PATH = STATE_DIR / 'controller.sock'
LOG_FILE = STATE_DIR / 'controller.log'
ACTION_MAP = {
    'stand': 'stand', 'sit': 'sit', 'lie': 'lie', 'wag-tail': 'wag_tail',
    'forward': 'forward', 'backward': 'backward', 'turn-left': 'turn_left',
    'turn-right': 'turn_right', 'trot': 'trot', 'stretch': 'stretch',
    'push-up': 'push_up', 'nod': 'head_up_down', 'shake-head': 'shake_head',
    'tilt-head-left': 'tilting_head_left', 'tilt-head-right': 'tilting_head_right',
}
# Finite, explicitly requested choreographies without audio or light cues.
GESTURE_MAP = {'scratch': 'scratch', 'handshake': 'hand_shake',
               'high-five': 'high_five', 'lick-hand': 'lick_hand'}
COLOR_MAP = {'red': (255, 0, 0), 'green': (0, 255, 0), 'blue': (0, 0, 255),
             'yellow': (255, 255, 0), 'purple': (128, 0, 255), 'pink': (255, 0, 128),
             'cyan': (0, 255, 255), 'white': (255, 255, 255), 'orange': (255, 128, 0),
             'black': (0, 0, 0)}
LIGHT_MODES = {'off', 'breath', 'listen', 'boom', 'solid'}


def parse_color(value):
    value = value.strip().lower()
    if value in COLOR_MAP:
        return COLOR_MAP[value]
    try:
        value = value.removeprefix('#')
        if len(value) == 6:
            return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Use a color name or #RRGGBB')


def number(value, name, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a number')
    if not math.isfinite(value) or not low <= value <= high or (integer and int(value) != value):
        raise ValueError(f'{name} must be between {low} and {high}')
    return int(value) if integer else value


class PiDogRuntime:
    def __init__(self):
        self.instance = None

    def connect(self):
        if self.instance is None:
            self.instance = importlib.import_module('pidog').Pidog(agent_control=True)
        return self.instance

    def close(self):
        if self.instance is not None:
            self.instance.close()
            self.instance = None


class PiDogController:
    def __init__(self, *, agent_id='main', openclaw_cli='openclaw', voice=False,
                 runtime=None, queue=None, tts_factory=None,
                 startup_posture='sit', shutdown_posture='lie'):
        self.runtime = runtime or PiDogRuntime()
        self.queue = queue or DurableEventQueue(STATE_DIR / 'events.sqlite3')
        self.worker = OpenClawEventWorker(self.queue, agent_id=agent_id, openclaw_cli=openclaw_cli)
        self.voice_enabled = voice
        self.tts_factory = tts_factory
        self.tts = None
        self.stop_event = threading.Event()
        self.speaking = threading.Event()
        self.speaking.generation = 0
        self.command_lock = threading.Lock()
        self.sensor_thread = None
        self.voice_thread = None
        self.sensor_error = None
        self.voice_error = None
        self.server = None
        self.ownership_lock = None
        self.started_at = time.monotonic()
        self.last_action = None
        for posture in (startup_posture, shutdown_posture):
            if posture not in {'sit', 'lie', 'stand', 'none'}:
                raise ValueError('Unsupported lifecycle posture')
        self.startup_posture = startup_posture
        self.shutdown_posture = shutdown_posture
        self._closed = False

    def _wait_motion(self, dog, timeout=30, stopping_allowed=False):
        deadline = time.monotonic() + timeout
        while not dog.is_all_done():
            interrupted = self.stop_event.wait(0.02) if not stopping_allowed else False
            if stopping_allowed:
                time.sleep(0.02)
            if interrupted or time.monotonic() >= deadline:
                dog.body_stop()
                raise RuntimeError('Motion interrupted or timed out')

    def _lifecycle_posture(self, dog, posture, stopping_allowed=False):
        if posture == 'none':
            return
        dog.body_stop()
        dog.do_action(posture, speed=60)
        if posture == 'sit':
            dog.head_move([[0, 0, 0]], pitch_comp=-35, speed=60)
        else:
            dog.head_move_raw([[0, 0, 0]], speed=60)
        dog.tail_move([[0]], speed=60)
        self._wait_motion(dog, timeout=10 if stopping_allowed else 30,
                          stopping_allowed=stopping_allowed)

    def action(self, name, speed=60, count=1):
        if name not in ACTION_MAP and name not in GESTURE_MAP:
            raise ValueError('Unsupported movement; sound effects are disabled')
        speed = number(speed, 'speed', 1, 100, integer=True)
        count = number(count, 'count', 1, 5, integer=True)
        with self.command_lock:
            if self.stop_event.is_set():
                raise RuntimeError('Controller is stopping')
            dog = self.runtime.connect()
            if name in ACTION_MAP:
                dog.actions_dict[ACTION_MAP[name]]
                dog.do_action(ACTION_MAP[name], speed=speed, step_count=count)
                self._wait_motion(dog)
            else:
                if count != 1:
                    raise ValueError('Composite gestures run once per request')
                presets = importlib.import_module('pidog.preset_actions')
                if name in {'handshake', 'high-five'}:
                    dog.do_action('sit', speed=speed)
                    self._wait_motion(dog)
                getattr(presets, GESTURE_MAP[name])(dog)
                self._wait_motion(dog)
            self.last_action = name
        return {'completed': name, 'count': count}

    def say(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError('Speech must contain 1–2000 characters of text')
        with self.command_lock:
            if self.stop_event.is_set():
                raise RuntimeError('Controller is stopping')
            self.speaking.generation += 1
            self.speaking.set()
            try:
                if self.tts is None:
                    factory = self.tts_factory or importlib.import_module('pidog.tts').Piper
                    self.tts = factory(model='en_US-ryan-low')
                self.tts.say(text)
            finally:
                self.speaking.clear()
        return {'spoken': True, 'engine': 'piper'}

    def light(self, mode, color=(255, 255, 255), bps=1, brightness=0.8):
        if mode not in LIGHT_MODES:
            raise ValueError('Unsupported light mode')
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise ValueError('color must contain three RGB values')
        color = tuple(number(c, 'color', 0, 255, integer=True) for c in color)
        bps = number(bps, 'bps', 0.1, 10)
        brightness = number(brightness, 'brightness', 0, 1)
        with self.command_lock:
            if self.stop_event.is_set():
                raise RuntimeError('Controller is stopping')
            dog = self.runtime.connect()
            if mode == 'off':
                dog.rgb_strip.set_mode('monochromatic', color='black')
            else:
                dog.rgb_strip.set_mode('boom' if mode == 'solid' else mode,
                                       color=color, bps=bps, brightness=brightness)
        return {'light': mode}

    def submit(self, kind, payload):
        if self.stop_event.is_set():
            raise RuntimeError('Controller is stopping')
        if kind not in {'voice', 'keyboard'} or not isinstance(payload, dict):
            raise ValueError('Only voice and keyboard text events may be submitted')
        text = payload.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Input must contain 1–4000 characters of text')
        return {'event_id': self.queue.enqueue(kind, {'text': text.strip()}), 'queued': True}

    def status(self):
        pending = self.queue.pending(limit=1)
        return {'running': not self.stop_event.is_set(), 'pid': os.getpid(),
                'uptime_s': round(time.monotonic() - self.started_at, 1),
                'agent': self.worker.agent_id, 'connected': self.runtime.instance is not None,
                'sensor_error': self.sensor_error, 'voice_error': self.voice_error,
                'voice_enabled': self.voice_enabled, 'speaking': self.speaking.is_set(),
                'last_action': self.last_action, 'pending_events': self.queue.count(),
                'oldest_event_error': pending[0]['last_error'] if pending else None}

    def handle(self, req):
        if not isinstance(req, dict):
            raise ValueError('Request must be an object')
        cmd = req.get('cmd')
        if cmd == 'ping':
            return self.status()
        if cmd == 'action':
            return self.action(req.get('name'), req.get('speed', 60), req.get('count', 1))
        if cmd == 'light':
            return self.light(req.get('mode'), req.get('color', (255, 255, 255)),
                              req.get('bps', 1), req.get('brightness', 0.8))
        if cmd == 'say':
            return self.say(req.get('text'))
        if cmd == 'event':
            return self.submit(req.get('kind'), req.get('payload'))
        if cmd == 'stop-motion':
            if self.stop_event.is_set():
                raise RuntimeError('Controller is stopping')
            self.runtime.connect().body_stop()
            return {'motion_stopped': True}
        if cmd == 'shutdown':
            self.stop_event.set()
            return {'stopping': True}
        raise ValueError(f'Unknown controller command: {cmd}')

    def _sensors(self, poller):
        try:
            while not self.stop_event.is_set():
                poller.poll()
                self.stop_event.wait(0.05)
        except Exception as exc:
            self.sensor_error = type(exc).__name__
            print(f'Required sensor reporting failed: {self.sensor_error}', file=sys.stderr, flush=True)
            self.stop_event.set()

    def _voice(self):
        try:
            from voice_input import run_voice
            run_voice(lambda text: self.submit('voice', {'text': text}), self.stop_event, self.speaking)
        except Exception as exc:
            self.voice_error = type(exc).__name__
            print(f'Voice input stopped: {self.voice_error}', file=sys.stderr, flush=True)
            self.stop_event.set()

    def serve(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (STATE_DIR / 'controller.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError('A PiDog controller already owns the hardware') from None
        self.ownership_lock = lock
        try:
            self._serve_owned()
        finally:
            self.close()

    def _serve_owned(self):
        if SOCKET_PATH.exists():
            try:
                controller_request({'cmd': 'ping'}, timeout=1)
            except (ConnectionRefusedError, FileNotFoundError):
                SOCKET_PATH.unlink()
            else:
                raise RuntimeError('A PiDog controller already owns this socket')
        if shutil.which(self.worker.openclaw_cli) is None:
            raise RuntimeError('OpenClaw CLI is unavailable; install it for the controller user')
        controller = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(5)
                try:
                    raw = self.rfile.readline(32769)
                    if len(raw) > 32768 or not raw.endswith(b'\n'):
                        raise ValueError('Request must be a JSON line smaller than 32 KiB')
                    result = controller.handle(json.loads(raw))
                    response = {'ok': True, 'data': result}
                except Exception as exc:
                    response = {'ok': False, 'error': str(exc)}
                self.wfile.write((json.dumps(response) + '\n').encode())

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True
            block_on_close = False

        try:
            # Claim socket before hardware, preventing a second controller owner.
            self.server = Server(str(SOCKET_PATH), Handler)
            os.chmod(SOCKET_PATH, 0o600)
            dog = self.runtime.connect()
            poller = SensorEventPoller(dog, self.queue)
            self._lifecycle_posture(dog, self.startup_posture)
            self.sensor_thread = threading.Thread(target=self._sensors, args=(poller,), daemon=True)
            self.sensor_thread.start()
            self.worker.start()
            if self.voice_enabled:
                self.voice_thread = threading.Thread(target=self._voice, daemon=True)
                self.voice_thread.start()
            self.server.timeout = 0.25
            while not self.stop_event.is_set():
                self.server.handle_request()
        finally:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.stop_event.set()
        try:
            self.worker.stop(timeout=3)
            if self.sensor_thread is not None:
                self.sensor_thread.join(timeout=1)
            if self.server is not None:
                self.server.server_close()
                SOCKET_PATH.unlink(missing_ok=True)
            # Active commands observe stop_event and exit before devices are closed.
            with self.command_lock:
                try:
                    if self.runtime.instance is not None:
                        self._lifecycle_posture(self.runtime.instance, self.shutdown_posture,
                                                stopping_allowed=True)
                finally:
                    self.runtime.close()
        finally:
            if self.ownership_lock is not None:
                self.ownership_lock.close()
                self.ownership_lock = None


def controller_request(payload, timeout=40):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(SOCKET_PATH))
        sock.sendall((json.dumps(payload) + '\n').encode())
        with sock.makefile('rb') as stream:
            raw = stream.readline(65537)
    if not raw or len(raw) > 65536:
        raise RuntimeError('Invalid controller response')
    response = json.loads(raw)
    if not response.get('ok'):
        raise RuntimeError(response.get('error', 'Controller request failed'))
    return response['data']


def start_controller(args):
    try:
        return controller_request({'cmd': 'ping'}, timeout=1)
    except (FileNotFoundError, ConnectionRefusedError):
        pass
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    argv = [sys.executable, str(Path(__file__).resolve()), 'serve',
            '--agent', args.agent, '--openclaw-cli', args.openclaw_cli,
            '--startup-posture', args.startup_posture, '--shutdown-posture', args.shutdown_posture]
    if args.voice:
        argv.append('--voice')
    with LOG_FILE.open('ab') as log:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Controller failed to start; inspect {LOG_FILE}')
        try:
            result = controller_request({'cmd': 'ping'}, timeout=1)
            if result['connected']:
                return result
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout):
            pass
        time.sleep(0.2)
    process.terminate()
    raise RuntimeError(f'Controller startup timed out; inspect {LOG_FILE}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    for name in ('start', 'serve'):
        p = sub.add_parser(name)
        p.add_argument('--agent', default='main')
        p.add_argument('--openclaw-cli', default='openclaw')
        p.add_argument('--voice', action='store_true', help='Vosk with silent Hey Buddy wake gate')
        p.add_argument('--startup-posture', choices=['sit', 'lie', 'stand', 'none'], default='sit')
        p.add_argument('--shutdown-posture', choices=['sit', 'lie', 'stand', 'none'], default='lie')
    sub.add_parser('status')
    sub.add_parser('stop')
    sub.add_parser('stop-motion')
    for name in ('action', 'send-action'):
        p = sub.add_parser(name)
        p.add_argument('name', choices=sorted(ACTION_MAP.keys() | GESTURE_MAP.keys()))
        p.add_argument('--speed', type=int, default=60)
        p.add_argument('--count', type=int, default=1)
        p.add_argument('--hold', action='store_true', help='Compatibility flag; posture is always retained')
    for name in ('light', 'send-light'):
        p = sub.add_parser(name)
        p.add_argument('mode', choices=sorted(LIGHT_MODES))
        p.add_argument('--color', type=parse_color, default=COLOR_MAP['white'])
        p.add_argument('--bps', type=float, default=1)
        p.add_argument('--brightness', type=float, default=0.8)
    p = sub.add_parser('say', help='Speak text using Piper only')
    p.add_argument('text')
    p = sub.add_parser('message', help='Send keyboard text to the OpenClaw agent')
    p.add_argument('text')
    args = parser.parse_args(argv)
    try:
        if args.cmd == 'serve':
            controller = PiDogController(agent_id=args.agent, openclaw_cli=args.openclaw_cli,
                                         voice=args.voice, startup_posture=args.startup_posture,
                                         shutdown_posture=args.shutdown_posture)
            signal.signal(signal.SIGTERM, lambda *_: controller.stop_event.set())
            signal.signal(signal.SIGINT, lambda *_: controller.stop_event.set())
            controller.serve()
            if controller.sensor_error or controller.voice_error:
                raise RuntimeError('Required input reporting failed; inspect the controller log')
            return
        if args.cmd == 'start':
            result = start_controller(args)
        else:
            request = {'cmd': args.cmd}
            if args.cmd == 'status':
                request = {'cmd': 'ping'}
            elif args.cmd == 'stop':
                request = {'cmd': 'shutdown'}
            elif args.cmd in {'action', 'send-action'}:
                request = {'cmd': 'action', 'name': args.name, 'speed': args.speed, 'count': args.count}
            elif args.cmd in {'light', 'send-light'}:
                request = {'cmd': 'light', 'mode': args.mode, 'color': args.color,
                           'bps': args.bps, 'brightness': args.brightness}
            elif args.cmd == 'say':
                request = {'cmd': 'say', 'text': args.text}
            elif args.cmd == 'message':
                request = {'cmd': 'event', 'kind': 'keyboard', 'payload': {'text': args.text}}
            result = controller_request(request)
        print(json.dumps(result, indent=2))
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == '__main__':
    main()
