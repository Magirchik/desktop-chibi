"""One GUI per user profile, with activation through a private local socket."""
import hashlib
from pathlib import Path

from PySide6.QtCore import QObject, QElapsedTimer, QLockFile, QStandardPaths, QThread, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    activated = Signal()

    def __init__(self, parent=None, *, directory=None):
        super().__init__(parent)
        directory = Path(directory or Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.GenericConfigLocation)) / 'DesktopChibi').resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = QLockFile(str(directory / 'instance.lock'))
        # A live instance can run indefinitely. Dead process locks are still
        # recovered by QLockFile using the recorded PID and process identity.
        self.lock.setStaleLockTime(0)
        identity = hashlib.sha256(str(directory).encode()).hexdigest()[:24]
        self.name = 'desktop-chibi-instance-' + identity
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self.activate)

    def start(self, timeout=3000):
        """Return True for the owner, False after activating an existing GUI.

        The lock serializes startup and stale socket recovery. A second launch
        waits briefly if the owner has not opened its activation socket yet.
        Failure never falls through to starting a second GUI.
        """
        elapsed = QElapsedTimer()
        elapsed.start()
        while True:
            if self.lock.tryLock(0):
                # Only the lock owner may remove a socket left by a crash.
                QLocalServer.removeServer(self.name)
                if self.server.listen(self.name):
                    return True
                self.lock.unlock()
                raise OSError('Не удалось открыть канал Desktop Chibi: ' + self.server.errorString())
            if self.lock.error() != QLockFile.LockError.LockFailedError:
                raise OSError('Не удалось создать блокировку Desktop Chibi. Проверьте доступ к папке настроек.')
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            connected = socket.waitForConnected(100)
            socket.abort()
            if connected:
                return False
            if elapsed.elapsed() >= timeout:
                raise OSError('Desktop Chibi уже запущен, но не отвечает. Подождите или завершите его через диспетчер задач.')
            QThread.msleep(25)

    def activate(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.close()
            socket.deleteLater()
            self.activated.emit()

    def close(self):
        self.server.close()
        self.lock.unlock()
