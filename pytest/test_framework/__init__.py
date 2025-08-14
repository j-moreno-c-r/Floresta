"""
tests/test_framework/pytest_framework.py

Pytest-compatible version of the Floresta test framework.
Adapted from the original __init__.py to work with pytest while preserving
all the RPC preparation and node management functionality.
"""

import os
import re
import sys
import copy
import time
import random
import socket
import signal
import contextlib
import pytest
from datetime import datetime, timezone
from typing import Any, Dict, List, Pattern, TextIO

from test_framework.crypto.pkcs8 import (
    create_pkcs8_private_key,
    create_pkcs8_self_signed_certificate,
)
from test_framework.daemon.bitcoin import BitcoinDaemon
from test_framework.daemon.floresta import FlorestaDaemon
from test_framework.daemon.utreexo import UtreexoDaemon
from test_framework.rpc.bitcoin import BitcoinRPC
from test_framework.rpc.floresta import FlorestaRPC
from test_framework.rpc.utreexo import UtreexoRPC
from test_framework.rpc.bitcoin import REGTEST_RPC_SERVER as bitcoind_rpc_server
from test_framework.rpc.floresta import REGTEST_RPC_SERVER as florestad_rpc_server
from test_framework.rpc.utreexo import REGTEST_RPC_SERVER as utreexod_rpc_server


class Node:
    """
    A node object to be used in the test framework.
    It contains the `daemon`, `rpc` and `rpc_config` objects.
    """

    def __init__(self, daemon, rpc, rpc_config, variant):
        self.daemon = daemon
        self.rpc = rpc
        self.rpc_config = rpc_config
        self.variant = variant

    def start(self):
        """Start the node."""
        if self.daemon.is_running:
            raise RuntimeError(f"Node '{self.variant}' is already running.")
        self.daemon.start()
        self.rpc.wait_for_connections(opened=True)

    def stop(self):
        """Stop the node."""
        if self.daemon.is_running:
            response = self.rpc.stop()
            self.rpc.wait_for_connections(opened=False)
            self.daemon.process.wait()
            return response
        return None

    def get_host(self) -> str:
        """Get the host address of the node."""
        return self.rpc_config["host"]

    def get_ports(self) -> int:
        """Get all ports of the node."""
        return self.rpc_config["ports"]

    def get_port(self, port_type: str) -> int:
        """Get the port of the node based on the port type."""
        if port_type not in self.rpc_config["ports"]:
            raise ValueError(
                f"Port type '{port_type}' not found in node ports: {self.rpc_config['ports']}"
            )
        return self.rpc_config["ports"][port_type]

    def send_kill_signal(self, sigcode="SIGTERM"):
        """Send a signal to kill the daemon process."""
        with contextlib.suppress(ProcessLookupError):
            pid = self.daemon.process.pid
            os.kill(pid, getattr(signal, sigcode, signal.SIGTERM))


