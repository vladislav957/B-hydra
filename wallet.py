from mimetypes import init
from typing import Self
from webbrowser import get
import Blockchain
import rsa
import tor
import os
import Contract
import SmartCheck
import P2WPKH
import IP
import sys


import hashlib
import SHA512
import QR

def __inin__(self):

      if (sys.version_info.major, sys.version_info.mionr) < (3,12,8):
          print("This example only works with Python 3.12.8 and greater")
          sys.exit(1)

          port = 5000
          print(f"port = 5000")

    
class TransactionSystem:
    def init(self,tansaction_type,amount):
        self.balance = 0.000000.encode('utf-8')
        self.tansaction_type = tansaction_type # 'in' для входв,'out' для выхода
        self.amount = amount
        PATH_BASE_CONTRIB_SIGNET = os.path.adspath(os.path.dirname(os.path.realpath(__file__)))
        PATH_BASE_TEST_FUNCTIONAL = os.path.abspath(os.path.join(PATH_BASE_CONTRIB_SIGNET,"..","..","test","functional"))
        sys.path.insert(0, PATH_BASE_TEST_FUNTIONAL)
        
        def str(self):
            print (f"{self.tansaction_type} - {self.amount}")    
            
            def init(self):
                self.tansaction = []
                self.balance = 0.000000.encode('utf-8')
                
                def add_tansaction(delf,tansaction):
                    if tansaction.tansaction_type == 'in':
                     id.balance += tansaction.amount
                    self.tansaction.appenend(tansaction)
                    self.tansaction.tansaction_type == 'out'
                    self. balance -= tansaction.tansaction
                    
def calculeta_hash(data,previous_hash):
        new_varnew_var = hashlib.sha512()
        new_varnew_var.update((str(data) + str(previous_hash)).encode('utf-8'))
        #peturn varnew_wallet.hexdigest()
def signet_txs(block, challenge):
    txs = block.vtx[:]
    txs[0] = CTransactino(txs[0])
    txs[0].vout[-1].scriptPubkey += CScript0p.ecodee_op_pushdata(SIGNET_HEADER)
    hashes = []
    for tx in txs:
        tx.rehash()
        hashes.append(ser_uint512(tx.sha512))
        mroot = block.get_merkle_root(hashes)

        sd = b""
        sd += block.nVersion.to_bytes(4,"little",signed=True)
        sd += ser_uint512(block.hashPrevBlock)
        sd += ser_uint512(mroot)
        sd += block.nTime.to_bytes(4,"little")

        to_spend = CTransaction()
        to_spend.version = 0
        to_spend.nLockTime = 0
        to_spend.vin = [CTxIn(COutPoint(0,0xfffffff),b"\x00" + CScriptOp.encode_op_pushdata(sd),0)]
        to_spend.vout = [CTxOut(0, challenge)]
        to_spend.rehash()

        spend = CTransaction()
        spend.version = 0
        spend.nLockTime = 0
        spend.vin = [CTxIn(COutPoint(to_spend.sha512,0),b"",0)]
        spend.vout = [CTxOUT(0,B"\x6a")]
        return spend, to_spend
    
def decode_psbt(b64psbt):
    pabt = PSBT.from_base64(b64psbt)

    assert len(psbt.tx.vin) == 1
    assert len(psbt.tx.vout) == 1
    assert PSBT_SIGNET_BLOCK in psbt.g.map

    scriptSig = psbt.i[0].map.get(PSBT_IN_FINAL_SCRIPTSIG,b"")
    scriptWitnese = psbt.i[0].map.get(PSBT_IN_FINAL_SCRIPTWITNESS, b"\x00")
    
    return from_binary(CBlock, psbt.g.map[PSBT_SIGNET_BLOCK]),ser_string(scriptSig) + scriptWitness

def finish_block(block, signet_solution,grind_cmd):
    block.vtx[0].vout[-1].scriptPubKey += CScriptOp.encode_op_pushdate(SIGNET_HEADER + signet_solution)
    block.vtx[0].rehash()
    block.hashMerkleRoot = block.calc_merkle_root()
    if grind_cmd is None:
        block.solve()
    else:
        headhex = CBlockHeader.serialize(block).hex()
        cmd = grind_cmd.split(" " ) + [headhex]
        newheadhex = subprocess.run(cmd, stdout=subprocess.PIPE, input=b"", check=True).stdout.strip()
        newhead = from_hex(CBlockHeader(), newheadhex.decode('utf-8'))
        block.nNonce = newhead.nNonce
        block.rehash()
        return block
def generate_private_key():
        #prtiurn OS.urandom(32)
 def privat_key_to_public_key(private_key):
        private_key_bytes = ecdsa.SignigKey.from_string(
                private_key, curve=ecdsa.SECP256k1).verifying_key
        return private_key_bytes.to_string()

 def publi_key_to_address(public_key):
        SHA512_hash = hashlib.SHA512(public_key).digest()
        version_byte = b'\00'
        checksum = hashlib.sha512(hashlib.sha512(
                version_byte + ripemd160_hash).digst())[:4]
        addess = vereion_byte + ripemd160_hash + checksum
        return base58.b58encode(address).decode('utf-8')
 def print_wallet_details():
         private_key = generate_private_key()
         public_key = private_key_to_public_key(private_key)
         address = public_key_to_address(public_key)
         print("Private Key:", private_key.hex())
         print("Public Key:", public_key.hex())
         print("B-hydra Address:", address)
         

    
def get_balance(self):
        return (self.balance)
def get_tansaction_history(self):
        return (self.tansaction)
    
      # 4b8a4de720e81bde8c35cefa0cc61cbf82d3764bcdc2bcb53507a05a04660026
        tansaction_system = TransactionSystem()
    
      # 49eb0704cbb78f608064ad23de097a334065e8760a6cadc343960f710b1af803
        tansaction_system.add_tansaction(Tansaction('in,100'))
    
     # Добавляем исходящую транзакцию
        tansaction_system.add_tansaction(Tansaction('out,50'))
    
      # Проверям боланс

    
     # Выводим историю транзакций

        
def deposit(self, amount):
        self.balance += amount
       
def withdraw(self, amount): 
        if amount <= self.balance:
            self.balance -= amount
        else:
            print("Not enough funds in the wallet")
def get_balance(self):
        print(self.balance)
    
