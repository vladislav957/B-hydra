#from asyncio import log
from asyncio import log
import socket
from xmlrpc import client
import wallet
import TCP
import socket
import rsa # type: ignore
from threading import Thread
from time import sleep
import datetime


# Создание клиентского сокета
#client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# P2P сервер
class P2P:
    def __init__(self, _port: int, _max_clients: int = 1):
        # Индикатор работы сервера
        self.running = True
        # Порт сервера
        self.port = _port
        # Максимальное кол-во подключений
        self.max_clients = _max_clients
        # Подключённые пользователи
        self.clients_ip = ["" for i in range(self.max_clients)]
        # Словарь с входящими сообщениями
        self.incoming_requests = {}
        # Логи клиентов
        self.clients_logs = [log for i in range(self.max_clients)] # type: ignore
        # Клиентские сокеты
        self.client_sockets = [socket.socket() for i in range(self.max_clients)]
        # Таймауты клиентов
        for i in self.client_sockets:
            i.settimeout(0.2)
        # Ключи для шифрования исходящих сообщений
        self.keys = [rsa.key.PublicKey for i in range(self.max_clients)]
        # Ключи для дешифрования входящих сообщений
        self.my_keys = [rsa.key.PrivateKey for i in range(self.max_clients)]
        # Информация загруженности сокетов
        self.socket_busy = [False for i in range(self.max_clients)]
        # Чёрный список
        self.blacklist = ["127.0.0.1"] + log.read_and_return_list("blacklist.txt") # type: ignore
        # Серверный сокет
        self.server_socket = socket.socket()
        # Таймаут сервера
        self.server_socket.settimeout(0.2)
        # Бинд сервера
        self.server_socket.bind(('localhost', _port))
        self.server_socket.listen(self.max_clients)
        self.log = log("./server.log") # type: ignore
        self.log.save_data("Server initialized")

    # server control
def connect_to_server(client):
    try:
        client.connect(("IPv4-адрес: 127.0.0.1:5554", 54000)).decode('utf-8')
        print("Connected to server")
    except ConnectionError as e:
        print(f"Connection error: {e}")

def receive_message(client):
    try:
        response = client.recv(4096)
        print(f"Server: {response.decode('utf-8')}")
    except Exception as e:
        print(f"Error receiving data: {e}")

def main():
    connect_to_server(client)
    receive_message(client)
    client.close()

# Создаёт сессию с этим пользователем
    def create_session(self, _address: str):
        self.log.save_data("Creating session with {}".format(_address))
        ind = self.__get_free_socket()
        if _address in self.blacklist:
            self.log.save_data("{} in blacklist".format(_address))
            return
        if ind is None:
            self.log.save_data("All sockets are busy, can`t connect to {}".format(_address))
            return
        try:
            self.__add_user(_address)
            thread = Thread(target=self.__connect, args=(_address, 1))
            thread.start()
            thread.join(0)
            connection, address = self.server_socket.accept()
            connection.settimeout(0.2)
        except OSError:
            self.log.save_data("Failed to create session with {}".format(_address))
            self.__del_user(_address)
            return
        my_key = rsa.newkeys(512)
        self.raw_send(_address, my_key[0].save_pkcs1())
        key = connection.recv(162).decode()
        self.clients_logs[ind].save_data("from {}: {}".format(_address, key))
        key = rsa.PublicKey.load_pkcs1(key)
        self.__add_keys(_address, key, my_key[1])
        while self.running and self.socket_busy[ind]:
            try:
                data = connection.recv(2048)
            except socket.timeout:
                continue
            except OSError:
                self.close_connection(_address)
                return
            if data:
                data = rsa.decrypt(data, self.my_keys[ind])
                self.__add_request(_address, data)
        try:
            self.close_connection(_address)
        except TypeError or KeyError:
            pass

# Подключается к пользователю
    def __connect(self, _address: str, *args):
        ind = self.__get_ind_by_address(_address)
        try:
            self.client_sockets[ind].connect((_address, self.port))
            self.socket_busy[ind] = True
            return True
        except OSError:
            return False

    # Перезагружает сокет
    def __reload_socket(self, _ind: int):
        self.client_sockets[_ind].close()
        self.client_sockets[_ind] = socket.socket()
        self.socket_busy[_ind] = False

    # Закрывает соединение
    def close_connection(self, _address: str):
        ind = self.__get_ind_by_address(_address)
        self.__del_key(_address)
        self.__reload_socket(ind)
        self.__del_user(_address)

    # Останавливает сервер
    def kill_server(self):
        self.running = False
        sleep(1)
        self.server_socket.close()
        self.log.kill_log()
        for i in self.client_sockets:
            i.close()
        for i in self.clients_logs:
            try:
                i.kill_log()
            except TypeError:
                pass
