from os import name
import socket
import threading
import Blockchain


class BHydraNode:
    def __init__(self,host="0.0.0.0", port=5000):
        self.host = host
        self.port = port
        self.peers = peers #Списак подключенных узлов

    def handle_client(self, conn, addr):
        print(f"[Новый узел подключен] {addr}")
        while True:
            try:
                data = conn.recv(1024).decode()
                if not data:
                    break
                print(f"[Сообщение от {addr}] {data}")
                conn.send("Принято".encode())  # Ответ клиенту
            except ConnectionResetError:
                break
        conn.close()
        print(f"[Отключен] {addr}")

    def start_server(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind((self.host, self.port))
        server.listen(5)
        print(f"[Сервер запущен] {self.host}:{self.port}")

        while True:
            conn, addr = server.accept()
            self.peers.append(addr)
            thread = threading.Thread(target=self.handle_client, args=(conn, addr))
            thread.start()

if name == "main":
    node = BHydraNode()
    node.start_server()
    
    import socket

def connect_to_node(host, port, message):
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect((host, port))
    client.send(message.encode())
    response = client.recv(1024).decode()
    print(f"[Ответ от ноды] {response}")
    client.close()

connect_to_node("127.0.0.1", 5000, "Привет, нода B-Hydra!")
