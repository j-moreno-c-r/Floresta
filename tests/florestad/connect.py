"""
Test the --connect cli option of florestad

This test will start a utreexod, then start a florestad node with
the --connect option pointing to the utreexod node. Then check if
the utreexod node is connected to the florestad node.
"""
import pytest
import time

@pytest.mark.timeout(60)
def test_connect_cli_option(connect_nodes):
    """Test florestad can connect to utreexod using --connect option"""
    utreexod_node, florestad_node = connect_nodes
    waited = 0
    interval = 0.5
    timeout = 10
    peer_info = None
    while waited < timeout:
        peer_info = utreexod_node.rpc.get_peerinfo()
        if len(peer_info) == 1:
            break
        time.sleep(interval)
        waited += interval
    if not peer_info or len(peer_info) != 1:
        pytest.fail("florestad did not connect to utreexod within the timeout")
    assert len(peer_info) == 1
