"""kvn: keeps a sing-box proxy on a BlancVPN subscription alive.

Fetches the subscription, builds a sing-box config with an urltest group over
all servers, supervises sing-box, probes connectivity and, when the tunnel
dies, refreshes the subscription, re-tests every server and lets urltest
switch to the fastest one. Desktop notifications go through notify-send.
"""

__version__ = "0.1.0"
