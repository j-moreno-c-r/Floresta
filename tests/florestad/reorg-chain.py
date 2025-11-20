"""
Chain reorg test (pytest version) - FIXED

This test will spawn a florestad and a utreexod, we will use utreexod to mine some blocks.
Then we will invalidate one of those blocks, and mine an alternative chain. This should
make florestad switch to the new chain. We then compare the two node's main chain and
accumulator to make sure they are the same.
"""
import pytest
import re
import time


def wait_for_peer_connection(florestad, timeout=30):
    """Wait for florestad to establish peer connection with utreexod."""
    waited = 0
    interval = 0.5
    
    while waited < timeout:
        peer_info = florestad.rpc.get_peerinfo()
        if peer_info and len(peer_info) > 0:
            user_agent = peer_info[0].get("user_agent", "")
            if re.match(r"/btcwire:\d+\.\d+\.\d+/utreexod:\d+\.\d+\.\d+/", user_agent):
                return peer_info
        time.sleep(interval)
        waited += interval
    
    raise TimeoutError(f"Failed to establish peer connection after {timeout}s")


def wait_for_sync(florestad, utreexod, expected_height, timeout=60):
    """Wait for florestad to sync to expected block height."""
    waited = 0
    interval = 1
    
    while waited < timeout:
        floresta_info = florestad.rpc.get_blockchain_info()
        utreexo_info = utreexod.rpc.get_blockchain_info()
        
        floresta_height = floresta_info.get("height", 0)
        utreexo_height = utreexo_info.get("blocks", 0)
        floresta_block = floresta_info.get("best_block", "")
        utreexo_block = utreexo_info.get("bestblockhash", "")
        
        # Check if both nodes are at expected height and have same best block
        if (floresta_height >= expected_height and 
            utreexo_height >= expected_height and
            floresta_block == utreexo_block):
            return floresta_info, utreexo_info
        
        # Debug output every 10 seconds
        if waited % 10 == 0:
            print(f"[{waited}s] Waiting for sync... "
                  f"F: h={floresta_height} block={floresta_block[:16] if floresta_block else 'None'}... "
                  f"U: h={utreexo_height} block={utreexo_block[:16] if utreexo_block else 'None'}...")
        
        time.sleep(interval)
        waited += interval
    
    raise TimeoutError(
        f"Nodes failed to sync after {timeout}s. "
        f"Floresta: height={floresta_height}, block={floresta_block}, "
        f"Utreexo: height={utreexo_height}, block={utreexo_block}, "
        f"expected_height={expected_height}"
    )


@pytest.mark.timeout(180)
@pytest.mark.integration
def test_chain_reorg(reorg_chain_nodes):
    """Test chain reorganization between florestad and utreexod nodes."""
    florestad, utreexod = reorg_chain_nodes

    # Connect florestad to utreexod BEFORE mining
    host = florestad.get_host()
    port = utreexod.get_port("p2p")
    print(f"Connecting florestad to utreexod at {host}:{port}")
    florestad.rpc.addnode(f"{host}:{port}", command="onetry", v2transport=False)
    
    # Wait for peer connection to establish
    print("Waiting for peer connection...")
    peer_info = wait_for_peer_connection(florestad, timeout=30)
    assert peer_info and len(peer_info) > 0, "No peers connected"
    assert re.match(
        r"/btcwire:\d+\.\d+\.\d+/utreexod:\d+\.\d+\.\d+/",
        peer_info[0]["user_agent"]
    ), f"Unexpected peer user agent: {peer_info[0]['user_agent']}"
    print(f"Peer connection established: {peer_info[0]['user_agent']}")

    # Now mine blocks with utreexod after connection is established
    print("Mining initial 10 blocks...")
    utreexod.rpc.generate(10)
    
    # Give nodes time to propagate blocks
    time.sleep(3)
    
    # Wait for the nodes to sync to height 10
    print("Waiting for initial sync to height 10...")
    floresta_chain, utreexo_chain = wait_for_sync(florestad, utreexod, expected_height=10, timeout=60)
    
    print(f"Initial sync complete - Height: {floresta_chain['height']}, Block: {floresta_chain['best_block'][:16]}...")
    
    assert floresta_chain["best_block"] == utreexo_chain["bestblockhash"], (
        f"Chain mismatch before reorg: "
        f"floresta={floresta_chain['best_block']}, "
        f"utreexo={utreexo_chain['bestblockhash']}"
    )
    assert floresta_chain["height"] == utreexo_chain["blocks"], (
        f"Height mismatch before reorg: "
        f"floresta={floresta_chain['height']}, "
        f"utreexo={utreexo_chain['blocks']}"
    )

    # Invalidate block at height 5 and mine alternative chain
    hash_to_invalidate = utreexod.rpc.get_blockhash(5)
    print(f"Invalidating block at height 5: {hash_to_invalidate}")
    utreexod.rpc.invalidate_block(hash_to_invalidate)
    
    # Check utreexod state after invalidation
    utreexo_info_after_invalidate = utreexod.rpc.get_blockchain_info()
    print(f"After invalidation - Utreexo height: {utreexo_info_after_invalidate.get('blocks', 0)}")
    
    print("Mining 10 new blocks on alternative chain...")
    utreexod.rpc.generate(10)
    
    # Give extra time for reorg propagation
    time.sleep(5)
    
    # After reorg, the chain should be at height 14 (genesis + 4 valid + 10 new)
    # Wait for nodes to sync after reorg
    print("Waiting for sync after reorg to height 14...")
    floresta_chain, utreexo_chain = wait_for_sync(florestad, utreexod, expected_height=14, timeout=90)

    print(f"Post-reorg sync complete - Height: {floresta_chain['height']}, Block: {floresta_chain['best_block'][:16]}...")

    assert floresta_chain["best_block"] == utreexo_chain["bestblockhash"], (
        f"Chain mismatch after reorg: "
        f"floresta={floresta_chain['best_block']}, "
        f"utreexo={utreexo_chain['bestblockhash']}"
    )
    assert floresta_chain["height"] == utreexo_chain["blocks"], (
        f"Height mismatch after reorg: "
        f"floresta={floresta_chain['height']}, "
        f"utreexo={utreexo_chain['blocks']}"
    )

    # Wait for accumulator roots to be available and match
    print("Checking accumulator roots...")
    waited = 0
    interval = 1
    timeout = 30
    
    while waited < timeout:
        floresta_roots = florestad.rpc.get_roots()
        utreexo_roots = utreexod.rpc.get_utreexo_roots(utreexo_chain["bestblockhash"])
        
        print(f"[{waited}s] floresta roots: {floresta_roots}")
        print(f"[{waited}s] utreexo roots: {utreexo_roots.get('roots') if utreexo_roots else None}")
        
        # Check if both have non-empty roots
        if floresta_roots and utreexo_roots and utreexo_roots.get("roots"):
            # Compare roots
            if floresta_roots == utreexo_roots["roots"]:
                print("Accumulator roots match!")
                break
            else:
                print(f"Roots don't match yet, continuing to wait...")
        
        time.sleep(interval)
        waited += interval
    
    # Final assertions
    assert floresta_roots, f"floresta_roots is empty after {timeout}s: {floresta_roots}"
    assert utreexo_roots and utreexo_roots.get("roots"), f"utreexo_roots is empty after {timeout}s: {utreexo_roots}"
    assert floresta_roots == utreexo_roots["roots"], (
        f"Accumulator roots mismatch: "
        f"floresta={floresta_roots}, "
        f"utreexo={utreexo_roots['roots']}"
    )

    print("Test completed successfully!")