"""Serve a fresh isolated synthetic fixture for the optional showcase capture."""
import json
from pathlib import Path
import sys
from http.server import ThreadingHTTPServer

from backend import server
from backend.service import Store, Worker
from backend.simulator import ShipmentConsumer


def main():
    directory = Path(sys.argv[1]).resolve()
    allowed = Path(__file__).resolve().parents[1] / '.data'
    if not directory.is_relative_to(allowed.resolve()) or not (directory / 'dist/index.html').is_file():
        raise SystemExit('Use a prepared isolated build inside .data/.')
    database = directory / 'capture.sqlite3'
    if database.exists():
        raise SystemExit('Capture requires a fresh fixture database.')
    server.ROOT = directory  # Only this new process uses the isolated static root.
    store = Store(str(database), 1000)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(store, 0))
    httpd.RequestHandlerClass = server.make_handler(store, httpd.server_port)
    worker, consumer = Worker(store), ShipmentConsumer(store)
    worker.start()
    consumer.start()
    print(json.dumps({'port': httpd.server_port}), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        worker.stopping.set()
        consumer.stopping.set()
        worker.join(timeout=5)
        consumer.join(timeout=5)
        httpd.server_close()
        store.db.close()


if __name__ == '__main__':
    main()
