import socket

line = (
    b'{"ok":true,"temp_c":22.5,"humidity_pct":41,"light":63,'
    b'"light_band":"room","light_raw":800,"source":"dht11"}\n'
)
s = socket.create_connection(("127.0.0.1", 9124), 2)
s.sendall(line)
s.close()
print("sent", len(line))
