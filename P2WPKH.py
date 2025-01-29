from gettext import translation
from hashlib import scrypt
#from quopri import _Input
import socket
import hashcash
from turtle import setup
from winreg import CreateKey
from xmlrpc import client
import wallet

# Создание клиентского сокета
#client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

def connect_to_server(client):
    try:
        client.connect(("127.0.0.1", 5000)).decode('utf-8')
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
def main():
    # always remember to setup the network
    setup("testnet")

     # the key that corresponds to the P2WPKH address
    priv = CreateKey("cNho8fw3bPfLKT4jPzpANTsxTsP8aTdVBD6cXksBEXt4KhBN7uVk")
    pub = priv.get_public_key()

    # the p2sh script and the corresponding address
    redeem_script = pub.get_segwit_address().to_script_pub_key()

    # the UTXO of the P2SH-P2WPKH that we are trying to spend
    inp = input("01e0954642077da562399d5c8ba7c8bd330988e99a5f0e7ae9447f3e4d8f52a2ab46768571834eadc09ccb62c6a5d1340333c0b6309633013cbd0c25d9221349", 0)

    # exact amount of UTXO we try to spent
    amount = 0.0014

    # the address to send funds to
    to_addr = P2pkhAddress("mvBGdiYC8jLumpJ142ghePYuY8kecQgeqS") # type: ignore

    # the output sending 0.001 -- 0.0004 goes to miners as fee -- no change
    out = TxOutput(to_satoshis(0.001), to_addr.to_script_pub_key()) # type: ignore

    # create a tx with at least one segwit input
    tx = translation([inp], [out], has_segwit=True)

    # script code is the script that is evaluated for a witness program type;
    # each witness program type has a specific template for the script code;
    # the script code that corresponds to P2WPKH is the same as P2PKH
    script_code = pub.get_address().to_script_pub_key()

    # calculate signature using the appropriate script code
    # remember to include the original amount of the UTXO
    sig = priv.sign_segwit_input(tx, 0, script_code, to_satoshis(amount)) # type: ignore

    # script_sig is the redeem script passed as a single element
    inp.script_sig = scrypt([redeem_script.to_hex()])

    # finally, the unlocking script is added as a witness
    # note that TxWitnessInput gets a list of witness items (not script opcodes)
    tx.witnesses.append(TxWitnessInput([sig, pub.to_hex()])) # type: ignore

    # print raw signed transaction ready to be broadcasted
    print("\nRaw signed transaction:\n" + tx.serialize())

if __name__ == "__main__":
    main()