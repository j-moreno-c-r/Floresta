from bitcoin_rpc import BitcoindDirectTest
from test_framework import FlorestaTestBase
from typing import List


class TestBitcoind(): 
    global expected_blockhash
    global expected_chain
    expected_chain = "regtest"
    expected_height = 0
    expected_headers = 0
    expected_blockhash = "0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206"
    expected_difficulty = 1
    
    def run_bitcoin_test(self):
            btc_rpc = BitcoindDirectTest()
            try:
                # Setup and start bitcoind
                btc_rpc.setup_datadir()
                btc_rpc.start_bitcoind()
                
                response = btc_rpc.rpc_call("getblockchaininfo")
                
                # Assertions from original test
                assert response["chain"] == expected_chain
                assert response["bestblockhash"] == expected_blockhash
                assert response["difficulty"] > 0
                
            except Exception as e:
                raise
            finally:
                btc_rpc.stop_bitcoind()
                btc_rpc.cleanup()
    
    def test_bitcoind_testframework(self):
        FlorestaTestBase.bitcoind = FlorestaTestBase.add_node(self=FlorestaTestBase, extra_args=[None], variant="bitcoind")
        FlorestaTestBase.run_node(FlorestaTestBase.bitcoind)
        response = FlorestaTestBase.bitcoind.rpc.get_blockchain_info()

        FlorestaTestBase.assertEqual(response["chain"], TestBitcoind.expected_chain)
        FlorestaTestBase.assertEqual(response["bestblockhash"], TestBitcoind.expected_blockhash)
        FlorestaTestBase.assertTrue(response["difficulty"] > 0)

        FlorestaTestBase.stop()


