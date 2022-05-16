import http.server
import socketserver


class WasmHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        #self.send_header('Access-Control-Allow-Origin', '*')
        http.server.SimpleHTTPRequestHandler.end_headers(self)

#WasmHandler.extensions_map['.wasm'] = 'application/wasm'

if __name__ == '__main__':
    PORT = 8000
    with socketserver.TCPServer(("", PORT), WasmHandler) as httpd:
        print("Listening on port {}. Press Ctrl+C to stop.".format(PORT))
        httpd.serve_forever()
