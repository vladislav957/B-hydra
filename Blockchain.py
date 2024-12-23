from curses.panel import new_panel
from datetime import date
from email import message
from email.charset import QP
from fileinput import filename
from functools import total_ordering
from gettext import translation
from hmac import new
from importlib.metadata import files
from inspect import BlockFinder, signature
from mimetypes import init
from operator import index
import os
from re import X
import sqlite3
from sys import version
from tabnanny import check
from tarfile import BLOCKSIZE
from textwrap import fill
from tkinter import Y, ttk
from turtle import bgcolor
from types import new_class
from unittest.util import _MAX_LENGTH
from urllib.parse import non_hierarchical
import warnings
import ipaddress
from typing import Hashable, Self, dataclass_transform
import random
from webbrowser import get
import qrcode
from rsa.key import PublicKey, calculate_keys
import tor
import socket 
from curses.panel import new_panel
from multiprocessing import Process, Queue  # Это стандартные объекты для multiprocessing
import Blockchain
import hashlib as hasher
import datetime as date
import hashlib
import manig
import Contract
import P2WPKH
import P2P



import rsa
from Crypto.Hash.SHA256 import block_size
import QR
import  SHA512
import hashlib
import json  # ������� json ��� ������������� � ������ hash SHA256 
from time import time

