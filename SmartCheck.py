import Blockchain
import cripta
import wallet
import P2WPKH



class SmartCheck:
    def __init__(self):
        self.parties = {} # Сторонаы обмена
        self.assts = {}  # Активы,которые каждая сторона предлогает
        self.status = "Pending" # Статус сделки

    def add_party(self,party_id,asset):      # Добавления в сделку с активамвом
         #if party_id not self.paties:
          #  return(f"PARTY {party_id} is already part of the exchange.")
           self.parties[party_id] = False # Изначально актив не подтвержден
           self.assets[party_id] = asset
           #return(f"Party {party_id}added with asset: {asset}") 
    def confirm_asset (self,party_id):
        if party_id not in self.parties:    # Подтверждение активов от стороны 
            #return(f"Party {party_id} is not part of the exchange.")
            self.parties[party_id] = True
           #return(f"Party {party_id} has confirmed the asset.")
    def check_exchange_status(self):       # Проверяет, выполнены ли все условия обмена    
         if all(self.parties.values()):
             self.status = "Completed"
             #return(f"Exchange completed successfully!")
            #return(f"Exchang is still panding. Awaiting confirmation.")
    def det_status(self):
        return(f"Exchang status: {self.status}.")
    # Пример использования
    # = SmartCheck()
    # Добавлям стороны в сделку
    #print(smart_check.add_party(""))
    #print(smart_check.add_party(""))
    # Подтверждем активы
    #print(smart_check.confirm_asset("")) # Подтверждает
    #print(smart_check_exchange_status()) # Проверка статуса
    # Получение финального статуса
    #print(smart_check.get_status())
