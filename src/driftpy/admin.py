from typing import Type
import json
from importlib import resources
import asyncio

from solana.publickey import PublicKey
from solana.transaction import TransactionSignature
from solana.keypair import Keypair
from solana.system_program import SYS_PROGRAM_ID
from solana.sysvar import SYSVAR_RENT_PUBKEY
from spl.token.constants import TOKEN_PROGRAM_ID
from anchorpy import Program, Provider, Idl, Context

from driftpy.clearing_house import (
    ClearingHouse,
    T,
    get_clearing_house_state_account_public_key_and_nonce,
)


class Admin(ClearingHouse):
    @classmethod
    async def from_(cls: Type[T], program_id: PublicKey, provider: Provider) -> T:
        with resources.open_text("driftpy.idl", "clearing_house.json") as f:
            idl_raw = json.load(f)
        idl = Idl.from_json(idl_raw)
        program = Program(idl, program_id, provider)
        return await cls.create(program)

    @classmethod
    async def initialize(
        cls: Type[T],
        program: Program,
        usdc_mint: PublicKey,
        admin_controls_prices: bool,
    ) -> tuple[TransactionSignature, TransactionSignature]:
        state_account_rpc_response = await program.provider.connection.get_account_info(
            cls._get_state_pubkey(program)
        )
        if state_account_rpc_response["result"]["value"] is not None:
            raise RuntimeError("Clearing house already initialized")

        (
            collateral_vault_public_key,
            collateral_vault_nonce,
        ) = PublicKey.find_program_address([b"collateral_vault"], program.program_id)

        collateral_vault_authority, _, = PublicKey.find_program_address(
            [bytes(collateral_vault_public_key)], program.program_id
        )

        (
            insurance_vault_public_key,
            insurance_vault_nonce,
        ) = PublicKey.find_program_address([b"insurance_vault"], program.program_id)

        insurance_vault_authority, _ = PublicKey.find_program_address(
            [bytes(insurance_vault_public_key)], program.program_id
        )

        markets = Keypair()
        deposit_history = Keypair()
        funding_rate_history = Keypair()
        funding_payment_history = Keypair()
        trade_history = Keypair()
        liquidation_history = Keypair()
        curve_history = Keypair()

        (
            clearing_house_state_public_key,
            clearing_house_nonce,
        ) = get_clearing_house_state_account_public_key_and_nonce(program.program_id)
        initialize_tx_sig = await program.rpc["initialize"](
            clearing_house_nonce,
            collateral_vault_nonce,
            insurance_vault_nonce,
            admin_controls_prices,
            ctx=Context(
                accounts={
                    "admin": program.provider.wallet.public_key,
                    "state": clearing_house_state_public_key,
                    "collateral_mint": usdc_mint,
                    "collateral_vault": collateral_vault_public_key,
                    "collateral_vault_authority": collateral_vault_authority,
                    "insurance_vault": insurance_vault_public_key,
                    "insurance_vault_authority": insurance_vault_authority,
                    "markets": markets.public_key,
                    "rent": SYSVAR_RENT_PUBKEY,
                    "system_program": SYS_PROGRAM_ID,
                    "token_program": TOKEN_PROGRAM_ID,
                },
                pre_instructions=[
                    await program.account["Markets"].create_instruction(markets),
                ],
                signers=[markets],
            ),
        )

        initialize_history_tx_sig = await program.rpc["initialize_history"](
            ctx=Context(
                accounts={
                    "admin": program.provider.wallet.public_key,
                    "state": clearing_house_state_public_key,
                    "deposit_history": deposit_history.public_key,
                    "funding_rate_history": funding_rate_history.public_key,
                    "funding_payment_history": funding_payment_history.public_key,
                    "trade_history": trade_history.public_key,
                    "liquidation_history": liquidation_history.public_key,
                    "curve_history": curve_history.public_key,
                    "rent": SYSVAR_RENT_PUBKEY,
                    "system_program": SYS_PROGRAM_ID,
                },
                pre_instructions=await asyncio.gather(
                    program.account["funding_rate_history"].create_instruction(
                        funding_rate_history
                    ),
                    program.account["funding_payment_history"].create_instruction(
                        funding_payment_history
                    ),
                    program.account["trade_history"].create_instruction(trade_history),
                    program.account["liquidation_history"].create_instruction(
                        liquidation_history
                    ),
                    program.account["deposit_history"].create_instruction(
                        deposit_history
                    ),
                    program.account["curve_history"].create_instruction(curve_history),
                ),
                signers=[
                    deposit_history,
                    funding_payment_history,
                    trade_history,
                    liquidation_history,
                    funding_rate_history,
                    curve_history,
                ],
            )
        )

        return initialize_tx_sig, initialize_history_tx_sig