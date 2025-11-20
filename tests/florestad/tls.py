"""
florestad/tls-test.py (pytest version)

This functional test tests the proper creation of a TLS port on florestad.
"""

import pytest
from test_framework.electrum.client import ElectrumClient

@pytest.mark.integration
def test_tls_initialization(florestad_with_tls):
    # Cria conexão com Electrum client no TLS port
    host = florestad_with_tls.get_host()
    tls_port = florestad_with_tls.get_port("electrum-server-tls")
    electrum = ElectrumClient(host, tls_port, tls=True)
    res = electrum.ping()
    # Verifica resposta do ping
    assert res["result"] is None
    assert res["id"] == 0
    assert res["jsonrpc"] == "2.0"
