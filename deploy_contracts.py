"""
deploy_contracts.py — A.N.Sx Vault | One-Time Contract Deployment to Polygon Amoy
==================================================================================

Run this ONCE to deploy SessionBroker.sol and ANSXRegistry.sol to the Amoy testnet.
The deployed contract addresses are automatically written to settings.py.

Prerequisites:
  1. pip install web3 py-solc-x
  2. Fund your deployer wallet with test MATIC:
       https://faucet.polygon.technology/
  3. Set your deployer wallet private key (with 0x prefix) below OR as env var:
       export ANSX_DEPLOYER_KEY=0x<your_private_key>

Usage:
  python3 deploy_contracts.py
"""

import os
import sys
import json

AMOY_RPC = "https://rpc-amoy.polygon.technology"
CHAIN_ID  = 80002


def get_deployer_key() -> str:
    key = os.environ.get("ANSX_DEPLOYER_KEY", "")
    if not key:
        print("\n  Enter your deployer private key (with 0x prefix).")
        print("  Get free MATIC at: https://faucet.polygon.technology/")
        key = input("  Private key: ").strip()
    if not key.startswith("0x"):
        key = "0x" + key
    return key


def compile_contract(sol_path: str, contract_name: str):
    from solcx import compile_standard, install_solc
    install_solc("0.8.0")
    with open(sol_path) as f:
        src = f.read()
    result = compile_standard({
        "language": "Solidity",
        "sources": {os.path.basename(sol_path): {"content": src}},
        "settings": {
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode"]}}
        },
    }, solc_version="0.8.0")
    name = os.path.basename(sol_path)
    iface = result["contracts"][name][contract_name]
    return iface["abi"], iface["evm"]["bytecode"]["object"]


def deploy(w3, account, abi, bytecode, label: str) -> str:
    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    nonce = w3.eth.get_transaction_count(account.address)
    tx = Contract.constructor().build_transaction({
        "chainId":  CHAIN_ID,
        "from":     account.address,
        "nonce":    nonce,
        "gas":      3_000_000,
        "gasPrice": w3.eth.gas_price,
    })
    signed  = w3.eth.account.sign_transaction(tx, account.key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  [{label}] Tx sent: https://amoy.polygonscan.com/tx/{tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    addr    = receipt.contractAddress
    print(f"  [{label}] Deployed at: {addr}  (block {receipt.blockNumber})")
    return addr


def update_settings(broker_addr: str, registry_addr: str):
    settings_path = os.path.join(os.path.dirname(__file__), "settings.py")
    lines = []
    if os.path.exists(settings_path):
        with open(settings_path) as f:
            lines = f.readlines()

    # Remove old address lines
    lines = [l for l in lines if not l.startswith(("SESSION_BROKER_ADDRESS", "ANSX_REGISTRY_ADDRESS"))]
    lines.append(f'SESSION_BROKER_ADDRESS = "{broker_addr}"\n')
    lines.append(f'ANSX_REGISTRY_ADDRESS  = "{registry_addr}"\n')

    with open(settings_path, "w") as f:
        f.writelines(lines)

    print("\n  settings.py updated:")
    print(f"    SESSION_BROKER_ADDRESS = \"{broker_addr}\"")
    print(f"    ANSX_REGISTRY_ADDRESS  = \"{registry_addr}\"")


def main():
    print("\n" + "=" * 62)
    print("  A.N.Sx Vault — Contract Deployment to Polygon Amoy")
    print("=" * 62)

    from web3 import Web3
    private_key = get_deployer_key()
    account = Web3().eth.account.from_key(private_key)

    w3 = Web3(Web3.HTTPProvider(AMOY_RPC))
    if not w3.is_connected():
        print("\n  ERROR: Cannot connect to Amoy RPC. Check internet connection.")
        sys.exit(1)

    balance = w3.from_wei(w3.eth.get_balance(account.address), "ether")
    print(f"\n  Deployer: {account.address}")
    print(f"  Balance:  {balance:.4f} MATIC")

    if balance < 0.01:
        print("\n  ERROR: Insufficient MATIC. Get free MATIC from:")
        print(f"    https://faucet.polygon.technology/?network=amoy&address={account.address}")
        sys.exit(1)

    vault_root = os.path.dirname(os.path.abspath(__file__))

    print("\n  Compiling SessionBroker.sol...")
    broker_abi, broker_bytecode = compile_contract(
        os.path.join(vault_root, "SessionBroker.sol"), "SessionBroker"
    )

    print("  Compiling ANSXRegistry.sol...")
    registry_abi, registry_bytecode = compile_contract(
        os.path.join(vault_root, "ANSXRegistry.sol"), "ANSXRegistry"
    )

    print("\n  Deploying SessionBroker...")
    broker_addr = deploy(w3, account, broker_abi, broker_bytecode, "SessionBroker")

    print("\n  Deploying ANSXRegistry...")
    registry_addr = deploy(w3, account, registry_abi, registry_bytecode, "ANSXRegistry")

    update_settings(broker_addr, registry_addr)

    # Save ABIs for future reference
    abi_dir = os.path.join(vault_root, "abis")
    os.makedirs(abi_dir, exist_ok=True)
    with open(os.path.join(abi_dir, "SessionBroker.json"), "w") as f:
        json.dump(broker_abi, f, indent=2)
    with open(os.path.join(abi_dir, "ANSXRegistry.json"), "w") as f:
        json.dump(registry_abi, f, indent=2)

    print(f"\n  ABIs saved to: {abi_dir}/")
    print("\n  ✅ DEPLOYMENT COMPLETE. Launch main.py to use blockchain TCP.")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    main()
