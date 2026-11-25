
import hashlib
import json
from datetime import datetime
from pathlib import Path


class Block:

    def __init__(self, index, timestamp, data, previous_hash):

        self.index = index
        self.timestamp = timestamp
        self.data = data
        self.previous_hash = previous_hash

        self.hash = self.calculate_hash()

    # Generate SHA-256 hash
    def calculate_hash(self):

        block_data = {
            "index": self.index,
            "timestamp": self.timestamp,
            "data": self.data,
            "previous_hash": self.previous_hash
        }

        encoded_data = json.dumps(
            block_data,
            sort_keys=True
        ).encode()

        return hashlib.sha256(encoded_data).hexdigest()

    # Convert block into dictionary for JSON storage
    def to_dict(self):

        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "data": self.data,
            "previous_hash": self.previous_hash,
            "hash": self.hash
        }

    # Reconstruct block from saved JSON
    @classmethod
    def from_dict(cls, block_data):

        block = cls(
            block_data["index"],
            block_data["timestamp"],
            block_data["data"],
            block_data["previous_hash"]
        )

        # Restore the originally saved hash
        block.hash = block_data["hash"]

        return block


class Blockchain:

    def __init__(self, storage_file="blockchain/chain.json"):

        self.chain = []
        self.storage_file = Path(storage_file)

        self.storage_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        if self.storage_file.exists():
            self.load_chain()
        else:
            self.create_genesis_block()
            self.save_chain()

    # Create first block
    def create_genesis_block(self):

        genesis_block = Block(
            0,
            datetime.now().isoformat(),
            {"message": "Genesis Block"},
            "0"
        )

        self.chain.append(genesis_block)

    # Get latest block
    def get_latest_block(self):

        return self.chain[-1]

    # Save blockchain to JSON
    def save_chain(self):

        blockchain_data = [
            block.to_dict()
            for block in self.chain
        ]

        temporary_file = self.storage_file.with_suffix(".tmp")

        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(
                blockchain_data,
                file,
                indent=4
            )

        temporary_file.replace(self.storage_file)

    # Load blockchain from JSON
    def load_chain(self):

        try:
            with open(
                self.storage_file,
                "r",
                encoding="utf-8"
            ) as file:

                saved_blocks = json.load(file)

            if not isinstance(saved_blocks, list) or not saved_blocks:
                raise ValueError("Blockchain storage is empty or invalid")

            self.chain = [
                Block.from_dict(block_data)
                for block_data in saved_blocks
            ]

            if not self.validate_chain():
                raise ValueError(
                    "Blockchain integrity check failed! "
                    "Stored data may have been modified."
                )

            print("Existing blockchain loaded successfully!")
            print("Total blocks:", len(self.chain))

        except (OSError, ValueError, KeyError, TypeError) as error:
            raise RuntimeError(
                f"Could not safely load blockchain: {error}"
            ) from error

    # Add new block
    def add_block(self, data):

        previous_block = self.get_latest_block()

        new_block = Block(
            len(self.chain),
            datetime.now().isoformat(),
            data,
            previous_block.hash
        )

        self.chain.append(new_block)

        try:
            self.save_chain()
        except OSError:
            self.chain.pop()
            raise

        print("New block added successfully!")
        print("Block Index:", new_block.index)
        print("Block Hash:", new_block.hash)

    # Verify blockchain
    def validate_chain(self):

        if not self.chain:
            return False

        genesis = self.chain[0]

        if genesis.index != 0:
            return False

        if genesis.previous_hash != "0":
            return False

        if genesis.hash != genesis.calculate_hash():
            return False

        for i in range(1, len(self.chain)):

            current_block = self.chain[i]
            previous_block = self.chain[i - 1]

            if current_block.index != i:
                return False

            if current_block.hash != current_block.calculate_hash():
                return False

            if current_block.previous_hash != previous_block.hash:
                return False

        return True

    # Display blockchain
    def display_chain(self):

        for block in self.chain:

            print("\n--------------------------")
            print("Block Index:", block.index)
            print("Timestamp:", block.timestamp)
            print("Data:", block.data)
            print("Previous Hash:", block.previous_hash)
            print("Current Hash:", block.hash)
            print("--------------------------")


# Standalone test
if __name__ == "__main__":

    my_blockchain = Blockchain()

    print("\nBlockchain loaded successfully!")

    print("Total blocks:", len(my_blockchain.chain))

    my_blockchain.display_chain()

    print(
        "\nBlockchain Valid:",
        my_blockchain.validate_chain()
    )