# Отправляет сообщение с шифрованием
    def send(self, _address: str, _message: str):
        ind = self.__get_ind_by_address(_address)
        try:
            self.clients_logs[ind].save_data("to {}: {}".format(_address, _message))
            self.client_sockets[ind].send(rsa.encrypt(_message.encode(), self.keys[ind]))
            self.log.save_data("Send message to {}".format(_address))
        except OSError:
            self.log.save_data("Can`t send message to {}".format(_address))
# Отправляет сообщение без шифрования
    def raw_send(self, _address: str, _message: bytes):
        ind = self.__get_ind_by_address(_address)
        try:
            self.client_sockets[ind].send(_message)
            self.clients_logs[ind].save_data("to {}: {}".format(_address, _message))
            self.log.save_data("Raw send message to {}".format(_address))
        except OSError:
            self.log.save_data("Raw send to {} Failed".format(_address))
 # Добавляет пользователя
    def __add_user(self, _address: str):
        ind = self.__get_free_socket()
        self.clients_logs[ind] = log("{}.log".format(_address))
        self.clients_ip[ind] = _address
        self.incoming_requests[_address] = []
        self.log.save_data("Added user {}".format(_address))

    # Добавляет ключ для шифрования и дешифрования адресу
    def __add_keys(self, _address: str, _key: rsa.key.PublicKey, _my_key: rsa.key.PrivateKey):
        ind = self.__get_ind_by_address(_address)
        try:
            self.keys[ind] = _key
            self.my_keys[ind] = _my_key
        except TypeError:
            return
        # Добавляет входящее сообщение от адреса
    def __add_request(self, _address: str, _message: bytes):
        self.incoming_requests[_address].append(_message.decode())
        self.clients_logs[self.__get_ind_by_address(_address)].save_data("from {}: {}".format(_address, str(_message)))
        self.log.save_data("Get incoming message from {}".format(_address))

    # get

    # Возвращает индекс первого свободного соккета
    # if self.__get_free_socket() is not None: *
    def __get_free_socket(self):
        for i in range(len(self.socket_busy)):
            if not self.socket_busy[i]:
                return i
        return None
    # Возвращает номер индекса, к которому подключён адрес
    def __get_ind_by_address(self, _address: str):
        for i in range(len(self.clients_ip)):
            if self.clients_ip[i] == _address:
                return i
        else:
            return None

    # Возвращает входящее сообщение от адреса
    def get_request(self, _address: str):
        data = self.incoming_requests[_address][0]
        self.incoming_requests[_address] = [self.incoming_requests[_address][i]
                                            for i in range(1, len(self.incoming_requests[_address]))]
        return data
     # Проверяет наличие входящих сообщения от пользователя
    # if self.check_request(_address): *
    def check_request(self, _address: str):
        return bool(self.incoming_requests.get(_address))

    # return True if you already connected to _address else False
    def check_address(self, _address: str):
        return True if _address in self.clients_ip else False
    # Удаляет пользователя
    def __del_user(self, _address: str):
        ind = self.__get_ind_by_address(_address)
        self.clients_logs[ind].kill_log()
        self.clients_logs[ind] = log
        self.clients_ip[ind] = ""
        self.incoming_requests.pop(_address)
        self.log.save_data("Deleted user {}".format(_address))

    # Удаляет пользователя
    def __del_key(self, _address: str):
        ind = self.__get_ind_by_address(_address)
        self.keys[ind] = rsa.key.PublicKey
        self.my_keys[ind] = rsa.key.PrivateKey
        # Возвращает число подключённых пользователей
    def __len__(self):
        num = 0
        for i in self.clients_ip:
            if i != "":
                num += 1
        return num

    # возвращает Правду если есть хотя бы одно подключение
    def __bool__(self):
        for i in self.clients_ip:
            if i != "":
                return True
        return False
class Log:
    def __init__(self, _name: str):
        self.name = _name
        try:
            self.file = open(_name, "a")
        except FileNotFoundError:
            self.file = open(_name, "w")
        self.save_data("Log started at " + str(datetime.datetime.now()))
        self.file.close()

    # Сохраняет информацию в файл
    def save_data(self, _data: str):
        self.file = open(self.name, "a")
        self.file.write("{}\n".format(_data))
        self.file.close()

    # Возвращает данные из файла в виде листа
    @staticmethod
    def read_and_return_list(_name: str):
        try:
            file = open(_name, "r")
        except FileNotFoundError:
            return []
        data = file.read()
        return data.split("\n")

    # Останавливает лог
    def kill_log(self):
        self.file = open(self.name, "a")
        self.save_data("Log stopped at {}\n".format(datetime.datetime.now()))
        self.file.close()
