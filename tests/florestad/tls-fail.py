"""
florestad/tls-fail-test.py (pytest version)

This functional test checks the failure on connect to florestad's TLS port.
"""

import pytest
import errno
from test_framework.electrum.client import ElectrumClient


@pytest.mark.integration
def test_tls_fail_initialization(florestad_node):
    # Tenta conectar ao Electrum TLS port sem TLS habilitado
    host = florestad_node.get_host()
    tls_port = florestad_node.get_port("electrum-server") + 1
    with pytest.raises(ConnectionRefusedError) as exc:
        ElectrumClient(host, tls_port)
    # Verifica se a exceção é de conexão recusada
    assert exc.value.errno == errno.ECONNREFUSED
