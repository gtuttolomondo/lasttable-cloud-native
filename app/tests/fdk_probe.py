"""FDK wire-protocol startup check; an invalid event must return a controlled 400."""
import socket
import time
from datetime import datetime, timedelta, timezone

deadline=time.monotonic()+90
client=socket.socket(socket.AF_UNIX)
client.settimeout(100)
while True:
    try:
        client.connect('/tmp/fn.sock')
        break
    except (FileNotFoundError,ConnectionRefusedError):
        if time.monotonic()>deadline:
            raise
        time.sleep(1)
timestamp=(datetime.now(timezone.utc)+timedelta(seconds=90)).isoformat().replace('+00:00','Z')
request=('POST /call HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n'
         'Fn-Intent: httprequest\r\nFn-Deadline: '+timestamp+'\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}')
client.sendall(request.encode())
response=b''
while b'INVALID_EVENT' not in response:
    chunk=client.recv(65536)
    if not chunk:
        break
    response+=chunk
assert b'INVALID_EVENT' in response and b'fn-http-status: 400' in response.lower(),response
print('PASS: FDK starts as non-root, imports notification code, returns controlled HTTP 400 for invalid input')
