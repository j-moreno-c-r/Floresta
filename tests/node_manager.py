import os
import sys
import copy
import random
import socket
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Import your existing classes from the test_framework
from test_framework.crypto.pkcs8 import (
    create_pkcs8_private_key,
    create_pkcs8_self_signed_certificate,
)
from test_framework.daemon.bitcoin import BitcoinDaemon
from test_framework.daemon.floresta import FlorestaDaemon
from test_framework.daemon.utreexo import UtreexoDaemon
from test_framework.rpc.bitcoin import BitcoinRPC, REGTEST_RPC_SERVER as bitcoind_rpc_server
from test_framework.rpc.floresta import FlorestaRPC, REGTEST_RPC_SERVER as florestad_rpc_server
from test_framework.rpc.utreexo import UtreexoRPC, REGTEST_RPC_SERVER as utreexod_rpc_server


class Node:
    """Keep your existing Node class - it's perfect as is"""
    def __init__(self, daemon, rpc, rpc_config, variant):
        self.daemon = daemon
        self.rpc = rpc
        self.rpc_config = rpc_config
        self.variant = variant

    def start(self):
        if self.daemon.is_running:
            raise RuntimeError(f"Node '{self.variant}' is already running.")
        self.daemon.start()
        self.rpc.wait_for_connections(opened=True)

    def stop(self):
        if self.daemon.is_running:
            response = self.rpc.stop()
            self.rpc.wait_for_connections(opened=False)
            self.daemon.process.wait()
            return response
        return None

    def get_host(self) -> str:
        return self.rpc_config["host"]

    def get_ports(self) -> Dict[str, int]:
        return self.rpc_config["ports"]

    def get_port(self, port_type: str) -> int:
        if port_type not in self.rpc_config["ports"]:
            raise ValueError(
                f"Port type '{port_type}' not found in node ports: {self.rpc_config['ports']}"
            )
        return self.rpc_config["ports"][port_type]

    def send_kill_signal(self, sigcode="SIGTERM"):
        import signal
        import contextlib
        with contextlib.suppress(ProcessLookupError):
            pid = self.daemon.process.pid
            os.kill(pid, getattr(signal, sigcode, signal.SIGTERM))


