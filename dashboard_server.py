"""
Live dashboard server: the static dashboard (main.py's dashboard.html) is a
snapshot of one run; this serves the same page wired up to re-run the real
model whenever an input changes, instead of a fixed, pre-baked JSON blob.

Local, single-user tool: binds to 127.0.0.1 only, never 0.0.0.0. There's no
auth, because there's nothing to protect against beyond your own machine -
don't put this behind a public port.

GET  /          - runs pipeline.run() once with this process's startup
                   defaults (--months/--bank-cert) and serves the live page.
POST /api/run    - body is a JSON object of pipeline.run() overrides
                   (months, bank_cert, dividend_payout_ratio, custom_shock_bps,
                   position_overrides), re-runs the model, and returns
                   reporting.dashboard.build_data(results) as JSON. A bad
                   override (unknown position/field) or a failed calibration
                   comes back as {"error": ...} with HTTP 400, never a stack
                   trace to the browser.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import pipeline
from reporting import dashboard

# Only used to render the very first GET /; every POST /api/run payload is
# self-contained (the live page always sends explicit values, using null for
# "cleared", e.g. bank_cert: null to go back to the synthetic book) - falling
# back to these startup defaults for a POST would make "clear the bank cert"
# impossible once the server started with --bank-cert set.
_STARTUP_DEFAULTS = {"months": 24, "bank_cert": None}


def _run_from_payload(payload):
    return pipeline.run(
        bank_cert=payload.get("bank_cert"),
        months=payload.get("months") or config.HORIZON_MONTHS,
        dividend_payout_ratio=payload.get("dividend_payout_ratio"),
        custom_shock_bps=payload.get("custom_shock_bps"),
        position_overrides=payload.get("position_overrides"),
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the console quiet; errors still surface in the JSON response

    def do_GET(self):
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        results = pipeline.run(bank_cert=_STARTUP_DEFAULTS["bank_cert"], months=_STARTUP_DEFAULTS["months"])
        html = dashboard.render_live_page(dashboard.build_data(results))
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/run":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            results = _run_from_payload(payload)
            body = json.dumps(dashboard.build_data(results)).encode("utf-8")
            status = 200
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode("utf-8")
            status = 400
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(host="127.0.0.1", port=8765, months=24, bank_cert=None):
    _STARTUP_DEFAULTS["months"] = months
    _STARTUP_DEFAULTS["bank_cert"] = bank_cert
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[dashboard_server] Live dashboard running at http://{host}:{server.server_port} "
          f"(Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Live NIM dashboard server (local only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--months", type=int, default=24, help="Initial forecast horizon")
    parser.add_argument("--bank-cert", type=int, default=None, help="Initial FDIC certificate to calibrate to")
    args = parser.parse_args()
    serve(port=args.port, months=args.months, bank_cert=args.bank_cert)


if __name__ == "__main__":
    main()
