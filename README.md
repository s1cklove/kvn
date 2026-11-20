# kvn

Keeps a local [sing-box](https://sing-box.sagernet.org/) proxy on a
[BlancVPN](https://blanc.link) (or any `vless://` / `ss://`) subscription alive,
so you don't have to babysit a VPN client.

- Downloads the subscription, turns every server into a sing-box outbound and
  puts them into a `urltest` group: traffic always goes through the fastest
  server that answers — unless the *master* server (Amsterdam by default) is
  good enough, then it is preferred.
- Probes the tunnel every 30 s. When it dies, re-downloads the subscription
  (Blanc rotates SNI / short ids), re-tests every server and lets sing-box
  switch over. Tells apart "VPN is down" from "no internet at all".
- Supervises the sing-box process and restarts it if it crashes.
- Sends native desktop notifications (Linux `notify-send`, macOS Notification
  Center, Windows toast): tunnel down / back up, server switched, subscription
  changed, subscription about to expire.
- Optionally routes Russian sites directly (domain zones + geoip/geosite
  rule-sets), everything else through the VPN.
- Optional isolated native desktop UI ([`ui/`](ui/README.md)) for users who
  prefer a window and buttons over a terminal.

It exposes a plain local HTTP/SOCKS5 proxy on `127.0.0.1:10808` (and optionally
a chained exit port such as `127.0.0.1:10809`). It is **strictly a local
proxy** — **not** a TUN or system-wide proxy. You decide which apps use it.

> Unofficial. Not affiliated with BlancVPN. Works with any v2rayN-style
> subscription handing out `vless://` or `ss://` links.

## Requirements

- **Linux**, **macOS**, or **Windows**
- Python ≥ 3.9 — core `kvn` uses the **standard library only** (`dependencies = []`).
- [sing-box](https://sing-box.sagernet.org/installation/package-manager/) ≥ 1.11
  (uses the rule actions syntax; tested with 1.14).
- A subscription URL.
- Free localhost ports: `10808` (proxy), `9095` (sing-box Clash API), and
  `9096` (kvn Control API, authenticated with a random Bearer secret). All can
  be changed.

Supported links:
- `vless://` with `security` = `none` / `tls` / `reality` and transport
  `tcp` / `ws` / `grpc` / `httpupgrade` / `http`.
- `ss://` (SIP002 / Shadowsocks & Shadowsocks-2022, including `2022-blake3-aes-128-gcm`).

Anything else is skipped and reported by `kvn fetch`.

## Installation

### Windows

1. Install `sing-box` and `kvn` (or download a prebuilt **KVN Connect** desktop
   bundle from GitHub Releases):
   ```powershell
   winget install SagerNet.sing-box
   pipx install git+https://github.com/s1cklove/kvn
   # or: uv tool install git+https://github.com/s1cklove/kvn
   ```
2. Configure your subscription URL, register autostart, and start the daemon:
   ```powershell
   kvn init
   kvn install
   kvn start
   kvn status
   ```

Config lives in `%APPDATA%\kvn\`, state & logs in `%LOCALAPPDATA%\kvn\`.

### macOS

```sh
brew install sing-box
pipx install git+https://github.com/s1cklove/kvn   # or: uv tool install ...

kvn init        # paste your subscription URL
kvn install     # installs ~/Library/LaunchAgents/io.github.s1cklove.kvn.plist
kvn start
kvn status
```

Config lives in `~/Library/Application Support/kvn/`.

### Linux (Ubuntu / Debian / Arch / etc.)

```sh
# 1. Install sing-box from https://sing-box.sagernet.org/installation/package-manager/
# 2. Install kvn:
pipx install git+https://github.com/s1cklove/kvn

kvn init        # saves ~/.config/kvn/sub_url (chmod 600) & validates
kvn install     # installs & enables ~/.config/systemd/user/kvn.service
kvn start
kvn status
```

To keep it running while logged out: `loginctl enable-linger $USER`.

### NixOS / Home Manager (flake)

```nix
# flake.nix
inputs.kvn = {
  url = "github:s1cklove/kvn";
  inputs.nixpkgs.follows = "nixpkgs";
};

# home-manager configuration
{ inputs, ... }: {
  imports = [ inputs.kvn.homeManagerModules.default ];
  services.kvn = {
    enable = true;
    # settings = { port = 10808; chain_port = 10809; };
  };
}
```

Then save your subscription URL once (keep it `600` and out of git / the Nix store):

```sh
kvn init
```

The default Nix package (`packages.<system>.default`) is **CLI-only** with zero
GUI dependencies and pins `sing-box` + `notify-send` automatically.

## Desktop UI (optional & isolated)

`ui/` is a standalone native desktop window (**KVN Connect**, built with
`pywebview` + optional `pystray` tray icon) that talks to the local daemon over
`127.0.0.1:9096`. Core `kvn/` does not import or depend on `ui/` in any way.

- **From source / pip:**
  ```sh
  pip install "kvn[ui]"   # or from a checkout: pip install -e ".[ui]"
  python ui/app.py
  ```
- **On NixOS:**
  ```sh
  nix run github:s1cklove/kvn#kvn-ui
  ```
- **Prebuilt binaries:** see GitHub Releases (`kvn-connect-windows-amd64.zip`,
  `kvn-connect-macos-arm64.tar.gz`, `kvn-connect-linux-amd64.tar.gz`), which
  bundle `sing-box` inside.

See [ui/README.md](ui/README.md) for details.

## Connecting your apps

`kvn` deliberately never touches your OS system proxy or routing table. Point
only the apps you want at `127.0.0.1:10808` (mixed HTTP + SOCKS5):

- **Telegram Desktop:** Settings → Advanced → Connection type → Use custom proxy → **SOCKS5**, host `127.0.0.1`, port `10808`.
- **Browser (Firefox / Chrome / Chromium):** install **FoxyProxy** (or **SwitchyOmega**), add an HTTP or SOCKS5 proxy at `127.0.0.1:10808`, and optionally route only specific domains.
- **Terminal / CLI tools:**
  ```sh
  export HTTPS_PROXY=http://127.0.0.1:10808 HTTP_PROXY=http://127.0.0.1:10808
  curl https://1.1.1.1/cdn-cgi/trace
  ```
- **Dedicated chain port (when `chain_port = 10809`):** point apps that need your static exit IP at `127.0.0.1:10809` while everything else stays on `127.0.0.1:10808`.

## Usage

The service runs on its own; the commands are for when something is off.
Start with `kvn status`, it tells which command to run next.

| situation | command |
|---|---|
| first-time setup | `kvn init` — save subscription URL & validate config |
| install / remove OS autostart | `kvn install`, `kvn uninstall` |
| what's going on? | `kvn status` (or `kvn status --json`) |
| sites don't open / it's slow, but the service runs | `kvn connect` — re-test all servers and switch to the best one |
| I want a specific exit (country), not the fastest | `kvn use <name>` — pin it; `kvn use auto` hands control back |
| all servers are dead, or Blanc changed the servers | `kvn fetch` — download the subscription now and apply it |
| sing-box hangs, nothing helps | `kvn restart` |
| turn the proxy on / off | `kvn start`, `kvn stop` |
| why? | `kvn logs` |
| which server is the fastest | `kvn servers` (or `kvn servers --json`) |
| prefer the master server / stop preferring it | `kvn master on`, `kvn master off` |
| route through a second exit (static VPS IP) | `kvn chain on`, `kvn chain off` |

```
kvn init [URL]              # first-time setup: save sub_url & verify config
kvn install | uninstall     # register / remove OS autostart (systemd / launchd / Task Scheduler)
kvn status [--json]         # service, proxy, current server, chain, subscription age/expiry
kvn connect                 # re-test every server, switch to the fastest live one
kvn use <name>|auto         # pin one server by hand (substring is enough), or back to auto
kvn master [on|off]         # show the master server and why it is / isn't used; turn it on / off
kvn chain [on|off|check]    # optional second exit layer (e.g. personal VPS over the subscription)
kvn fetch [-n] [-v]         # download the subscription and apply it (-n: only validate, -v: list servers)
kvn start | stop | restart  # start / stop / restart the background daemon
kvn logs [-n 50] [--no-follow]
kvn servers [--json]        # every server with its last measured delay
kvn run                     # the daemon itself (what the service runs)
```

None of the commands print the subscription URL or server credentials.

### Picking a server by hand

By default routing goes through an `auto` group that always uses the fastest
server that answers, and the watchdog re-tests and switches on its own. To
override that — a specific country, or just to stop it hopping around:

```sh
kvn servers            # see the names and their delays
kvn use berlin         # pin it: a substring of the name is enough
kvn use auto           # back to automatic — the watchdog takes over again
```

While a server is pinned the watchdog leaves it alone: it keeps sing-box running
but won't switch servers, recover on failure, or restart to apply a routine
subscription refresh. A pin is **runtime-only** — any sing-box restart (`kvn
restart`, a crash, `kvn fetch`) resets routing to `auto`.

## Configuration

Optional `config.json` in your config directory (`~/.config/kvn/config.json` on
Linux, `~/Library/Application Support/kvn/config.json` on macOS,
`%APPDATA%\kvn\config.json` on Windows), any subset of:

| key | default | meaning |
|---|---|---|
| `listen`, `port` | `127.0.0.1`, `10808` | where the mixed HTTP/SOCKS proxy listens |
| `clash_api` | `127.0.0.1:9095` | sing-box Clash API used for health checks |
| `api_listen`, `api_port` | `127.0.0.1`, `9096` | localhost JSON Control API for CLI & UI (`0` disables) |
| `user_agent` | `v2rayN/7.10` | UA for the subscription request (decides the format the panel returns) |
| `sub_interval` | `10800` | routine subscription refresh, seconds |
| `check_interval` | `30` | tunnel probe period, seconds |
| `fail_threshold` | `2` | failed probes in a row before recovery starts |
| `startup_grace` | `150` | seconds after sing-box start during which failures are ignored |
| `recover_backoff` | `120` | min seconds between recovery attempts while down |
| `test_url` | `https://1.1.1.1/cdn-cgi/trace` | URL urltest measures servers with |
| `probe_urls` | anthropic / 1.1.1.1 / github | "is the tunnel alive" probes (any success = alive) |
| `direct_probe_urls` | `ya.ru`, `vk.com` | "is the internet alive" probes, direct |
| `exclude` | `🇷🇺\|Россия` | regex; matching server names are left out of auto-selection |
| `master` | `Амстердам` | regex; the fastest live matching server is preferred over urltest's pick while it's good enough; `""` disables |
| `master_max_ratio`, `master_max_lag` | `2`, `150` | "good enough": at most this many times slower than the leader **or** at most this many ms behind it |
| `urltest_interval`, `tolerance` | `3m`, `150` | sing-box urltest tuning |
| `bypass_russia` | `true` | route RU domains / IPs directly |
| `rulesets` | hiddify / MetaCubeX / runetfreedom | rule-sets for the bypass, name → `.srs` URL |
| `notify_switch` | `true` | notify when urltest changes the server |
| `expire_warn_days` | `3` | warn this many days before the subscription expires |
| `chain_via` | `"chain"` | how `kvn chain` reaches the exit: `"chain"` (detour via subscription), `"auto"` (`urltest` direct vs chained), or `"direct"` |
| `chain_port` | `null` | `null` toggles the main port (`10808`); a port number (e.g. `10809`) opens a dedicated chained proxy port inside the same sing-box |

Environment: `KVN_SING_BOX`, `KVN_NOTIFY_SEND` (paths to the tools),
`XDG_CONFIG_HOME`, `XDG_STATE_HOME`.

State (server cache, generated sing-box config, rule-sets, API secret, log file)
lives in `~/.local/state/kvn/` (Linux), `~/Library/Application Support/kvn/state/`
(macOS), or `%LOCALAPPDATA%\kvn\` (Windows); deleting it is safe.

## Chain exit layer (`kvn chain`)

To keep a stable personal exit IP (e.g. your own VPS) on top of the subscription
— inside the **same** `kvn` daemon and without a second service — put one or
more `ss://...` or `vless://...` links into `chain_url` in your config directory
(`chmod 600`, out of git / the Nix store). Shadowsocks-2022
(`ss://2022-blake3-aes-128-gcm:...`) is recommended for chaining over a
subscription because it avoids TLS-in-TLS overhead and cannot be hijacked by
upstream TLS sniffing:

```sh
install -m 600 /dev/null ~/.config/kvn/chain_url
$EDITOR ~/.config/kvn/chain_url   # paste your ss:// or vless:// link
kvn chain check                   # parse link and validate the full sing-box config
kvn restart                       # load the chain layer
kvn chain                         # show route and probe latency
```

- By default (`chain_via = "chain"`), traffic reaches your exit through the
  active subscription server (`detour: "select"`): the subscription handles ISP
  rotation/recovery while external sites always see your static exit IP.
- With `chain_port = null` (default), `kvn chain on` / `kvn chain off` toggles
  the chain layer on the main `127.0.0.1:10808` proxy on the fly (no restart;
  state is persisted in `chain_off`).
- With `chain_port = 10809`, `127.0.0.1:10808` stays on the subscription while
  `127.0.0.1:10809` routes through the chain — still one `sing-box` process.

## Master server

Automatic routing (`select` → `auto`) goes through a selector that points either
at the `fastest` urltest group or at the master server (a manual pin with
`kvn use <name>` overrides both). Every check (30 s) the daemon compares the
master's last measured delay with the leader's — the fastest live server — and
uses the master while it answers and is at most `master_max_ratio` times slower
**or** at most `master_max_lag` ms behind. So Amsterdam at 260 ms still wins
over a 130 ms leader (2×), and at 200 ms over a 60 ms one (140 ms behind).
Delays are re-measured every `urltest_interval`; switching between the master
and urltest's pick doesn't drop open connections. A master that matches
`exclude` is never used.

`kvn master off` survives restarts (a `master_off` file in the state dir) and
doesn't touch `config.json`, so it works with the Home Manager module too.

## How recovery works

1. Probe through the proxy fails `fail_threshold` times in a row.
2. If the direct probes fail too, it's your network: wait quietly.
3. Otherwise notify, re-download the subscription — direct first, because Blanc
   refuses it from its own exits — and restart sing-box if the servers changed.
4. Ask urltest to re-test every server; it switches to the fastest alive one.
   If the master server is alive and good enough, traffic goes there instead.
5. Repeat every `recover_backoff` seconds until the probe passes again.

## Development

```sh
python -m unittest discover -s tests -v   # sing-box on PATH enables the config check test
python -m kvn fetch --dry-run
```

## License

MIT
