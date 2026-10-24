"""Send approved local events, or run a command and report its exit status.

python -m desktop_chibi.emit --source code --event code.done
python -m desktop_chibi.emit --source code --run python my_script.py
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtNetwork import QLocalSocket
if __package__:
    from .events import socket_name
else:
    # The beginner guide copies an absolute launcher command usable from any cwd.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from desktop_chibi.events import socket_name


def send_event(source, event, subject='', active=True, token=None, server_name=None):
    app = QCoreApplication.instance() or QCoreApplication([])
    if token is None:
        store = QSettings('DesktopChibi', 'DesktopChibi')
        try:
            config = json.loads(store.value('events_v1', '{}'))
            permission = next((s for s in config.get('sources', []) if s.get('id') == source), None)
        except (TypeError, ValueError, AttributeError):
            permission = None
        if not permission or permission.get('kind') != 'local' or not permission.get('enabled'):
            raise ValueError('Источник выключен. Разрешите его в «События и разрешения».')
        if event not in permission.get('events', []):
            raise ValueError('Этот тип события не разрешён источнику.')
        token = permission['token']
    socket = QLocalSocket()
    socket.connectToServer(server_name or socket_name())
    if not socket.waitForConnected(1500):
        raise ValueError('Приложение не запущено или локальный канал выключен.')
    packet = json.dumps({'source': source, 'event': event, 'subject': subject,
                         'active': active, 'token': token}, ensure_ascii=False).encode() + b'\n'
    if len(packet) > 8192:
        socket.abort()
        raise ValueError('Слишком длинное событие.')
    socket.write(packet)
    socket.waitForBytesWritten(1500)
    response = bytearray()
    while b'\n' not in response and len(response) <= 8192:
        if not socket.bytesAvailable() and not socket.waitForReadyRead(2000):
            break
        response.extend(bytes(socket.readAll()))
    socket.disconnectFromServer()
    try:
        result = json.loads(bytes(response).split(b'\n', 1)[0])
        if not result.get('ok'):
            raise ValueError('Источник или тип события не разрешён; проверьте ключ.')
        return result
    except (TypeError, json.JSONDecodeError, UnicodeError):
        raise ValueError('Приложение не подтвердило событие.') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description='Локальные события Desktop Chibi; без передачи текста сообщений и кода.')
    parser.add_argument('--source', default='code', help='ID разрешённого локального источника')
    parser.add_argument('--event', help='Тип события, например code.done')
    parser.add_argument('--subject', default='', help='ID задачи, приложения или участника (не текст сообщения)')
    parser.add_argument('--stop', action='store_true', help='Передать active=false')
    parser.add_argument('--token-env', action='store_true', help='Взять ключ из CHIBI_EVENT_TOKEN вместо локальных настроек')
    parser.add_argument('--run', nargs=argparse.REMAINDER, help='Запустить команду и послать code.done/code.failed. Команда передаётся без shell.')
    args = parser.parse_args(argv)
    if not args.event and args.run is None:
        parser.error('Укажите --event или --run с командой.')
    exit_code = None
    if args.run is not None:
        command = args.run[1:] if args.run and args.run[0] == '--' else args.run
        if not command:
            parser.error('После --run нужна команда.')
        try:
            exit_code = subprocess.run(command, check=False).returncode
        except OSError as error:
            print(f'Не удалось запустить команду: {error}', file=sys.stderr)
            exit_code = 127
        except KeyboardInterrupt:
            exit_code = 130
    event = args.event or ('code.done' if exit_code == 0 else 'code.failed')
    token = os.environ.get('CHIBI_EVENT_TOKEN') if args.token_env else None
    try:
        if args.token_env and not token:
            raise ValueError('Переменная CHIBI_EVENT_TOKEN не задана.')
        result = send_event(args.source, event, args.subject, not args.stop, token)
        if exit_code is None:
            print(f'Событие принято; анимаций запущено: {result["matched"]}')
    except ValueError as error:
        print(f'Chibi: {error}', file=sys.stderr)
        if exit_code is None:
            return 1
    return (128 - exit_code if exit_code is not None and exit_code < 0 else exit_code) if exit_code is not None else 0


if __name__ == '__main__':
    raise SystemExit(main())
