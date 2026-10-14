"""Opt-in, reversible Windows tuning with a write-ahead recovery journal."""
import atexit
import ctypes
import json
import os
import re
import threading
from pathlib import Path
from .process import capture
from .settings import data_dir


def is_admin():
    return os.name == 'nt' and bool(ctypes.windll.shell32.IsUserAnAdmin())


def elevate():
    import sys
    import subprocess
    if os.name != 'nt' or is_admin():
        return False
    args = sys.argv[1:] if getattr(sys, 'frozen', False) else [str(Path(sys.argv[0]).resolve()), *sys.argv[1:]]
    result = ctypes.windll.shell32.ShellExecuteW(None, 'runas', sys.executable,
                                                subprocess.list2cmdline(args), None, 1)
    return result > 32


def cpu_temperature():
    import psutil
    try:
        sensors = psutil.sensors_temperatures()
        values = [item.current for group in sensors.values() for item in group if item.current > 0]
        if values:
            return max(values)
    except (AttributeError, OSError):
        pass
    if os.name == 'nt':
        try:
            import pythoncom
            import wmi
            pythoncom.CoInitialize()
            try:
                values = wmi.WMI(namespace=r'root\wmi').MSAcpi_ThermalZoneTemperature()
                return max(v.CurrentTemperature / 10 - 273.15 for v in values) if values else None
            finally:
                pythoncom.CoUninitialize()
        except Exception:
            pass
    return None


