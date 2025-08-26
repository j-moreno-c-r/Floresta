import os
import re
import sys
import copy
import time
import random
import socket
import signal
import contextlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Pattern

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

    def get_ports(self) -> int:
        return self.rpc_config["ports"]

    def get_port(self, port_type: str) -> int:
        if port_type not in self.rpc_config["ports"]:
            raise ValueError(
                f"Port type '{port_type}' not found in node ports: {self.rpc_config['ports']}"
            )
        return self.rpc_config["ports"][port_type]

    def send_kill_signal(self, sigcode="SIGTERM"):
        with contextlib.suppress(ProcessLookupError):
            pid = self.daemon.process.pid
            os.kill(pid, getattr(signal, sigcode, signal.SIGTERM))


class FlorestaTestMetaClass(type):
    def __new__(mcs, clsname, bases, dct):
        if not clsname == "FlorestaTestFramework":
            if not ("run_test" in dct and "set_test_params" in dct):
                raise TypeError(
                    "FlorestaTestFramework subclasses must override 'run_test' and 'set_test_params'"
                )
            if "__init__" in dct or "main" in dct:
                raise TypeError(
                    "FlorestaTestFramework subclasses may not override '__init__' or 'main'"
                )
        return super().__new__(mcs, clsname, bases, dct)


class FlorestaTestFramework(metaclass=FlorestaTestMetaClass):
    class _AssertRaisesContext:
        def __init__(self, test_framework, expected_exception):
            self.test_framework = test_framework
            self.expected_exception = expected_exception
            self.exception = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type is None:
                self.test_framework.stop_all_nodes()
                raise AssertionError(f"{self.expected_exception} was not raised")
            if not issubclass(exc_type, self.expected_exception):
                raise AssertionError(f"Expected {self.expected_exception} but got {exc_type}")
            self.exception = exc_value
            return True

    def __init__(self):
        self._nodes = []

    def log(self, msg: str):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        print(f"[{self.__class__.__name__} {now:%Y-%m-%d %H:%M:%S}] {msg}")

    def main(self):
        try:
            self.set_test_params()
            self.run_test()
        except Exception as err:
            processes = []
            for node in self._nodes:
                processes.append(str(node.daemon.process.pid))
                if getattr(node, "rpc", None):
                    node.rpc.stop()
                    node.rpc.wait_for_connections(opened=False)
                else:
                    try:
                        node.send_kill_signal("SIGTERM")
                    except Exception:
                        node.send_kill_signal("SIGKILL")
            raise RuntimeError(f"Process with pids {', '.join(processes)} failed to start: {err}") from err

    def set_test_params(self):
        raise NotImplementedError

    def run_test(self):
        raise NotImplementedError

    @staticmethod
    def get_integration_test_dir():
        if os.getenv("FLORESTA_TEMP_DIR") is None:
            raise RuntimeError("FLORESTA_TEMP_DIR not set")
        return os.getenv("FLORESTA_TEMP_DIR")

    @staticmethod
    def create_data_dirs(data_dir: str, base_name: str, nodes: int) -> list[str]:
        paths = []
        for i in range(nodes):
            p = os.path.join(data_dir, "data", base_name, f"node-{i}")
            os.makedirs(p, exist_ok=True)
            paths.append(p)
        return paths

    @staticmethod
    def get_available_random_port(start: int, end: int = 65535):
        while True:
            port = random.randint(start, end)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    return port

    def get_test_log_path(self) -> str:
        tempdir = str(self.get_integration_test_dir())
        filename = os.path.basename(sys.modules[self.__class__.__module__].__file__)
        filename = filename.replace(".py", "")
        return os.path.join(tempdir, "logs", f"{filename}.log")

    def create_tls_key_cert(self) -> tuple[str, str]:
        tls_rel_path = os.path.join(self.get_integration_test_dir(), "data", "tls")
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

    def add_node(self, extra_args: List[str] = [], variant: str = "florestad", tls: bool = False) -> Node:
        port_index = len(self._nodes)
        tempdir = str(self.get_integration_test_dir())
        targetdir = os.path.join(tempdir, "binaries")
        testname = self.__class__.__name__.lower()
    
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

    def get_node(self, index: int) -> Node:
        if index < 0 or index >= len(self._nodes):
            raise IndexError(f"Node {index} not found")
        return self._nodes[index]

    def run_node(self, node: Node, timeout: int = 180):
        node.daemon.start()
        
        if node.variant == "florestad":
            node.rpc = FlorestaRPC(node.daemon.process, node.rpc_config)
        elif node.variant == "utreexod":
            node.rpc = UtreexoRPC(node.daemon.process, node.rpc_config)
        elif node.variant == "bitcoind":
            node.rpc = BitcoinRPC(node.daemon.process, node.rpc_config)

        node.rpc.wait_for_connections(opened=True, timeout=timeout)
        self.log(f"Node '{node.variant}' started on ports: {node.rpc_config['ports']}")

    def stop_node(self, index: int):
        return self.get_node(index).stop()

    def stop(self):
        for node in self._nodes:
            node.stop()

    def stop_all_nodes(self):
        self.stop()

    def assertTrue(self, condition: bool):
        if not condition:
            self.stop()
            raise AssertionError(f"Expected: True, Got: {condition}")

    def assertFalse(self, condition: bool):
        if condition:
            self.stop()
            raise AssertionError(f"Expected: False, Got: {condition}")

    def assertIsNone(self, thing: Any):
        if thing is not None:
            self.stop()
            raise AssertionError(f"Expected: None, Got: {thing}")

    def assertIsSome(self, thing: Any):
        if thing is None:
            self.stop()
            raise AssertionError("Expected: not None")

    def assertEqual(self, condition: Any, expected: Any):
        if condition != expected:
            self.stop()
            raise AssertionError(f"Expected: {expected}, Got: {condition}")

    def assertNotEqual(self, condition: Any, expected: Any):
        if condition == expected:
            self.stop()
            raise AssertionError(f"Expected: not {expected}, Got: {condition}")

    def assertIn(self, element: Any, container: List[Any]):
        if element not in container:
            self.stop()
            raise AssertionError(f"Expected {element} in {container}")

    def assertMatch(self, actual: Any, pattern: Pattern):
        if not re.fullmatch(pattern, actual):
            self.stop()
            raise AssertionError(f"Pattern {pattern} not matched in {actual}")

    def assertRaises(self, expected_exception):
        return self._AssertRaisesContext(self, expected_exception)