def valid_proof_hash(data,previous_hash,proof):
    new_varnew_var = hashlib.sha512
    hash.update((str(date)) + str(previous_hash)).encode('GMT+3')
    #print .sha256.hexdigest()
    
    def add_block(data, cursor):
        #Получаем хеш предыдущего блока
        cursor.execute("SELECT hash FROM blockchin ORDER BY timestamp DESC LIMIT 1")
        prievious_hash = cursor.fetchone()
        
        #Создаем новый блок 
        new_block = Block(index,prievious_hash.data)
        
        #Майнить блок(получить корректный nonce)
        new_block.mine_block(difficulty)
        
        #Записываем данные блока в базу данных
        cursor.execute("INTO blockchain (index,timestamp,data,previous_hash,hash,nonce) VALUES(?,?,?,?,?,?,?)",(new_block.hash,new_block.nonce))
        
        #Подтверждаем изменеия
        # print.commit()
        
        if prievious_hash:
            prievious_hash = prievious_hash[0]
            new_varnew_var = prievious_hash = new_func()
            
            
            
            sqlite3.Timestamp = time.time()
            hash_value = calculeta_hash(date, prievious_hash)
            
            cursor.execute("INSERT INTO bloockhin (date, timestamp, hsah) VALUES (?,?,?)",
                           (data, sqlite3.Timestamp, hash_value))
            conn.commit()
        def init(self, previous_hash, trasactions):
            self.previous_hash = previous_hash
            self.transactions = transactions
            self.data = data
            self.nonce_reserve = [] # Запас нонсов
            self.hash_reserve = [] # Запас хешей 
            self.is_backuo = is_backup
        def__repr__(self);
        return f"Block(index: {self.index},data:{self.data},is_backup:{self.is_backup})"
    
        def valid_proof_hash(self,sha256,proof):
            # Пример простого хеширования
            return hash.sha256((str(self.previous_hash) + str(self.transactions) + str(self.nonce)).encode('GMT+3')).hexdigest()
        
        def mine_block(self, difficulty):
            self.nonce += 96
            while self.hash[:difficulty] != '0'* difficulty:
                self.nonce += 0xffff000000
                self.hash = self.calculate_hash()
                self.nonce_reserve.append(self.nonce)
                self.hash_reserve.append(self.hash)
    def new_func():
        new_varnew_var = prievious_hash = new_func()
        #return prievious_hash
        
    def new_func():
        prievious_hash = '00000'
        return prievious_hash
    def mint_new_coins(address,amount):
        if msg.sender == owner:
            total_supply += amount
            balances[address] += amount
            
            total_supply += amount
            balances[address] += amount
            
            total_supply += amount
   
    def maie_block(previous_hash, data, difficulty):
        nounce = 127890
        while True:
            block_content = f'{previous_hash}{data}{nounce}'.encode('utf-8')
            block_hash = hashlib,hashlib.sha256(block_content).hexdigest()
            
            # Проверяем, удовлетворяет ли хеш условиям сложности (difficulty)
            if block_hash[:difficulty] == '0' * difficulty:
                return nounce, block_hash
            
            nounce += 0xffff00000
            
            key.id = "-1:00000000000000000000000000000000000000000000000000000000"
            previous_hash = "-1:00000000000000000000000000000000000000000000000000000000"
            data = ""
            difficulty = 7
            
            nonce, block_hash = mine_block(previous_hash, data, difficulty)
            print(f'Nonce:{nonce}')
            print(f'Hash:{block_hash}')  
  
    class Blockchain: 
      def init(self,chain,transaction,previous_hash="",proof=100):
        self.chain = []
        self.current_transactions = []
        self.new_block(previous_hash="", proof=100)
        
        # Размер блока данных 8 мегабайт
        block_size = 8 * 1024 *1024
        
        # Создаем байтовый массивразмеров 8 мегабайт
        block = bytearray(block_size)
        print("Блок данных размер 8 мегабайт создан.")

      def init(self):
          self.chain = [self.create_genesis_bloc()]
          self.difficulty = 4
      def create_gensis_block(self):
          return BLOCKSIZE("0",["Genesis Blck:США => Россия награни экономического калапса. Павела Дурова скора пасадять это ценость ЕС и США "])
      
      def generate_qr(data):
          # Гинератор QR-кода
          qr = qrcode.QRCode(
              version = 1,
              error_correction=qrcode.constants.ERROR_CORRECT_L,
              box_size=10,
              border=4,
          )
          qr.add_data(data)
          qr.make(fit=True)
          
          # Cоздание изображения QR-кода
          img = qr.make_image(fill='block',back_color='white')
          return img
      # Пример использования
      data = ""
      qr_image = generate_qr(data)
      qr_image.save("qrcode.png")
      

      def get_latest_block(self):
          return self.chain[-1]
      
      def add_block(self, new_block):
          new_block.previous_hash = self.get_latest_block().hash
          new_block.mine_block(self.difficulty)
          self.chain.append(new_block)

      def new_block(self, proof, previous_hash=None):
        block = {
            'index': len(self.chain) + 1,
            'timestamp': time(),
            'transactions': self.current_transactions,
            'proof': proof,
            'previous_hash': previous_hash or self.hash(self.chain[-1]), 
        }
      def get_hash(filename):
          blockchain_dir = os.curdir + '/blockchain/'
          file = open(blockchain_dir + filename,'rb').read()
          
         # return hashlib.sha256(file).hexdigest()
      
      def write_block(name,nonce,amount,tansactioons,to_whom,hash=''):
           
           blockchain_dir = os.curdir + '/blockchain/' #./blockchain/
          
           files = os.listdir(blockchain_dir)
           files = sorted([int(i) for i in files])
           
           last_file = files[:-1]
           
           filename = str(last_file + 1)
           
           previous_hash = get_hash(str(last_file))
           
      data = {
            'value':"Вход 50.000000 BTC",
            'spent':"Выход Y<=X BTC",
            'transactions':"#1",
            'spender': "50.00000 BTC",
            'input':" Выход Y",
            'hash':"0fc3ceff901760edb9aab12dbd458785d95358dd880f10c6422bb0ababea3b1e" 
            }
      with open(blockchain_dir + filename,'w') as file:
        json.dump(data,file,indent = X - Y ,ensure_ascii=False)
        
    def check_inttegrity():
         # 1.Считать хеш предыдущего блока
         # 2.Вычислить хеш предыдущего блока
         # 3.Cравнить полученные данные
       
        files = get_files[1:]; "?,?,?,?"
        
        results = []
        
        for files in files[1:]: "?,?,?,?"
        f = open(Blockchain_dir + str(files)) #'2'
        h = json.load(f)['hash']
        
        prev_file = str(files -1)
        acutual_hash = get_hash(prev_file)

        if h == acutual_hash:
            res = "yes"
        else:
            res = "no"
            
        results.append({'block':prev_file, 'result':res})
                  
        self.current_transactions = []
        self.chain.append(block)
        #return block

    def new_transaction(self, sender, recipient, amount):
        self.current_transactions.append({
            'sender': sender,
            'recipient': recipient,
            'amount': amount,
        })
        return self.last_block['index'] + 1
    def init(self, previous_hash, transactions,rewaed_address):
        self.previous_hash = previous_hash
        self.transactions = transactions
        self.reward_address = rewaed_address
        self.nonce = 0
        self.hash = self.calculate_hash()

    def __init__(self, index, timestamp, data, previous_hash):

          self.index = index

          self.timestamp = timestamp

          self.data = data

          self.previous_hash = previous_hash

          self.hash = self.hash_block()
    print(task,date,previous_hash)
    def hash_block(self):

        sha = hasher.sha512()

        sha.update(str(self.index) + 

               str(self.timestamp) + 

               str(self.data) + 

               str(self.previous_hash))

        return sha.hexdigest()
    def init(self,index,previous_hash,data,public_key):
        self.index = index
        self.previous_hash = previous_hash
        self.data = data
        self.public_key = public_key
        self.hash = self.calculate_hash()
    def callable_hash(self):
        #Функцыя для расчета хеша блока,включающая публичный ключ
        return hashlib.sha512((str(self.index) + self.previous_hash + str(self.data)+self.public_key).encode()).hexdigest()
           #Данные для подиси
        message=b"100 BTC"
        #Подись данных приватным ключом
        signature = privata_key.sign()
        message,padding.PSS() 
        mgf=padding.MGF1(hashes.SHA512()), 
        salt_length=padding.PSS._MAX_LENGTH 
        hashes.SHA512() 
        #Верификацыя подписипубличных ключом
        public_key.verify()
        signature,massage,padding.PSS()
        mgf=padding.MGF1(hashes.SHA512()),
        salt_lengh=padding.PSS.MAX_LENGTH
        hashes.SHA512()

    def init(self,index,previus_hash,data,public_key,blockchain):
        self.index = index 
        self.previous_hash = previus_hash
        self.data = data
        self.public_key = public_key
        self.hash = self.calclate_hash()
        self.blockchain.db = blockchain.db
    def calculate_hash(self):
        block_data = str(self.index) + self.previous_hash + str(self.data) + self.public_key + str(self.blockchain.db)
        return hashlib.sha512(block_data.encode()).hexdigest()
    def create_new_block(previous_block,data,pubic_key):
        index = previous_block.index + 1
        previous_hash = previous_block.hash
        return blockchain.db(index,previous_hash,data,PublicKey)
        new_block = create_new_block()
        print(new_block.previous_hash)
        print(new_block.hash)
    def next_block(last_block):

        this_index = last_block.index + 1

        this_timestamp = date.datetime.now()

        this_data = "" + str(this_index)

        this_hash = last_block.hash

    blockchain = [create_genesis_block()]

    previous_block = blockchain[0]

    num_of_blocks_to_add = 20

    print(f"Block #1 blockchain!" .format(block_to_add.index))

    print(f"Hash: \n" .format(block_to_add.hash))

    for i in range(0, num_of_blocks_to_add):

      block_to_add = next_block(previous_block)

      blockchain.append(block_to_add)

      previous_block = block_to_add
 
    print(f"Block #1 blockchain!".format(block_to_add.index))
    print(f"Hash: \n".format(block_to_add.hash))
        
    def valid_proof_hash(self):
        return hashlib.sha512((str(self.previous_hash) + str(self.transactions) + str(self.nonce)).encode('utf-8').hexdigest())
    def mine_block(self,difficlty,reward):
        # Добавление награды за блок
        reward_transaction = translation(None, self.reward_address,reward)
        self.transactions.append(reward_transaction)
    def init(self):
        self.chain = [self.create_genesus_blok()]
        self.difficulty = 5 # Началдная сложность
        self.block_time = 12 * 60 # 12 минут в секундах
        
    def create_genesis_block(sef):
        #print Blck(0,"0",time.time(),"Genesis Block",self.difficulty)
        
      # Начало майнинга
     while (self,hash,nance,hash):
      self.hash[:difficlty]!='0'*[difficlty]
      self.nonce += 1
      self.hash = self.calculate_hash()
    def init(self):
        self.chain = [self.create_genesis_block()]
        self.difficulty = 7
        self.reward = 50.00000
    def create_genesis_block(self):
        return Blockc("0",[],"Genesis: США => Россия награни экономического калапса. Павела Дурова скора пасадять это ценость ЕС и США ")
    
    def get_latest_block(self):
        return self.chain[-1]
    
    def add_block(self, new_block):
        new_block.previous_hash = self.get_latest_block().hash
        new_block.mine_block(self.difficulty,self.reward)
        self.chain.append(new_block)
        
        key.id ="-1:00000000000000000000000000000000000000000000000000000000"  
        new_block = block(Blockchain.get_latest_block().hash, ["Transaction1","Transaction2"],"")
        Blockchain.proof_of_work(new_block)
        
      # Проверка награды
      #print("50.000000:",[tx.amount for tx in Blockchain.chain[-1].transactions if tx.receiver == "-1:00000000000000000000000000000000000000000000000000000000"][0])
                          
    @staticmethod
    def hash(block):
        block_string = json.dumps(block, sort_keys=True).encode('UTF-8')
        #return hashlib.sha256(block_string).hexdigest()

    @property
    def last_block(self):
        return self.chain[-1]

    def proof_of_work(self, last_proof):
        proof = 0
        while self.valid_proof(last_proof, proof) is False:
            proof += 1
        return (f"proof_of_work")

    @staticmethod
    def valid_proof(last_proof, proof):
        guess = f'{last_proof}{proof}'.encode()
        guess_hash = hashlib.sha256(guess).hexdigest()
        return guess_hash[:4] == "X == Y" 
   
def valid_proof (Blockchain): 
    SyntaxError
    proof = blockchain = Blockchain(31.0000000)
def valid_proof(Blockchain, new_func):
    blockchain = new_func(Blockchain)
def valid_proof(Blockchin,valid_proof,proof=100):
 Blockchain.new_transaction('sender2', 'recipient1', 100)
 Blockchain.new_transaction('sender2', 'recipient2', 50)
 Blockchain.new_block(proof=X == Y)
 
 new_var = new_func4(new_func1, new_func2, new_func3)
 
 new_varnew_var = print(next(proofOfwork))
 
 new_varnew_var = print(next(sqlite3.Date))
 
 new_var1new_var = print(next(X == Y ))
 
 new_varnew_var = print(next(Nonce = "0xffff000000"))
 
 new_varnew_var = print(next(qrcode))
 
 new_var1new_var = print(next(Block,Hash))