class SystemTweaks:
    def __init__(self, report=lambda *args: None):
        self.path = data_dir() / 'restore.json'
        self.lock = threading.RLock()
        self.report = report
        self.state = {}
        self.thermal = False
        self.priority_guard = False
        self.threshold = 85
        self.was_hot = False
        self.stop_event = threading.Event()
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text(encoding='utf-8'))
                self.restore()
            except Exception as exc:
                report('WARN', f'Recovery needs administrator access: {exc}')
        atexit.register(self.restore)
        threading.Thread(target=self._guard, daemon=True).start()

    def journal(self, key, value):
        self.state[key] = value
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.path)

    def apply(self, options):
        if os.name != 'nt':
            self.report('WARN', 'Windows system tweaks are unavailable on this OS')
            return
        if not is_admin():
            self.report('WARN', 'Use Settings → Restart as administrator to apply system tweaks')
            return
        with self.lock:
            if self.state:
                self.restore()
            if self.state:
                raise RuntimeError('Restore pending changes before applying new tweaks')
            self.priority_guard = options.get('priority', False)
            self.thermal = options.get('thermal', True)
            if options.get('power') or options.get('parking'):
                old = re.search(r'[0-9a-fA-F-]{36}', capture(['powercfg', '/getactivescheme'])).group()
                clone = capture(['powercfg', '/duplicatescheme', old])
                new = re.search(r'[0-9a-fA-F-]{36}', clone).group()
                self.journal('power', [old, new])
                if options.get('power'):
                    for setting in ('893dee8e-2bef-41e0-89c6-b55d0929964c', 'bc5038f7-23e0-4960-96da-33abaf5935ec'):
                        capture(['powercfg', '/setacvalueindex', new, '54533251-82be-4824-96c1-47b60b740d00', setting, '100'])
                if options.get('parking'):
                    capture(['powercfg', '/setacvalueindex', new, '54533251-82be-4824-96c1-47b60b740d00',
                             '0cc5b647-c1df-4637-891a-dec35c318583', '100'])
                capture(['powercfg', '/setactive', new])
            if options.get('timer'):
                ctypes.windll.winmm.timeBeginPeriod(1)
                self.journal('timer', True)
            if options.get('visual'):
                import winreg
                keypath = r'Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects'
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, keypath) as key:
                    try:
                        old = winreg.QueryValueEx(key, 'VisualFXSetting')[0]
                    except FileNotFoundError:
                        old = None
                    self.journal('visual', old)
                    winreg.SetValueEx(key, 'VisualFXSetting', 0, winreg.REG_DWORD, 2)
            if options.get('services'):
                import win32serviceutil
                states = {}
                for service in ('SysMain', 'WSearch', 'MapsBroker', 'lfsvc'):
                    try:
                        running = win32serviceutil.QueryServiceStatus(service)[1] == 4
                        states[service] = running
                        self.journal('services', states)
                        if running:
                            win32serviceutil.StopService(service)
                    except Exception as exc:
                        self.report('WARN', f'{service}: {exc}')
            if options.get('apps'):
                import psutil
                states = []
                for proc in psutil.process_iter(['pid', 'name', 'create_time']):
                    if (proc.info['name'] or '').lower() in ('chrome.exe', 'firefox.exe', 'spotify.exe', 'steam.exe', 'discord.exe'):
                        try:
                            states.append([proc.pid, proc.create_time()])
                            self.journal('apps', states)
                            proc.suspend()  # Reversible; never destroy unsaved user work.
                        except psutil.Error:
                            pass
            if options.get('boris'):
                backups = {}
                for base in (Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'BorisFX',
                             Path(os.environ.get('APPDATA', '')) / 'BorisFX',
                             Path(os.environ.get('ProgramData', 'C:/ProgramData')) / 'BorisFX'):
                    if not base.exists():
                        continue
                    for path in base.rglob('s_config.text'):
                        if path.stat().st_size > 1_000_000:
                            continue
                        text = path.read_text(encoding='utf-8')
                        backups[str(path)] = text
                        self.journal('boris', backups)
                        updated, count = re.subn(r'(?im)^(\s*host_gpu_integration\s*:\s*)0\s*$', r'\g<1>1', text)
                        if not count and 'host_gpu_integration' not in text:
                            updated += '\nhost_gpu_integration: 1\n'
                        path.write_text(updated, encoding='utf-8')
            if options.get('ramdisk'):
                if Path('R:/').exists():
                    raise RuntimeError('R: already exists; refusing to replace it')
                self.journal('ramdisk', True)
                capture(['imdisk', '-a', '-s', '2G', '-m', 'R:', '-p', '/fs:ntfs /q /y'])
            if options.get('defender') and options.get('folder'):
                folder = str(Path(options['folder']).resolve())
                # Preserve preexisting exclusions; pass paths via an environment variable, not PS interpolation.
                os.environ['EDIUS_EXCLUSION'] = folder
                existing = capture(['powershell', '-NoProfile', '-Command', '(Get-MpPreference).ExclusionPath'])
                if folder.casefold() not in {s.casefold() for s in existing.splitlines()}:
                    self.journal('defender', folder)
                    capture(['powershell', '-NoProfile', '-Command', 'Add-MpPreference -ExclusionPath $env:EDIUS_EXCLUSION'])
            self.report('INFO', 'Selected tweaks applied; originals saved for recovery')

    def restore(self):
        if os.name != 'nt':
            return
        with self.lock:
            self.priority_guard = False
            self.thermal = False
            for key, value in list(self.state.items()):
                try:
                    if key == 'power':
                        capture(['powercfg', '/setactive', value[0]])
                        capture(['powercfg', '/delete', value[1]])
                    elif key == 'timer':
                        ctypes.windll.winmm.timeEndPeriod(1)
                    elif key == 'visual':
                        import winreg
                        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects') as reg:
                            if value is None:
                                winreg.DeleteValue(reg, 'VisualFXSetting')
                            else:
                                winreg.SetValueEx(reg, 'VisualFXSetting', 0, winreg.REG_DWORD, value)
                    elif key == 'services':
                        import win32serviceutil
                        for name, running in value.items():
                            if running and win32serviceutil.QueryServiceStatus(name)[1] != 4:
                                win32serviceutil.StartService(name)
                    elif key in ('apps', 'priorities'):
                        import psutil
                        for entry in value:
                            try:
                                proc = psutil.Process(entry[0])
                                if proc.create_time() == entry[1]:
                                    proc.resume() if key == 'apps' else proc.nice(entry[2])
                            except psutil.NoSuchProcess:
                                pass
                    elif key == 'boris':
                        for name, text in value.items():
                            Path(name).write_text(text, encoding='utf-8')
                    elif key == 'ramdisk':
                        capture(['imdisk', '-D', '-m', 'R:'])
                    elif key == 'defender':
                        os.environ['EDIUS_EXCLUSION'] = value
                        capture(['powershell', '-NoProfile', '-Command', 'Remove-MpPreference -ExclusionPath $env:EDIUS_EXCLUSION'])
                    self.state.pop(key)
                except Exception as exc:
                    self.report('WARN', f'Restore {key}: {exc}')
            if not self.state:
                self.path.unlink(missing_ok=True)
            else:
                self.journal('_pending', True)

    def _guard(self):
        import psutil
        while not self.stop_event.wait(5):
            if os.name != 'nt':
                continue
            try:
                if not (self.thermal or self.priority_guard):
                    self.was_hot = False
                    continue
                hot = self.thermal and (cpu_temperature() or 0) > (self.threshold - 5 if self.was_hot else self.threshold)
                if self.stop_event.is_set() or not (self.thermal or self.priority_guard):
                    continue
                just_cooled = self.was_hot and not hot
                self.was_hot = hot
                if not self.priority_guard and not hot and not just_cooled:
                    continue
                for proc in psutil.process_iter(['pid', 'name']):
                    name = (proc.info['name'] or '').lower()
                    if name == 'ffmpeg.exe' and not (hot or just_cooled):
                        continue  # Never escalate normal FFmpeg or journal every preview process.
                    if name in ('edius.exe', 'ffmpeg.exe'):
                        with self.lock:
                            if self.stop_event.is_set() or not (self.thermal or self.priority_guard):
                                continue
                            originals = self.state.get('priorities', [])
                            if not any(entry[0] == proc.pid and entry[1] == proc.create_time() for entry in originals):
                                originals.append([proc.pid, proc.create_time(), proc.nice()])
                                self.journal('priorities', originals)
                            original = next((entry[2] for entry in originals if entry[0] == proc.pid and entry[1] == proc.create_time()), proc.nice())
                            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if hot else
                                  psutil.HIGH_PRIORITY_CLASS if self.priority_guard and (proc.info['name'] or '').lower() == 'edius.exe' else original)
            except (psutil.Error, OSError):
                pass
