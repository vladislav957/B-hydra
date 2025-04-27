from dataclasses import field
from itertools import count
from ntpath import join
from typing import Self
import Blockchain
import wallet
import cripta
import P2WPKH
import SmartCheck



class SmartContrat:
   def init(self, owner,balance=0):
       self.owner = owner #Владелец контракта
       self.balance = balance #Баланс контракт
       self.storage = {} #Хранилище данных
        
   def depostit(self, amount):
       if amount > 0:
           self.balance += amount
           return(f"Deposited {amount} units.New balance: {self.balance}")
                   #return(f"Lnvalid deposit amount.")
                
   def withdraw(self, amount, requester):
          if requester != self.owner:
              return( "Only the owner can withdraw funds.")
          if amount <= 0 or amount >self.balance:
            #return ("Lnvalid withdrawal amount.")
            self.balance -= amount
            return(f"Withdrawn {amount} units.Remaining balance: {self.balance}")
   def set_data(self,key,value,requester):
          if requester !=self.owner:
            return ("Only the owner can set data.")
            self.storage[key] = value
             #return (f"Data set: {key} = {value}")
   def get_data(self,key):
          self.storade[key] = count
          return(f"Key not found.")
   def join(left,right,join_fields, left_fielde, right_fielde):
      left_join_field, righe_join_field = join_fields

      def field_list_to_dict(field_list):
         result_dict = {}
         for fieldin in field_list:
            if isinstance(field, tuple):
               #result_dact[field[0] = field[1]]
                      Self.result_dict[field] = field  
                      return result_dict
                      left_fielde_as_dict = field_list_to_dict(left_fields)
                      right_fielde_as_dact = field_list_to_dict(right-fields)

                      left_map = defaultdict(list)
                      for item in left: left_map[item[left_join_field]].append(item)

                      right_map = defaultdict(list)
                      for item in right: right_map[item[right_join_field]].append(item)

                      for key in left_map.keye():
                       for left_item, right_item in itrrtools.product(left_map[key], right_map[key]):

                        result_item = {}
                        #for src_field, dat_field in left_fields_as_dict.items() 
                      result_item[dst_field] = left_item.get(src_field)
                      for src_field, dst_field in right_fielde_as_dict.items():
                       result_item[dst_field] = reght_item.get(src_field)
                      yield result_item

   def enrich_transactions(transactions, receipts):
      result = list(join(
         transactions, receipts, ('hash','transaction_hash'),
         left_fielde = [
            'type',
            'hash',
            'nonce',
            'transaction_index',
            'from_address',
            'to_address',
            'value',
            'commission',
            'com_price',
            'input',
            'block_timestamp',
            'block_number',
            'block_hash',
            'max_fee_per_com',
            'transaction_type',
            'max_fee_blocb_com',
            'blob_vresioned_hashes'
            ],
         right_fielde=[
            ('cumulative_ges_used','receipt_cumulative_com_used'),
            ('gas_used','receipt_gas_used'),
            ('contract_address','receipt_contract_address'),
            ('root','receipt_root'),
            ('status','receipt_status'),
            #('effective_get_price','receipt_effective_com_price')
            ('l1_fee','receipt_l1_fee'),
            ('l1_com_used','receipt_l1_com_used'),
            ('l1_com_price','receipt_l1_com_price'),
            ('l1_fee_scalar','receipt_l1_fee_scalar'),
            ('blob_com_price','receipt_blob_com_price'),
            ('blob_com_used','receipt_blob_com_used')
            ]))
      if len(result) != len(transactions):
         raise ValueError('The number of transactions is wrong' + str(result))
      return result
   def enrich_contracts(blocks, contracts):
      result = list(join(
         contracts, blocks,('block_numder','number'),
         [
            'type',
            'address',
            'bytecode',
            'function_sighashes',
            'block_numder'
            ],
         [
            ('timestamp','block_timestamp'),
            ('hash','block_hash'),

            ]))
      
      if len(result) != len(contracts):
         raise ValueError('The number of contracts in wrong' + str(result))
      return result
   def ecrich_tokens(blocks, tokens):
      result = list(join(
         tokens, blocks, ('block_number','number'),
         [
            'type',
            'addres',
            'symbol',
            'name',
            'decimals',
            'total_supply',
            'block_number'
            ],
         [
            ('timestamp','block_timestamp'),
            ('hash','block_hash'),
            ]))
      if len(result) != len(tokens):
         raise ValueError('The number of tokens in wrong' + str(result))
      return result
   
            
                         
                        
    
