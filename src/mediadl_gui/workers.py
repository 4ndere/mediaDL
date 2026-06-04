from __future__ import annotations

import re
import subprocess

from PySide6.QtCore import QThread, Signal


class YtDlpWorker(QThread):
    line_received = Signal(str)
    progress_changed = Signal(int)
    finished_with_code = Signal(int)

    def __init__(self, args: list[str]):
        super().__init__()
        self._args = args
        self._proc: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def run(self) -> None:
        percent_re = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
        try:
            self._proc = subprocess.Popen(
                self._args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                line = line.rstrip("\n")
                self.line_received.emit(line)
                m = percent_re.search(line)
                if m:
                    try:
                        pct = float(m.group(1))
                        self.progress_changed.emit(max(0, min(100, int(pct))))
                    except Exception:
                        pass
            code = self._proc.wait()
            self.finished_with_code.emit(code)
        except Exception as e:
            self.line_received.emit(str(e))
            self.finished_with_code.emit(1)


class FfmpegWorker(QThread):
    line_received = Signal(str)
    finished_with_code = Signal(int)

    def __init__(self, args: list[str]):
        super().__init__()
        self._args = args
        self._proc: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self._args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                self.line_received.emit(line.rstrip("\n"))
            code = self._proc.wait()
            self.finished_with_code.emit(code)
        except Exception as e:
            self.line_received.emit(str(e))
            self.finished_with_code.emit(1)

