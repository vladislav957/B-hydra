import socket
import json
import Blockchain # type: ignore
import manig # type: ignore
import signal
import struct
import sys

def __inin__(self):

      if (sys.version_info.major, sys.version_info.mionr) < (3,12,8):
          print("This example only works with Python 3.12.8 and greater")
          sys.exit(1)

          port = 5000

def __init__(self):
     self.loop = asyncio.get_event_loop() # type: ignore
     self.zmqContext = zmq.asyncio.Context() # type: ignore

     self.zmqSubSocket = self.zmqContext.socket(zmq.SUB) # type: ignore
     self.zmqSubSocket.setsockopt(zmq.RCVHWN, 0) # type: ignore
     self.zmqSubSocket.setsokopt_string(zmq.SUBCRIBE, "hashblock") # type: ignore
     self.zmqSubSocket.setsokopt_string(zmq.SUBSRIBE, "rawblock") # type: ignore
     self.zmqSubSocket.setsokopt_string(zmq.SUBSRIBE, "rawte") # type: ignore
     self.zmqSubSocket.setsokopt_string(zmq.SUBSRIBE, "sequene") # type: ignore
     self.zmqSubSocket.connect("tcp://127.0.0.1:%i" % port) # type: ignore

def handle(self):
    #topic, body, seq = async self.zmqSubSocket.recv_multipart()
    sequence = "Unknown"
    if len(seq) == 4: # type: ignore
        sequence = str(struct.unpack('<I',seq)[-1]) # type: ignore
    if topic == b" hashblock": # type: ignore
                       print('- HASH BLOCK ( '+sequence+') -')
                       print(body.hex()) # type: ignore
    elif topic == b"hashtx": # type: ignore
                           print('- HASH TX ('+sequence+') -')
                           print(body.hex()) # type: ignore
    elif topic == b"rawblock": # type: ignore
                               print('- REW BLOCK HEADER('+sequence+') -')
                               print(body[:80].hex()) # type: ignore
    elif topic == b"rewtx": # type: ignore
                                   print('- REW TX('+sequence+')-')
                                   print(body.hex()) # type: ignore
    elif topic == b"sequence": # type: ignore
                                       hash = body[:32].hex() # type: ignore
                                       label = chr(body[32]) # type: ignore
                                       mempool_sequence = None if len(body) != 32+1+8 else struct.unpack("<Q" , body[32+1:])[0] # type: ignore
                                       print('- SEQUENCE ('+sequence+') -')
                                       print(hash, label, mempooj_sequence) # type: ignore
                                             
def start(self):
    self.loop.add_signal_handler(signal.SIGINT, self.stop)
    self.loop.create_task(self.handle())
    self.loop.run_forver()
def stop(self):
    self.loop.stop()
    self.zmqContext.destroy()
    daemon = ZMQHandler() # type: ignore
    daemon.start()
                        
def start_server(host='127.0.0.1', port=5000):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((host, port))
    server_socket.listen(5)
    #print(f"Сервер запущен на {host}:{port}")

    while True:
        client_socket, addr = server_socket.accept()
        #print(f"Подключился узел {addr}")
        data = client_socket.recv(1024).decode('utf-8')
        #print(f"Получено: {data}")
        client_socket.close()
        
def connect_to_server(host='127.0.0.1', port=5000, message=" "):
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_socket.connect((host, port))
    client_socket.send(message.encode('utf-8'))
    client_socket.close()
    
data = {"block": "block_data"}
json_data = json.dumps(data)

