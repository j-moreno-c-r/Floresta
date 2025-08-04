import json
from bitcoin_rpc import BitcoindDirectTest


def test_run_bitcoin_test():
        btc_rpc = BitcoindDirectTest()
        try:
            # Setup and start bitcoind
            btc_rpc.setup_datadir()
            btc_rpc.start_bitcoind()
            
            response = btc_rpc.rpc_call("getblockchaininfo")
            
            # Assertions from original test
            assert response["chain"] == btc_rpc.expected_chain
            assert response["bestblockhash"] == btc_rpc.expected_blockhash
            assert response["difficulty"] > 0
              
        except Exception as e:
            raise
        finally:
            btc_rpc.stop_bitcoind()
            btc_rpc.cleanup()
