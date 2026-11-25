"""Offline OpenAI-compatible service for the disposable browser test project."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from threading import Barrier, BrokenBarrierError

concurrent_requests = Barrier(2)


class Handler(BaseHTTPRequestHandler):
    def send(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != '/v1/models':
            return self.send_error(404)
        self.send({'data': [{'id': 'local-study:v2'}, {'id': 'local-small:v1'}]})

    def do_POST(self):
        if self.path != '/v1/chat/completions':
            return self.send_error(404)
        payload = json.loads(self.rfile.read(int(self.headers['content-length'])))
        source = json.loads(payload['messages'][1]['content'])
        instructions = payload['messages'][0]['content']
        if 'до 750 слов' not in instructions or (source['slides'] and 'не более 3' not in instructions):
            return self.send_error(400)
        if not source['transcript'] or payload['model'] != 'local-study:v2':
            return self.send_error(400)
        if source['title'].startswith('Параллельная проверка'):
            try:
                concurrent_requests.wait(timeout=20)
            except BrokenBarrierError:
                return self.send_error(504)
            # Keep both generation slots occupied while the browser observes the next two transcripts.
            time.sleep(15)
        else:
            time.sleep(4)  # Let the regular generation stage appear in browser polling.
        self.send({'choices': [{'message': {'content': '## Результат\n\nРаспознана речь.\n\n[[slide:2]]'}, 'finish_reason': 'stop'}]})


ThreadingHTTPServer(('0.0.0.0', 8091), Handler).serve_forever()