class FlorestaTestBase:
    """
    Base class for pytest-compatible Floresta tests.
    
    This class provides all the node management and RPC functionality
    without the metaclass constraints, making it compatible with pytest.
    """

    def __init__(self):
        """Initialize the test framework."""
        self._nodes = []

    def log(self, msg: str):
        """Log a message with the class caller"""
        now = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .strftime("%Y-%m-%d %H:%M:%S")
        )
        print(f"[{self.__class__.__name__} {now}] {msg}")

    def setup_method(self):
        """Pytest setup method - called before each test method."""
        self._nodes = []

    def teardown_method(self):
        """Pytest teardown method - called after each test method."""
        self.stop_all_nodes()

    def stop_all_nodes(self):
        """Stop all nodes and clean up."""
        processes = []
        for node in self._nodes:
            processes.append(str(node.daemon.process.pid))
            try:
                if getattr(node, "rpc", None):
                    node.rpc.stop()
                    node.rpc.wait_for_connections(opened=False)
                else:
                    try:
                        node.send_kill_signal("SIGTERM")
                    except Exception:
                        node.send_kill_signal("SIGKILL")
            except Exception as e:
                self.log(f"Error stopping node {node.variant}: {e}")

    @staticmethod
    def get_integration_test_dir():
        """Get path for integration test directory."""
        if os.getenv("FLORESTA_TEMP_DIR") is None:
            raise RuntimeError(
                "FLORESTA_TEMP_DIR not set. "
                + " Please set it to the path of the integration test directory."
            )
        return os.getenv("FLORESTA_TEMP_DIR")

    @staticmethod
    def create_data_dirs(data_dir: str, base_name: str, nodes: int) -> list[str]:
        """Create the data directories for any nodes to be used in the test."""
        paths = []
        for i in range(nodes):
            p = os.path.join(data_dir, "data", base_name, f"node-{i}")
            os.makedirs(p, exist_ok=True)
            paths.append(p)
        return paths

    @staticmethod
    def get_available_random_port(start: int, end: int = 65535):
        """Get an available random port in the range [start, end]"""
        while True:
            port = random.randint(start, end)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    return port

    def get_test_log_path(self) -> str:
        """Get the path for the test name log file."""
        tempdir = str(self.get_integration_test_dir())
        
        # Get the class's base filename
        filename = sys.modules[self.__class__.__module__].__file__
        filename = os.path.basename(filename)
        filename = filename.replace(".py", "")

        return os.path.join(tempdir, "logs", f"{filename}.log")

    def create_tls_key_cert(self) -> tuple[str, str]:
        """Create a PKCS#8 formatted private key and a self-signed certificate."""
        tls_rel_path = os.path.join(
            self.get_integration_test_dir(), "data", "tls"
        )
        tls_path = os.path.normpath(os.path.abspath(tls_rel_path))
        os.makedirs(tls_path, exist_ok=True)

        pk_path, private_key = create_pkcs8_private_key(tls_path)
        self.log(f"Created PKCS#8 key at {pk_path}")

        cert_path = create_pkcs8_self_signed_certificate(
            tls_path, private_key, common_name="florestad", validity_days=365
        )
        self.log(f"Created self-signed certificate at {cert_path}")

        return (pk_path, cert_path)

    def is_option_set(self, extra_args: list[str], option: str) -> bool:
        """Check if an option is set in extra_args"""
        for arg in extra_args:
            if arg.startswith(option):
                return True
        return False

    def create_data_dir_for_daemon(
        self,
        data_dir_arg: str,
        default_args: list[str],
        extra_args: list[str],
        tempdir: str,
        testname: str,
    ):
        """Create a data directory for the daemon to be run."""
        if not self.is_option_set(extra_args, data_dir_arg):
            datadir = os.path.normpath(os.path.join(tempdir, "data", testname))
            default_args.append(f"{data_dir_arg}={datadir}")
        else:
            data_dir_arg = next(
                (arg for arg in extra_args if arg.startswith(f"{data_dir_arg}="))
            )
            datadir = data_dir_arg.split("=", 1)[1]

        if not os.path.exists(datadir):
            self.log(f"Creating data directory for {data_dir_arg} in {datadir}")
            os.makedirs(datadir, exist_ok=True)

    def setup_florestad_daemon(
        self,
        targetdir: str,
        tempdir: str,
        testname: str,
        extra_args: List[str],
        tls: bool,
    ) -> FlorestaDaemon:
        """Add default args to a florestad node settings to be run."""
        daemon = FlorestaDaemon()
        daemon.create(target=targetdir)
        default_args = []

        self.create_data_dir_for_daemon(
            "--data-dir", default_args, extra_args, tempdir, testname
        )

        if not self.is_option_set(extra_args, "--rpc-address"):
            port = self.get_available_random_port(18443, 19443)
            default_args.append(f"--rpc-address=127.0.0.1:{port}")

        if not self.is_option_set(extra_args, "--electrum-address"):
            electrum_port = self.get_available_random_port(20001, 21001)
            default_args.append(f"--electrum-address=127.0.0.1:{electrum_port}")

        if tls:
            key, cert = self.create_tls_key_cert()
            default_args.append("--enable-electrum-tls")
            default_args.append(f"--tls-key-path={key}")
            default_args.append(f"--tls-cert-path={cert}")

            if not self.is_option_set(extra_args, "--electrum-address-tls"):
                tls_electrum_port = self.get_available_random_port(20002, 21002)
                default_args.append(
                    f"--electrum-address-tls=127.0.0.1:{tls_electrum_port}"
                )

        daemon.add_daemon_settings(default_args)
        daemon.add_daemon_settings(extra_args)
        return daemon

    def setup_utreexod_daemon(
        self,
        targetdir: str,
        tempdir: str,
        testname: str,
        extra_args: List[str],
        tls: bool,
    ):
        """Add default args to a utreexod node settings to be run."""
        daemon = UtreexoDaemon()
        daemon.create(target=targetdir)
        default_args = []

        self.create_data_dir_for_daemon(
            "--datadir", default_args, extra_args, tempdir, testname
        )

        if not self.is_option_set(extra_args, "--listen"):
            port = self.get_available_random_port(18444, 19444)
            default_args.append(f"--listen=127.0.0.1:{port}")

        if not self.is_option_set(extra_args, "--rpclisten"):
            port = self.get_available_random_port(18443, 19443)
            default_args.append(f"--rpclisten=127.0.0.1:{port}")

        if not self.is_option_set(extra_args, "--electrumlisteners"):
            electrum_port = self.get_available_random_port(20001, 21001)
            default_args.append(f"--electrumlisteners=127.0.0.1:{electrum_port}")

        if not tls:
            default_args.append("--notls")
        else:
            key, cert = self.create_tls_key_cert()
            default_args.append(f"--rpckey={key}")
            default_args.append(f"--rpccert={cert}")

            if not self.is_option_set(extra_args, "--tlselectrumlisteners"):
                tls_electrum_port = self.get_available_random_port(20002, 21002)
                default_args.append(f"--tlselectrumlisteners={tls_electrum_port}")

        daemon.add_daemon_settings(default_args)
        daemon.add_daemon_settings(extra_args)
        return daemon

    def setup_bitcoind_daemon(
        self,
        targetdir: str,
        tempdir: str,
        testname: str,
        extra_args: List[str],
    ) -> BitcoinDaemon:
        """Add default args to a bitcoind node settings to be run."""
        daemon = BitcoinDaemon()
        daemon.create(target=targetdir)
        default_args = []

        self.create_data_dir_for_daemon(
            "-datadir", default_args, extra_args, tempdir, testname
        )

        if not self.is_option_set(extra_args, "-bind"):
            port = self.get_available_random_port(18445, 19445)
            default_args.append(f"-bind=127.0.0.1:{port}")

        if not self.is_option_set(extra_args, "-rpcbind"):
            port = self.get_available_random_port(20443, 21443)
            default_args.append("-rpcallowip=127.0.0.1")
            default_args.append(f"-rpcbind=127.0.0.1:{port}")

        daemon.add_daemon_settings(default_args)
        daemon.add_daemon_settings(extra_args)
        return daemon

    def add_node(
        self,
        extra_args: List[str] = None,
        variant: str = "florestad",
        tls: bool = False,
    ) -> Node:
        """Add a node settings to be run."""
        if extra_args is None:
            extra_args = []
            
        tempdir = str(self.get_integration_test_dir())
        targetdir = os.path.normpath(os.path.join(tempdir, "binaries"))
        testname = self.__class__.__name__.lower()

        if variant == "florestad":
            daemon = self.setup_florestad_daemon(targetdir, tempdir, testname, extra_args, tls)
            rpcserver = copy.deepcopy(florestad_rpc_server)
        elif variant == "utreexod":
            daemon = self.setup_utreexod_daemon(targetdir, tempdir, testname, extra_args, tls)
            rpcserver = copy.deepcopy(utreexod_rpc_server)
        elif variant == "bitcoind":
            daemon = self.setup_bitcoind_daemon(targetdir, tempdir, testname, extra_args)
            rpcserver = copy.deepcopy(bitcoind_rpc_server)
        else:
            raise ValueError(
                f"Unsupported variant: {variant}. Use 'florestad', 'utreexod' or 'bitcoind'."
            )

        node = Node(daemon, rpc=None, rpc_config=rpcserver, variant=variant)
        self._nodes.append(node)
        return node

    def get_node(self, index: int) -> Node:
        """Given an index, return a node configuration."""
        if index < 0 or index >= len(self._nodes):
            raise IndexError(
                f"Node {index} not found. Please run it with add_node"
            )
        return self._nodes[index]

    def detect_ports(
        self, mode: str, log_file: TextIO, timeout: int = 180
    ) -> Dict[str, int]:
        """Generic port detector for florestad, utreexod, and bitcoind logs."""
        required_patterns: Dict[str, re.Pattern]
        optional_patterns: Dict[str, re.Pattern] = {}

        if mode == "florestad":
            required_patterns = {
                "rpc": re.compile(r"RPC server is running at [0-9.]+:(\d+)"),
                "electrum-server": re.compile(
                    r"Electrum Server is running at [0-9.]+:(\d+)"
                ),
            }
            optional_patterns = {
                "electrum-server-tls": re.compile(
                    r"Electrum TLS Server is running at [0-9.]+:(\d+)"
                )
            }
        elif mode == "utreexod":
            required_patterns = {
                "rpc": re.compile(r".*RPCS: RPC server listening on [\d.]+:(\d+)"),
                "p2p": re.compile(r".*CMGR: Server listening on [\d.]+:(\d+)"),
            }
        elif mode == "bitcoind":
            required_patterns = {
                "rpc": re.compile(r"Binding RPC on address [0-9.]+ port (\d+)"),
                "p2p": re.compile(r"Bound to [0-9.]+:(\d+)"),
            }
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        ports: Dict[str, int] = {}
        log_file.seek(0, 2)
        start_time = time.time()
        time_tls = None
        tls_period = 0.5

        while time.time() - start_time <= timeout:
            line = log_file.readline()
            if not line:
                time.sleep(0.1)
                continue

            for name, pattern in required_patterns.items():
                if name not in ports:
                    match = pattern.search(line)
                    if match:
                        ports[name] = int(match.group(1))
                        self.log(f"Detected {mode} {name} port: {ports[name]}")

            for name, pattern in optional_patterns.items():
                if name not in ports:
                    match = pattern.search(line)
                    if match:
                        ports[name] = int(match.group(1))
                        self.log(f"Detected {mode} optional {name} port: {ports[name]}")

            if all(name in ports for name in required_patterns):
                if not optional_patterns:
                    return ports
                if time_tls is None:
                    time_tls = time.time()
                elif (time.time() - time_tls) >= tls_period:
                    return ports

        raise TimeoutError(
            f"Timeout waiting for {mode} ports: {list(required_patterns)}"
        )

    def run_node(self, node: Node, timeout: int = 180):
        """Run a node and detect its ports from logs."""
        node.daemon.start()

        log_path = self.get_test_log_path()
        log_file = open(log_path, "r", encoding="utf-8")

        node.rpc_config["ports"] = self.detect_ports(node.variant, log_file)
        self.log(node.rpc_config)

        if node.variant == "florestad":
            node.rpc = FlorestaRPC(node.daemon.process, node.rpc_config)
        elif node.variant == "utreexod":
            node.rpc = UtreexoRPC(node.daemon.process, node.rpc_config)
        elif node.variant == "bitcoind":
            node.rpc = BitcoinRPC(node.daemon.process, node.rpc_config)

        node.rpc.wait_for_connections(opened=True, timeout=timeout)
        self.log(f"Node '{node.variant}' started")

    def stop_node(self, index: int):
        """Stop a node given an index."""
        node = self.get_node(index)
        return node.stop()