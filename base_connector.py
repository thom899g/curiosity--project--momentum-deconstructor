"""
Base L2 Web3 Connection Manager with Mempool WebSocket Listener
Architectural Rationale: Web3.py provides robust Ethereum interaction with proper
error handling and async support for real-time mempool monitoring.
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3, AsyncWeb3
from web3.exceptions import TransactionNotFound, BlockNotFound
from web3.middleware import geth_poa_middleware
from websockets import connect, ConnectionClosed
import requests

from dotenv import load_dotenv
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


@dataclass
class BaseTransaction:
    """Structured representation of a Base transaction"""
    hash: str
    from_address: str
    to_address: Optional[str]
    value: int
    gas: int
    gas_price: int
    input_data: str
    block_number: Optional[int]
    timestamp: Optional[datetime]
    status: str  # pending, confirmed, failed


class BaseL2Connector:
    """
    Manages connections to Base L2 with synchronous and asynchronous interfaces.
    
    Edge Case Handling:
    1. RPC node fallback mechanisms
    2. WebSocket reconnection logic
    3. Rate limiting and backoff strategies
    4. Transaction decoding with validation
    """
    
    def __init__(self, rpc_url: Optional[str] = None, ws_url: Optional[str] = None):
        """
        Initialize Base L2 connector with Web3 instances.
        
        Args:
            rpc_url: Base RPC URL (falls back to env var)
            ws_url: Base WebSocket URL (falls back to env var)
        """
        self.rpc_url = rpc_url or os.getenv('BASE_RPC_URL', 'https://mainnet.base.org')
        self.ws_url = ws_url or os.getenv('BASE_WS_URL', 'wss://mainnet.base.org/ws')
        self.chain_id = int(os.getenv('BASE_CHAIN_ID', 8453))
        
        # Initialize Web3 instances
        self.web3_sync = None
        self.web3_async = None
        self._executor = ThreadPoolExecutor(max_workers=5)
        
        # Mempool tracking
        self.mempool_listeners = []
        self._is_listening = False
        
        # Initialize connections
        self._initialize_connections()
        
    def _initialize_connections(self) -> None:
        """Initialize both sync and async Web3 connections with error handling"""
        try:
            # Synchronous connection
            self.web3_sync = Web3(Web3.HTTPProvider(self.rpc_url))
            
            # Inject POA middleware for Base compatibility
            self.web3_sync.middleware_onion.inject(geth_poa_middleware, layer=0)
            
            # Test connection
            if not self.web3_sync.is_connected():
                raise ConnectionError(f"Failed to connect to Base RPC: {self.rpc_url}")
                
            logger.info(f"Connected to Base L2 RPC (Chain ID: {self.web3_sync.eth.chain_id})")
            
        except Exception as e:
            logger.error(f"Failed to initialize Base RPC connection: {str(e)}")
            self.web3_sync = None
            
    async def initialize_async(self) -> bool:
        """
        Initialize async Web3 connection (call before using async methods).
        
        Returns:
            bool: Success status
        """
        try:
            self.web3_async = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(self.rpc_url))
            
            # Test async connection
            chain_id = await self.web3_async.eth.chain_id
            logger.info(f"Async connection established (Chain ID: {chain_id})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize async connection: {str(e)}")
            return False
    
    def get_transaction(self, tx_hash: str) -> Optional[BaseTransaction]:
        """
        Fetch transaction by hash with comprehensive error handling.
        
        Args:
            tx_hash: Transaction hash
            
        Returns:
            BaseTransaction or None if not found
        """
        if not self.web3_sync:
            logger.error("Web3 sync connection not available")
            return None
            
        try:
            # Get transaction receipt for status
            receipt = self.web3_sync.eth.get_transaction_receipt(tx_hash)
            
            # Get transaction details
            tx = self.web3_sync.eth.get_transaction(tx_hash)
            
            # Get block timestamp if available
            timestamp = None
            if receipt and receipt.blockNumber:
                block = self.web3_sync.eth.get_block(receipt.blockNumber)
                timestamp = datetime.fromtimestamp(block.timestamp)
            
            return BaseTransaction(
                hash=tx_hash,
                from_address=tx['from'],
                to_address=tx.get('to'),
                value=tx['value'],
                gas=tx['gas'],
                gas_price=tx['gasPrice'],
                input_data=tx['input'].hex(),
                block_number=receipt.blockNumber if receipt else None,
                timestamp=timestamp,
                status='confirmed' if receipt and receipt.status == 1 else 'failed'
            )
            
        except TransactionNotFound:
            logger.warning(f"Transaction not found: {tx_hash}")
            return None
        except Exception as e:
            logger.error(f"Error fetching transaction {tx_hash}: {str(e)}")
            return None
    
    def get_contract_bytecode(self, contract_address: str) -> Optional[str]:
        """
        Fetch contract bytecode from blockchain.
        
        Args:
            contract_address: