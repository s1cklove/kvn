import urllib.error
import urllib.request


def http_get(url, proxy=None, timeout=10, headers=None):
    """Returns (status, headers, body). proxy=None means direct, ignoring *_proxy env."""
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def probe(urls, proxy=None, timeout=8):
    """True if any URL gives an HTTP answer that isn't a proxy-side failure."""
    for url in urls:
        try:
            status, _, _ = http_get(url, proxy=proxy, timeout=timeout,
                                    headers={"User-Agent": "Mozilla/5.0"})
            if status < 500:
                return True
        except (OSError, ValueError):
            pass
    return False
