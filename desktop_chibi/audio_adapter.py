"""PipeWire playback-state metadata; no capture nodes or audio samples are opened."""
import codecs
import json
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer


def audio_names(value):
    name = Path(value).name.casefold()
    if name in ('firefox', 'firefox-bin', 'mozilla firefox', 'org.mozilla.firefox'):
        return {'firefox', 'firefox-bin', 'mozilla firefox', 'org.mozilla.firefox'}
    return {name}


class AudioAdapter(QObject):
    def __init__(self, hub, source):
        super().__init__(hub)
        self.hub, self.source = hub, source
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read)
        self.process.readyReadStandardError.connect(lambda: self.process.readAllStandardError())
        self.process.finished.connect(self.failed)
        self.process.errorOccurred.connect(self.failed)
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.buffer = ''
        self.nodes = {}
        self.active = set()
        self.stopping = False
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(2000)
        self.heartbeat.timeout.connect(self.refresh)

    def start(self):
        if not sys.platform.startswith('linux') or not shutil.which('pw-dump'):
            self.hub.set_status(self.source['id'], 'Нужны Linux, PipeWire и pw-dump. Звук не записывается.')
            return
        if not self.source.get('apps'):
            self.hub.set_status(self.source['id'], 'Выберите приложение для отслеживания воспроизведения.')
            return
        self.process.started.connect(lambda: self.hub.set_status(self.source['id'], 'Наблюдает состояние воспроизведения выбранных приложений'))
        self.process.start('pw-dump', ['-N', '-m'])
        self.heartbeat.start()

    def read(self):
        self.feed(self.decoder.decode(bytes(self.process.readAllStandardOutput())))

    def feed(self, chunk):
        self.buffer += chunk
        if len(self.buffer) > 4 * 1024 * 1024:
            self.stop()
            self.hub.set_status(self.source['id'], 'Слишком большой ответ PipeWire; наблюдение остановлено.')
            return
        decoder = json.JSONDecoder()
        while self.buffer.strip():
            self.buffer = self.buffer.lstrip()
            try:
                batch, end = decoder.raw_decode(self.buffer)
            except ValueError:
                return  # Wait for the rest of this JSON array.
            self.buffer = self.buffer[end:]
            if isinstance(batch, list):
                self.apply_batch(batch)

    def apply_batch(self, batch):
        for item in batch:
            if not isinstance(item, dict) or not isinstance(item.get('id'), int):
                continue
            identity, info = item['id'], item.get('info')
            if info is None:
                self.nodes.pop(identity, None)
                continue
            if not isinstance(info, dict):
                continue
            old = self.nodes.get(identity)
            if old is None and item.get('type') != 'PipeWire:Interface:Node':
                continue
            props = info.get('props') or {}
            if not isinstance(props, dict):
                continue
            # Retain only the few properties necessary for allowed applications.
            selected = {key: props[key] for key in ('application.process.binary', 'application.name', 'application.id', 'media.class') if key in props}
            previous = old or {'props': {}, 'state': '', 'muted': False, 'volume': 1.0}
            merged = {**previous, 'props': {**previous['props'], **selected}}
            names = {Path(str(merged['props'].get(key, ''))).name.casefold()
                     for key in ('application.process.binary', 'application.name', 'application.id')}
            if not any(names & audio_names(app) for app in self.source.get('apps', [])):
                self.nodes.pop(identity, None)
                continue
            if merged['props'].get('media.class') != 'Stream/Output/Audio':
                self.nodes.pop(identity, None)
                continue
            if 'state' in info:
                merged['state'] = info['state']
            params = info.get('params') or {}
            for volume in params.get('Props', []) if isinstance(params, dict) else []:
                if not isinstance(volume, dict):
                    continue
                if 'mute' in volume:
                    merged['muted'] = volume['mute'] is True
                values = volume.get('channelVolumes')
                if isinstance(values, list) and values and all(isinstance(v, (int, float)) for v in values):
                    merged['volume'] = max(values)
                elif isinstance(volume.get('volume'), (float, int)):
                    merged['volume'] = volume['volume']
            if old is not None or len(self.nodes) < 256:
                self.nodes[identity] = merged
        self.publish()

    def playing(self):
        result = set()
        for node in self.nodes.values():
            if node['state'] != 'running' or node['muted'] or node['volume'] <= 0:
                continue
            names = {Path(str(node['props'].get(key, ''))).name.casefold()
                     for key in ('application.process.binary', 'application.name', 'application.id')}
            for app in self.source.get('apps', []):
                if names & audio_names(app):
                    result.add(app)
        return result

    def publish(self):
        playing = self.playing()
        for app in sorted(playing - self.active):
            self.hub.dispatch(self.source['id'], 'audio.playing', app, True)
        for app in sorted(self.active - playing):
            self.hub.dispatch(self.source['id'], 'audio.playing', app, False)
        self.active = playing
        if playing:
            self.hub.set_status(self.source['id'], 'Воспроизводит: ' + ', '.join(sorted(playing)))
        else:
            self.hub.set_status(self.source['id'], 'Выбранные приложения сейчас не воспроизводят звук')

    def refresh(self):
        for app in sorted(self.active):
            self.hub.dispatch(self.source['id'], 'audio.playing', app, True, record=False, renew_only=True)

    def failed(self, *_):
        if not self.stopping:
            self.heartbeat.stop()
            self.nodes.clear()
            self.publish()
            self.hub.set_status(self.source['id'], 'PipeWire недоступен или наблюдение остановлено. Проверьте звуковой сеанс.')

    def stop(self):
        self.stopping = True
        self.heartbeat.stop()
        self.process.kill()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.waitForFinished(1000)
        self.nodes.clear()
        self.publish()
        self.buffer = ''
