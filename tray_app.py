#!/usr/bin/env python3
"""GenericAgent System Tray — macOS / Windows 状态栏图标，一键打开 Web UI。

Usage: python tray_app.py [--port 18600]
"""

import os, sys, time, threading, webbrowser, subprocess, platform

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 18600

if '--port' in sys.argv:
    try:
        idx = sys.argv.index('--port')
        PORT = int(sys.argv[idx + 1])
    except (ValueError, IndexError):
        pass

WEB_URL = f'http://localhost:{PORT}'
IS_MAC = platform.system() == 'Darwin'
IS_WIN = platform.system() == 'Windows'


def start_web_server():
    server_script = os.path.join(PROJECT_DIR, 'frontends', 'web_server.py')
    if not os.path.isfile(server_script):
        print(f'[Tray] Server script not found: {server_script}')
        return None
    print(f'[Tray] Starting web server on port {PORT}...')
    kwargs = {}
    if IS_WIN:
        kwargs['creationflags'] = 0x08000000
    try:
        p = subprocess.Popen(
            [sys.executable, server_script, '--port', str(PORT), '--no-browser'],
            cwd=os.path.join(PROJECT_DIR, 'frontends'),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **kwargs
        )
        return p
    except Exception as e:
        print(f'[Tray] Failed to start server: {e}')
        return None


def is_server_running():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(('127.0.0.1', PORT))
        s.close()
        return True
    except Exception:
        return False


def ensure_server():
    if is_server_running():
        print('[Tray] Server already running')
        return True
    p = start_web_server()
    if p:
        for _ in range(30):
            time.sleep(1)
            if is_server_running():
                print('[Tray] Server ready')
                return True
        print('[Tray] Server failed to start')
    return False


# ── macOS: rumps ──
if IS_MAC:
    try:
        import rumps
    except ImportError:
        print("Installing rumps...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'rumps'])
        import rumps

    class TrayApp(rumps.App):
        def __init__(self):
            super().__init__(name='GA', title='🧠', quit_button=None)
            self.idle_timer = rumps.Timer(self._idle_check, 300)
            self.idle_timer.start()
            self._server_proc = None
            self._build_menu()

        def _build_menu(self):
            self.menu.clear()
            self.menu.add(rumps.MenuItem('打开 Web UI', callback=lambda _: webbrowser.open(WEB_URL)))
            self.menu.add(rumps.separator)
            if is_server_running():
                self.menu.add(rumps.MenuItem('🔄 重启服务', callback=self._restart_server))
                self.menu.add(rumps.MenuItem('⏹ 停止服务', callback=self._stop_server))
            else:
                self.menu.add(rumps.MenuItem('▶ 启动服务', callback=self._start_server))
            self.menu.add(rumps.separator)
            running = self.idle_timer.is_alive()
            self.idle_item = rumps.MenuItem(f'{"✅" if running else "⏸"} 空闲任务: {"开" if running else "关"}', callback=self._toggle_idle)
            self.menu.add(self.idle_item)
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem('退出', callback=self._quit))

        def _restart_server(self, _):
            try:
                import requests
                requests.post(f'{WEB_URL}/api/update/restart', timeout=5)
            except Exception:
                pass
            # Wait for new server to come up, then rebuild menu
            def _wait():
                time.sleep(2)
                for _ in range(10):
                    if is_server_running():
                        break
                    time.sleep(1)
                self._build_menu()
            threading.Thread(target=_wait, daemon=True).start()

        def _stop_server(self, _):
            try:
                import requests
                requests.post(f'{WEB_URL}/api/update/stop', timeout=5)
            except Exception:
                pass
            time.sleep(0.5)
            self._build_menu()

        def _start_server(self, _):
            ensure_server()
            self._build_menu()

        def _toggle_idle(self, sender):
            if self.idle_timer.is_alive():
                self.idle_timer.stop()
                sender.title = '⏸ 空闲任务: 关'
            else:
                self.idle_timer.start()
                sender.title = '✅ 空闲任务: 开'

        def _idle_check(self, _):
            try:
                import requests
                resp = requests.get(f'{WEB_URL}/api/idle/status', timeout=5)
                data = resp.json()
                if not data.get('running'):
                    requests.post(f'{WEB_URL}/api/idle/run_checklist', timeout=10)
                # Also trigger auto moments
                requests.post(f'{WEB_URL}/api/moments/auto', timeout=10)
            except Exception:
                pass

        def _quit(self, _):
            if self.idle_timer.is_alive():
                self.idle_timer.stop()
            rumps.quit_application()

        def run(self):
            print(f'[Tray] macOS tray started (port {PORT})')
            super().run()

