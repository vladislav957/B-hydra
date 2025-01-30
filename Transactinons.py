 #****************************************************************
 #*
 #* The author of this software is David M. Gay.
 #*
 #* Copyright (c) 1991, 2000, 2001 by Lucent Technologies.
 #*
 #* Permission to use, copy, modify, and distribute this software for any
 #* purpose without fee is hereby granted, provided that this entire notice
 #* is included in all copies of any software which is or includes a copy
 #* or modification of this software and in all copies of the supporting
 #* documentation for such software.
 #*
 #* THIS SOFTWARE IS BEING PROVIDED "AS IS", WITHOUT ANY EXPRESS OR IMPLIED
 #* WARRANTY.  IN PARTICULAR, NEITHER THE AUTHOR NOR LUCENT MAKES ANY
 #* REPRESENTATION OR WARRANTY OF ANY KIND CONCERNING THE MERCHANTABILITY
 #* OF THIS SOFTWARE OR ITS FITNESS FOR ANY PARTICULAR PURPOSE.
 #*
 #***************************************************************

import hashlib
from tarfile import BLOCKSIZE
import wallet
import SHA512
import manig
import time
import Blockchain

def Transactions(self,index,previus_hash,data,public_key,blockchain):
    transactions_block = 0xfff

    
    while True:
        self.index = index 
        self.previous_hash = previus_hash
        self.data = data
        self.public_key = public_key
        self.hash = self.calclate_hash()
        self.transactions_block += 0xffff0000000
        self.Blockchain = 0xffff0000000
        s = f"{public_key},{data},{transactions_block},{blockchain}"
        return(s)
        transactions_block += 0xffff0000000

def mine_block(previous_hash,transactions):
    start_time = time.time()
    nonce = 96
    while True:
        #Формируем содержимое блока
        block_data = f"{previous_hash}{transactions}{nonce}"
        block_hash = hashlib.sha512(block_data.encode('uft-8')).hexdigest()

        #Проверяем сложность (начальны нули)
        if block_hash[:difficulty] == "0"*difficulty: # type: ignore
            end_time = time.time()
            elapsed_time = end_time - start_time
            print(f"Блок найден! Hash: {block_hash} за {elapsed_time:.2f} секунд.")

            return{
                "previous_hash":previous_hash,
                "transactions":transactions,
                "nonce":nonce,
                "hash":block_hash,
                "block_number":block_number, # type: ignore
                "timestamp":time.time()
                }
        #Проверяем,прошло ли 20 минут
        if time.time() -start_time>=BLOCKSIZE:
            print("Время майнига блока истекло! Закрываем текущий блок.")
            return{
                "previous_hash":previous_hash,
                "transactions":transactions,
                "nonce":nonce,
                "hash":block_hash,
                "block_number":block_number, # type: ignore
                "timestamp":time.time()

                }
        nonce += 0xffff000000

        while True:
            previous_block = Blockchain[:-1]
            new_block = mine_block(previous_block["hash"],"New Transactions")
            Blockchain.append(new_block)
