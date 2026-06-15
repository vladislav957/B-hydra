"""
SmartCheck.py — эскроу-обмен B-hydra.

Несколько сторон вносят активы; обмен считается завершённым только когда
все участники подтвердили свои активы.
"""


class SmartCheck:
    """Эскроу: обмен активами между несколькими сторонами."""

    def __init__(self):
        self.parties = {}   # party_id -> подтверждён ли актив (bool)
        self.assets = {}    # party_id -> предлагаемый актив
        self.status = "Pending"

    def add_party(self, party_id, asset):
        if party_id in self.parties:
            return f"Party {party_id} is already part of the exchange."
        self.parties[party_id] = False   # актив пока не подтверждён
        self.assets[party_id] = asset
        return f"Party {party_id} added with asset: {asset}"

    def confirm_asset(self, party_id):
        if party_id not in self.parties:
            return f"Party {party_id} is not part of the exchange."
        self.parties[party_id] = True
        self.check_exchange_status()
        return f"Party {party_id} has confirmed the asset."

    def check_exchange_status(self):
        if self.parties and all(self.parties.values()):
            self.status = "Completed"
            return "Exchange completed successfully!"
        return "Exchange is still pending. Awaiting confirmation."

    def get_status(self):
        return f"Exchange status: {self.status}."


if __name__ == "__main__":
    check = SmartCheck()
    print(check.add_party("Alice", "10 BHY"))
    print(check.add_party("Bob", "Token-X"))
    print(check.confirm_asset("Alice"))
    print(check.get_status())
    print(check.confirm_asset("Bob"))
    print(check.get_status())
