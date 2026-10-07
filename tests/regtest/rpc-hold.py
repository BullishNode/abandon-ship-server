#!/usr/bin/env python3
"""Forward regtest RPC, holding the first sendrawtransaction before forwarding."""
import base64
import http.server
import json
import pathlib
import sys
import threading
import tomllib
import urllib.error
import urllib.request

config = tomllib.loads(pathlib.Path(sys.argv[1]).read_text())["bitcoind"]
state = pathlib.Path(sys.argv[2])
auth = base64.b64encode(f'{config["user"]}:{config["pass"]}'.encode()).decode()


def forward(body):
    request = urllib.request.Request(config["url"], data=body,
                                    headers={"Authorization": "Basic " + auth})
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


_, reply = forward(json.dumps({"id": 1, "method": "getblockchaininfo", "params": []}).encode())
assert json.loads(reply)["result"]["chain"] == "regtest"


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        request = json.loads(body)
        if request["method"] == "sendrawtransaction":
            (state / "held-raw.txt").write_text(request["params"][0])
            # The scenario kills the sidecar, then this process. Never release
            # a signature whose pre-broadcast crash is being tested.
            threading.Event().wait()
        status, response = forward(body)
        self.send_response(status)
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(response)


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
(state / "port.txt").write_text(str(server.server_port))
server.serve_forever()