class NodeManager:
    """Extracted and simplified node management - no more metaclass needed!"""
    
    def __init__(self):
        self._nodes = []
        self._temp_dir = self.get_integration_test_dir()
        self._port_counter = 0

    @staticmethod
    def get_integration_test_dir():
        """Get integration test directory from environment"""
        if os.getenv("FLORESTA_TEMP_DIR") is None:
            raise RuntimeError("FLORESTA_TEMP_DIR not set")
        return os.getenv("FLORESTA_TEMP_DIR")

    def log(self, msg: str):
        """Simple logging with timestamp"""
        now = datetime.now(timezone.utc).replace(microsecond=0)
        print(f"[NodeManager {now:%Y-%m-%d %H:%M:%S}] {msg}")

    @staticmethod
    def get_available_random_port(start: int, end: int = 65535):
        """Find an available random port"""
        while True:
            port = random.randint(start, end)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    return port

    def create_tls_key_cert(self) -> tuple[str, str]:
        """Create TLS key and certificate"""
        tls_rel_path = os.path.join(self._temp_dir, "data", "tls")
        tls_path = os.path.normpath(os.path.abspath(tls_rel_path))
        os.makedirs(tls_path, exist_ok=True)

        pk_path, private_key = create_pkcs8_private_key(tls_path)
        self.log(f"Created PKCS#8 key at {pk_path}")

        cert_path = create_pkcs8_self_signed_certificate(
            tls_path, private_key, common_name="florestad", validity_days=365
        )
        self.log(f"Created self-signed certificate at {cert_path}")
        return (pk_path, cert_path)

    # Keep all your existing helper methods
    def is_option_set(self, extra_args: list[str], option: str) -> bool:
        return any(arg.startswith(option) for arg in extra_args)

    def extract_port_from_args(self, extra_args: list[str], option: str) -> int:
        for arg in extra_args:
            if arg.startswith(f"{option}="):
                address = arg.split("=", 1)[1]
                if ":" in address:
                    return int(address.split(":")[-1])
        return None

    def should_enable_electrum_for_utreexod(self, extra_args: list[str]) -> bool:
        electrum_disabled_options = ["--noelectrum", "--disable-electrum", "--electrum=false", "--electrum=0"]
        if any(arg.startswith(opt) for arg in extra_args for opt in electrum_disabled_options):
            return False
        
        electrum_listener_options = ["--electrumlisteners", "--tlselectrumlisteners"]
        return any(arg.startswith(opt) for arg in extra_args for opt in electrum_listener_options)

    def create_data_dir_for_daemon(self, data_dir_arg: str, default_args: list[str], 
                                 extra_args: list[str], tempdir: str, testname: str):
        if not self.is_option_set(extra_args, data_dir_arg):
            datadir = os.path.join(tempdir, "data", testname)
            default_args.append(f"{data_dir_arg}={datadir}")
        else:
            datadir = next(arg.split("=",1)[1] for arg in extra_args if arg.startswith(f"{data_dir_arg}="))

        os.makedirs(datadir, exist_ok=True)

    # Keep all your setup methods exactly as they are
    def setup_florestad_daemon(self, targetdir: str, tempdir: str, testname: str, 
                             extra_args: List[str], tls: bool, port_index: int):
        daemon = FlorestaDaemon()
        daemon.create(target=targetdir)
        default_args, ports = [], {}
    
        self.create_data_dir_for_daemon("--data-dir", default_args, extra_args, tempdir, testname)
    
        if not self.is_option_set(extra_args, "--rpc-address"):
            ports["rpc"] = 18443 + port_index
            default_args.append(f"--rpc-address=127.0.0.1:{ports['rpc']}")
        else:
            ports["rpc"] = self.extract_port_from_args(extra_args, "--rpc-address")
    
        if not self.is_option_set(extra_args, "--electrum-address"):
            ports["electrum-server"] = 20001 + port_index
            default_args.append(f"--electrum-address=127.0.0.1:{ports['electrum-server']}")
        else:
            ports["electrum-server"] = self.extract_port_from_args(extra_args, "--electrum-address")
    
        if tls:
            key, cert = self.create_tls_key_cert()
            default_args.extend(["--enable-electrum-tls", f"--tls-key-path={key}", f"--tls-cert-path={cert}"])
            
            if not self.is_option_set(extra_args, "--electrum-address-tls"):
                ports["electrum-server-tls"] = 21001 + port_index
                default_args.append(f"--electrum-address-tls=127.0.0.1:{ports['electrum-server-tls']}")
            else:
                ports["electrum-server-tls"] = self.extract_port_from_args(extra_args, "--electrum-address-tls")
    
        daemon.add_daemon_settings(default_args + extra_args)
        return daemon, ports

    def setup_utreexod_daemon(self, targetdir: str, tempdir: str, testname: str,
                            extra_args: List[str], tls: bool, port_index: int):
        daemon = UtreexoDaemon()
        daemon.create(target=targetdir)
        default_args, ports = [], {}

        self.create_data_dir_for_daemon("--datadir", default_args, extra_args, tempdir, testname)

        if not self.is_option_set(extra_args, "--listen"):
            ports["p2p"] = self.get_available_random_port(18000, 20000)
            default_args.append(f"--listen=127.0.0.1:{ports['p2p']}")
        else:
            ports["p2p"] = self.extract_port_from_args(extra_args, "--listen")

        if not self.is_option_set(extra_args, "--rpclisten"):
            ports["rpc"] = self.get_available_random_port(20001, 22000)
            default_args.append(f"--rpclisten=127.0.0.1:{ports['rpc']}")
        else:
            ports["rpc"] = self.extract_port_from_args(extra_args, "--rpclisten")

        electrum_enabled = self.should_enable_electrum_for_utreexod(extra_args)
        
        if electrum_enabled and self.is_option_set(extra_args, "--electrumlisteners"):
            ports["electrum-server"] = self.extract_port_from_args(extra_args, "--electrumlisteners")

        if tls:
            key, cert = self.create_tls_key_cert()
            default_args.extend([f"--rpckey={key}", f"--rpccert={cert}"])
            
            if electrum_enabled and self.is_option_set(extra_args, "--tlselectrumlisteners"):
                ports["electrum-server-tls"] = self.extract_port_from_args(extra_args, "--tlselectrumlisteners")
        else:
            default_args.append("--notls")

        daemon.add_daemon_settings(default_args + extra_args)
        return daemon, ports

    def setup_bitcoind_daemon(self, targetdir: str, tempdir: str, testname: str,
                            extra_args: List[str], port_index: int):
        daemon = BitcoinDaemon()
        daemon.create(target=targetdir)
        default_args, ports = [], {}
    
        self.create_data_dir_for_daemon("-datadir", default_args, extra_args, tempdir, testname)
    
        if not self.is_option_set(extra_args, "-bind"):
            ports["p2p"] = 18445 + port_index
            default_args.append(f"-bind=127.0.0.1:{ports['p2p']}")
        else:
            ports["p2p"] = self.extract_port_from_args(extra_args, "-bind")
    
        if not self.is_option_set(extra_args, "-rpcbind"):
            ports["rpc"] = 20443 + port_index
            default_args.extend(["-rpcallowip=127.0.0.1", f"-rpcbind=127.0.0.1:{ports['rpc']}"])
        else:
            ports["rpc"] = self.extract_port_from_args(extra_args, "-rpcbind")
    
        daemon.add_daemon_settings(default_args + extra_args)
        return daemon, ports

    def create_node(self, extra_args: List[str] = None, variant: str = "florestad", tls: bool = False, testname: str = "pytest") -> Node:
        """Create a node (but don't start it yet)"""
        if extra_args is None:
            extra_args = []
            
        port_index = self._port_counter
        self._port_counter += 1
        
        tempdir = self._temp_dir
        targetdir = os.path.join(tempdir, "binaries")
    
        if variant == "florestad":
            daemon, ports = self.setup_florestad_daemon(targetdir, tempdir, testname, extra_args, tls, port_index)
            rpcserver = copy.deepcopy(florestad_rpc_server)
        elif variant == "utreexod":
            daemon, ports = self.setup_utreexod_daemon(targetdir, tempdir, testname, extra_args, tls, port_index)
            rpcserver = copy.deepcopy(utreexod_rpc_server)
        elif variant == "bitcoind":
            daemon, ports = self.setup_bitcoind_daemon(targetdir, tempdir, testname, extra_args, port_index)
            rpcserver = copy.deepcopy(bitcoind_rpc_server)
        else:
            raise ValueError(f"Unsupported variant: {variant}")
    
        rpcserver["ports"] = ports
        node = Node(daemon, None, rpcserver, variant)
        self._nodes.append(node)
        return node

    def start_node(self, node: Node, timeout: int = 180):
        """Start a node and setup RPC"""
        node.daemon.start()
        
        if node.variant == "florestad":
            node.rpc = FlorestaRPC(node.daemon.process, node.rpc_config)
        elif node.variant == "utreexod":
            node.rpc = UtreexoRPC(node.daemon.process, node.rpc_config)
        elif node.variant == "bitcoind":
            node.rpc = BitcoinRPC(node.daemon.process, node.rpc_config)

        node.rpc.wait_for_connections(opened=True, timeout=timeout)
        self.log(f"Node '{node.variant}' started on ports: {node.rpc_config['ports']}")

    def stop_all_nodes(self):
        """Stop all managed nodes"""
        for node in self._nodes:
            try:
                node.stop()
            except Exception as e:
                self.log(f"Error stopping node {node.variant}: {e}")
                # Try force kill
                try:
                    node.send_kill_signal("SIGKILL")
                except Exception:
                    pass
        self._nodes.clear()

    def cleanup(self):
        """Full cleanup - stop nodes and reset state"""
        self.stop_all_nodes()
        self._port_counter = 0