# ── Windows / Linux: pystray ──
else:
    try:
        from PIL import Image, ImageDraw
        import pystray
    except ImportError:
        print("Installing pystray + Pillow...")
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pystray', 'Pillow'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            from PIL import Image, ImageDraw
            import pystray
        except Exception as e:
            print(f'[Tray] Failed to install pystray: {e}')
            print('[Tray] Falling back to web_server.py — run it directly.')
            sys.exit(1)

    def _make_icon():
        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([12, 20, 52, 52], fill='#7c6ff7', outline='#5a4fcf', width=2)
        draw.ellipse([8, 12, 36, 38], fill='#9b8ff7', outline='#5a4fcf', width=2)
        draw.ellipse([28, 12, 56, 38], fill='#9b8ff7', outline='#5a4fcf', width=2)
        for y in [24, 28, 32]:
            draw.line([22, y, 42, y], fill='#ffffff', width=2)
        return img

    class TrayApp:
        def __init__(self):
            self.idle_enabled = True
            self._setup()

        def _setup(self):
            menu_items = [
                pystray.MenuItem('打开 Web UI', lambda: webbrowser.open(WEB_URL), default=True),
                pystray.Menu.SEPARATOR,
            ]
            if is_server_running():
                menu_items.append(pystray.MenuItem('🔄 重启服务', self._restart_server))
                menu_items.append(pystray.MenuItem('⏹ 停止服务', self._stop_server))
            else:
                menu_items.append(pystray.MenuItem('▶ 启动服务', self._start_server))
            menu_items.append(pystray.Menu.SEPARATOR)
            label = f'空闲任务: {"开" if self.idle_enabled else "关"}'
            menu_items.append(pystray.MenuItem(label, self._toggle_idle))
            menu_items.append(pystray.Menu.SEPARATOR)
            menu_items.append(pystray.MenuItem('退出', self._quit))
            self.tray = pystray.Icon('GA', _make_icon(), 'GenericAgent', pystray.Menu(*menu_items))

        def _rebuild_menu(self):
            self._setup()

        def _restart_server(self, icon=None, item=None):
            try:
                import requests
                requests.post(f'{WEB_URL}/api/update/restart', timeout=5)
            except Exception:
                pass
            def _wait():
                time.sleep(2)
                for _ in range(10):
                    if is_server_running():
                        break
                    time.sleep(1)
                self._rebuild_menu()
            threading.Thread(target=_wait, daemon=True).start()

        def _stop_server(self, icon=None, item=None):
            try:
                import requests
                requests.post(f'{WEB_URL}/api/update/stop', timeout=5)
            except Exception:
                pass
            time.sleep(0.5)
            self._rebuild_menu()

        def _start_server(self, icon=None, item=None):
            ensure_server()
            self._rebuild_menu()

        def _toggle_idle(self, icon=None, item=None):
            self.idle_enabled = not self.idle_enabled
            self._rebuild_menu()

        def _quit(self, icon=None, item=None):
            self.tray.stop()

        def _idle_loop(self):
            import requests
            while getattr(self, '_running', True):
                time.sleep(300)
                if not self.idle_enabled:
                    continue
                try:
                    resp = requests.get(f'{WEB_URL}/api/idle/status', timeout=5)
                    if not resp.json().get('running'):
                        requests.post(f'{WEB_URL}/api/idle/run_checklist', timeout=10)
                    requests.post(f'{WEB_URL}/api/moments/auto', timeout=10)
                except Exception:
                    pass

        def run(self):
            self._running = True
            t = threading.Thread(target=self._idle_loop, daemon=True)
            t.start()
            print(f'[Tray] Windows tray started (port {PORT})')
            self.tray.run()
            self._running = False


if __name__ == '__main__':
    print(f'🧠 GenericAgent Tray ({platform.system()}, port {PORT})')
    ensure_server()
    webbrowser.open(WEB_URL)
    TrayApp().run()
