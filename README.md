# kvn

Keeps a local [sing-box](https://sing-box.sagernet.org/) proxy on a
[BlancVPN](https://blanc.link) subscription alive, so you don't have to babysit
a VPN client.

- Downloads the subscription, turns every server into a sing-box outbound and
  puts them into a single `urltest` group: traffic always goes through the
  fastest server that answers.
- Probes the tunnel every 30 s. When it dies, re-downloads the subscription
  (Blanc rotates SNI / short ids), re-tests every server and lets sing-box
  switch over. Tells apart "VPN is down" from "no internet at all".
- Supervises the sing-box process and restarts it if it crashes.
- Sends desktop notifications (`notify-send`): tunnel down / back up, server
  switched, subscription changed, subscription about to expire.
- Optionally routes Russian sites directly (domain zones + geoip/geosite
  rule-sets), everything else through the VPN.

It exposes a plain HTTP/SOCKS5 proxy on `127.0.0.1:10808`; point your browser /
`HTTPS_PROXY` / system proxy settings at it. It is **not** a TUN/system-wide VPN.

> Unofficial. Not affiliated with BlancVPN. Should work with any
> v2rayN-style subscription that hands out `vless://` links.

## Requirements

- Linux with systemd (user services). Other platforms are not tested.
- Python ≥ 3.9 — standard library only, no pip dependencies.
- [sing-box](https://sing-box.sagernet.org/installation/package-manager/) ≥ 1.11
  (uses the rule actions syntax; tested with 1.14).
- `notify-send` (libnotify) and a notification daemon — optional; without it
  notifications only go to the journal.
- A BlancVPN subscription URL.
- Free local ports: `10808` (proxy) and `9095` (sing-box Clash API, localhost
  only, protected by a random secret). Both can be changed.

Supported links: `vless://` with `security` = `none` / `tls` / `reality` and
transport `tcp` / `ws` / `grpc` / `httpupgrade` / `http`. Anything else is
skipped and reported by `kvn check`.

## Installation

### 1. Put the subscription URL in place

```sh
mkdir -p ~/.config/kvn
install -m 600 /dev/null ~/.config/kvn/sub_url
$EDITOR ~/.config/kvn/sub_url   # paste the URL, one line
```

The URL is a credential: keep the file `600` and out of git / the Nix store.
The service does not start while this file is missing.

### 2a. Any distro (pipx / pip)

```sh
pipx install git+https://github.com/<you>/kvn
# or, from a checkout: pipx install .

kvn check          # fetches the subscription and validates the config

mkdir -p ~/.config/systemd/user
cp contrib/systemd/kvn.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now kvn
```

The unit expects the binary at `~/.local/bin/kvn` (pipx default) —
edit `ExecStart` otherwise. If `sing-box` is not on the service's `PATH`, set
`KVN_SING_BOX` in the unit (see the comments there).

To keep it running while you're logged out: `loginctl enable-linger $USER`.

### 2b. NixOS / Home Manager (flake)

```nix
# flake.nix
inputs.kvn = {
  url = "github:<you>/kvn";
  inputs.nixpkgs.follows = "nixpkgs";
};

# home-manager configuration
{ inputs, ... }: {
  imports = [ inputs.kvn.homeManagerModules.default ];
  services.kvn.enable = true;
  # services.kvn.settings = { port = 1080; };
}
```

The package pins `sing-box` and `notify-send` itself. Without flakes:
`nix build .#default` gives you the binary.

### 3. Use the proxy

```sh
export HTTPS_PROXY=http://127.0.0.1:10808 HTTP_PROXY=http://127.0.0.1:10808
curl https://1.1.1.1/cdn-cgi/trace
```

## Usage

```
kvn run              # the daemon (what the systemd unit runs)
kvn check            # fetch + parse the subscription, validate the sing-box config
kvn status [-t]      # current server and per-server delays (-t: re-test first)
kvn update           # ask the running daemon to refresh and re-test now
journalctl --user -u kvn -f
```

`check` and `status` never print the subscription URL or server credentials.

## Configuration

Optional `~/.config/kvn/config.json`, any subset of:

| key | default | meaning |
|---|---|---|
| `listen`, `port` | `127.0.0.1`, `10808` | where the mixed HTTP/SOCKS proxy listens |
| `clash_api` | `127.0.0.1:9095` | sing-box Clash API used for health checks |
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
| `urltest_interval`, `tolerance` | `3m`, `150` | sing-box urltest tuning |
| `bypass_russia` | `true` | route RU domains / IPs directly |
| `rulesets` | hiddify / MetaCubeX / runetfreedom | rule-sets for the bypass, name → `.srs` URL |
| `notify_switch` | `true` | notify when urltest changes the server |
| `expire_warn_days` | `3` | warn this many days before the subscription expires |

Environment: `KVN_SING_BOX`, `KVN_NOTIFY_SEND` (paths to the tools),
`XDG_CONFIG_HOME`, `XDG_STATE_HOME`.

State (server cache, generated sing-box config, rule-sets, API secret) lives in
`~/.local/state/kvn/`; deleting it is safe.

## How recovery works

1. Probe through the proxy fails `fail_threshold` times in a row.
2. If the direct probes fail too, it's your network: wait quietly.
3. Otherwise notify, re-download the subscription — direct first, because Blanc
   refuses it from its own exits — and restart sing-box if the servers changed.
4. Ask urltest to re-test every server; it switches to the fastest alive one.
5. Repeat every `recover_backoff` seconds until the probe passes again.

## Development

```sh
python -m unittest discover -s tests   # sing-box on PATH enables the config check test
python -m kvn check
```

## License

MIT
