#!/usr/bin/python3
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def update(config):
    parameters = {"hostname": ",".join(config["domains"])}
    if config.get("ip"):
        parameters["myip"] = config["ip"]
    credentials = f"{config['token']}:{config['secret']}".encode()
    request = urllib.request.Request(
        "https://api.domeneshop.no/v0/dyndns/update?"
        + urllib.parse.urlencode(parameters),
        headers={
            "Authorization": "Basic " + base64.b64encode(credentials).decode(),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def main():
    try:
        with open(sys.argv[1], encoding="utf-8") as config_file:
            update(json.load(config_file))
    except (OSError, ValueError, KeyError, IndexError, urllib.error.URLError):
        print("Domeneshop DDNS update failed; check configuration and API access.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
