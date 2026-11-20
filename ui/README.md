# KVN Connect Desktop UI

Isolated native desktop window (`pywebview` + optional `pystray` tray icon) for `kvn`.

Core `kvn/` has **zero dependency** on this folder — CLI and NixOS users do not need to install any UI packages.

## Features

- Native OS WebView (Wayland WebKitGTK on Linux, Edge WebView2 on Windows, WKWebView on macOS)
- System tray icon (`icon.png`) with quick reconnect and show/hide
- Full control: Start / Stop / Reconnect, live Server picker (`use` / `auto`), Master & Chain toggles, Subscription & Chain URL configuration, Autostart install/uninstall, Live logs
- Strict local-proxy model (no system-wide proxy or TUN), with 1-click copyable proxy addresses

## Run locally

```sh
pip install -e ".[ui]"
python ui/app.py
```

Pass `--no-tray` to run without a system tray icon, or `--debug` for WebView devtools.
