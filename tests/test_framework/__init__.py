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
        """
        Start the node.
        """
        if self.daemon.is_running:
            raise RuntimeError(f"Node '{self.variant}' is already running.")
        self.daemon.start()
        self.rpc.wait_for_connections(opened=True)

    def stop(self):
        """
        Stop the node.
        """
        if self.daemon.is_running:
            response = self.rpc.stop()
            self.rpc.wait_for_connections(opened=False)
            self.daemon.process.wait()
            return response
        return None

    def get_host(self) -> str:
        """
        Get the host address of the node.
        """
        return self.rpc_config["host"]

    def get_ports(self) -> int:
        """Get all ports of the node."""
        return self.rpc_config["ports"]

    def get_port(self, port_type: str) -> int:
        """
        Get the port of the node based on the port type.
        This is a convenience method for `get_ports`.
        """
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


class FlorestaTestMetaClass(type):
    """
    Metaclass for FlorestaTestFramework.

    This metaclass ensures that any subclass of `FlorestaTestFramework`
    adheres to a standard whereby the subclass overrides `set_test_params` and
    `run_test, but DOES NOT override `__init__` or `main`. If those standards
    are violated, a `TypeError` is raised.
    """

    def __new__(mcs, clsname, bases, dct):
        if not clsname == "FlorestaTestFramework":
            if not ("run_test" in dct and "set_test_params" in dct):
                raise TypeError(
                    "FlorestaTestFramework subclasses must override 'run_test'"
                    "and 'set_test_params'"
                )

            if "__init__" in dct or "main" in dct:
                raise TypeError(
                    "FlorestaTestFramework subclasses may not override "
                    "'__init__' or 'main'"
                )

        return super().__new__(mcs, clsname, bases, dct)


# pylint: disable=too-many-public-methods
class FlorestaTestFramework(metaclass=FlorestaTestMetaClass):
    """
    Base class for a floresta test script. Individual floresta
    test scripts should:

    - subclass FlorestaTestFramework;
    - not override the __init__() method;
    - not override the main() method;
    - implement set_test_params();
    - implement run_test();

    The main change is that we now track ports directly instead of scanning log files.
    """

    class _AssertRaisesContext:
        """
        Context manager for testing that an exception is raised.
        """

        def __init__(self, test_framework, expected_exception):
            """Initialize the context manager with the expected exception type."""
            self.test_framework = test_framework
            self.expected_exception = expected_exception
            self.exception = None

        def __enter__(self):
            """Enter the context manager."""
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            """Exit the context manager and check if the expected exception was raised."""
            if exc_type is None:
                self.test_framework.stop_all_nodes()
                trace = traceback.format_exc()
                message = f"{self.expected_exception} was not raised"
                raise AssertionError(f"{message}: {trace}")

            if not issubclass(exc_type, self.expected_exception):
                trace = traceback.format_exc()
                message = f"Expected {self.expected_exception} but got {exc_type}"
                raise AssertionError(f"{message}: {trace}")

            self.exception = exc_value
            return True

    def __init__(self):
        """
        Sets test framework defaults.

        Do not override this method. Instead, override the set_test_params() method
        """
        self._nodes = []

    # pylint: disable=R0801
    def log(self, msg: str):
        """Log a message with the class caller"""

        now = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .strftime("%Y-%m-%d %H:%M:%S")
        )
        print(f"[{self.__class__.__name__} {now}] {msg}")

    def main(self):
        """
        Main function.

        This should not be overridden by the subclass test scripts.
        """
        try:
            self.set_test_params()
            self.run_test()
        except Exception as err:
            processes = []
            for node in self._nodes:

                # If the node has an RPC server, stop it gracefully
                # otherwise (maybe the error occurred before the RPC server
                # is started), try to kill the process with SIGTERM. If that
                # fails, try to force kill it with SIGKILL.
                processes.append(str(node.daemon.process.pid))
                if getattr(node, "rpc", None):
                    node.rpc.stop()
                    node.rpc.wait_for_connections(opened=False)
                else:
                    # pylint: disable=broad-exception-caught
                    try:
                        node.send_kill_signal("SIGTERM")
                    except Exception:
                        node.send_kill_signal("SIGKILL")

            raise RuntimeError(
                f"Process with pids {', '.join(processes)} failed to start: {err}"
            ) from err

    # Should be overridden by individual tests
    def set_test_params(self):
        """
        Tests must override this method to change default values for number of nodes, topology, etc
        """
        raise NotImplementedError

    def run_test(self):
        """
        Tests must override this method to run nodes, etc.
        """
        raise NotImplementedError

    @staticmethod
    def get_integration_test_dir():
        """
        Get path for florestad used in integration tests, generally set on
        $FLORESTA_TEMP_DIR/binaries
        """
        if os.getenv("FLORESTA_TEMP_DIR") is None:
            raise RuntimeError(
                "FLORESTA_TEMP_DIR not set. "
                + " Please set it to the path of the integration test directory."
            )
        return os.getenv("FLORESTA_TEMP_DIR")

    @staticmethod
    def create_data_dirs(data_dir: str, base_name: str, nodes: int) -> list[str]:
        """
        Create the data directories for any nodes to be used in the test.
        """
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
                # Check if the port is available
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    return port

    def get_test_log_path(self) -> str:
        """
        Get the path for the test name log file, which is the class name in lowercase.
        This is used to create a log file for the test.
        """
        tempdir = str(FlorestaTestFramework.get_integration_test_dir())

        # Get the class's base filename
        filename = sys.modules[self.__class__.__module__].__file__
        filename = os.path.basename(filename)
        filename = filename.replace(".py", "")

        return os.path.join(tempdir, "logs", f"{filename}.log")

    def create_tls_key_cert(self) -> tuple[str, str]:
        """
        Create a PKCS#8 formatted private key and a self-signed certificate.
        These keys are intended to be used with florestad's --tls-key-path and --tls-cert-path
        options.
        """
        # If we're in CI, we need to use the
        # path to the integration test dir
        # tempfile will be used to get the proper
        # temp dir for the OS
        tls_rel_path = os.path.join(
            FlorestaTestFramework.get_integration_test_dir(), "data", "tls"
        )
        tls_path = os.path.normpath(os.path.abspath(tls_rel_path))

        # Create the folder if not exists
        os.makedirs(tls_path, exist_ok=True)

        # Create certificates
        pk_path, private_key = create_pkcs8_private_key(tls_path)
        self.log(f"Created PKCS#8 key at {pk_path}")

        cert_path = create_pkcs8_self_signed_certificate(
            tls_path, private_key, common_name="florestad", validity_days=365
        )
        self.log(f"Created self-signed certificate at {cert_path}")

        return (pk_path, cert_path)

    def is_option_set(self, extra_args: list[str], option: str) -> bool:
        """
        Check if an option is set in extra_args
        """
        for arg in extra_args:
            if arg.startswith(option):
                return True
        return False

    def extract_port_from_args(self, extra_args: list[str], option: str) -> int:
        """
        Extract port from arguments like --rpc-address=127.0.0.1:8332
        """
        for arg in extra_args:
            if arg.startswith(f"{option}="):
                address = arg.split("=", 1)[1]
                if ":" in address:
                    return int(address.split(":")[-1])
        return None

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def create_data_dir_for_daemon(
        self,
        data_dir_arg: str,
        default_args: list[str],
        extra_args: list[str],
        tempdir: str,
        testname: str,
    ):
        """
        Create a data directory for the daemon to be run.
        """
        # Add a default data-dir if not set
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
        port_index: int,
    ) -> tuple[FlorestaDaemon, Dict[str, int]]:
        daemon = FlorestaDaemon()
        daemon.create(target=targetdir)
        default_args = []
        ports = {}
    
        self.create_data_dir_for_daemon(
            "--data-dir", default_args, extra_args, tempdir, testname
        )
    
        # RPC
        rpc_port = 18443 + port_index
        default_args.append(f"--rpc-address=127.0.0.1:{rpc_port}")
        ports["rpc"] = rpc_port
    
        # Electrum
        electrum_port = 20001 + port_index
        default_args.append(f"--electrum-address=127.0.0.1:{electrum_port}")
        ports["electrum-server"] = electrum_port
    
        # TLS
        if tls:
            key, cert = self.create_tls_key_cert()
            default_args.append("--enable-electrum-tls")
            default_args.append(f"--tls-key-path={key}")
            default_args.append(f"--tls-cert-path={cert}")
            tls_electrum_port = 21001 + port_index
            default_args.append(f"--electrum-address-tls=127.0.0.1:{tls_electrum_port}")
            ports["electrum-server-tls"] = tls_electrum_port
    
        daemon.add_daemon_settings(default_args)
        daemon.add_daemon_settings(extra_args)
        return daemon, ports
    
    def setup_utreexod_daemon(
        self,
        targetdir: str,
        tempdir: str,
        testname: str,
        extra_args: List[str],
        tls: bool,
        port_index: int,
    ) -> tuple[UtreexoDaemon, Dict[str, int]]:
        daemon = UtreexoDaemon()
        daemon.create(target=targetdir)
        default_args = []
        ports = {}

        self.create_data_dir_for_daemon(
            "--datadir", default_args, extra_args, tempdir, testname
        )

        # Use portas realmente livres!
        p2p_port = self.get_available_random_port(18000, 20000)
        default_args.append(f"--listen=127.0.0.1:{p2p_port}")
        ports["p2p"] = p2p_port

        rpc_port = self.get_available_random_port(20001, 22000)
        default_args.append(f"--rpclisten=127.0.0.1:{rpc_port}")
        ports["rpc"] = rpc_port

        electrum_port = self.get_available_random_port(22001, 24000)
        default_args.append(f"--electrumlisteners=127.0.0.1:{electrum_port}")
        ports["electrum-server"] = electrum_port

        if tls:
            key, cert = self.create_tls_key_cert()
            default_args.append(f"--rpckey={key}")
            default_args.append(f"--rpccert={cert}")
            tls_electrum_port = self.get_available_random_port(24001, 26000)
            default_args.append(f"--tlselectrumlisteners=127.0.0.1:{tls_electrum_port}")
            ports["electrum-server-tls"] = tls_electrum_port
        else:
            default_args.append("--notls")

        daemon.add_daemon_settings(default_args)
        daemon.add_daemon_settings(extra_args)
        return daemon, ports
    def setup_bitcoind_daemon(
        self,
        targetdir: str,
        tempdir: str,
        testname: str,
        extra_args: List[str],
        port_index: int,
    ) -> tuple[BitcoinDaemon, Dict[str, int]]:
        daemon = BitcoinDaemon()
        daemon.create(target=targetdir)
        default_args = []
        ports = {}
    
        self.create_data_dir_for_daemon(
            "-datadir", default_args, extra_args, tempdir, testname
        )
    
        # P2P
        p2p_port = 18445 + port_index
        default_args.append(f"-bind=127.0.0.1:{p2p_port}")
        ports["p2p"] = p2p_port
    
        # RPC
        rpc_port = 20443 + port_index
        default_args.append("-rpcallowip=127.0.0.1")
        default_args.append(f"-rpcbind=127.0.0.1:{rpc_port}")
        ports["rpc"] = rpc_port
    
        daemon.add_daemon_settings(default_args)
        daemon.add_daemon_settings(extra_args)
        return daemon, ports


    def add_node(
        self,
        extra_args: List[str] = [],
        variant: str = "florestad",
        tls: bool = False,
    ) -> Node:
        port_index = len(self._nodes)
        tempdir = str(FlorestaTestFramework.get_integration_test_dir())
        targetdir = os.path.normpath(os.path.join(tempdir, "binaries"))
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
            raise ValueError(
                f"Unsupported variant: {variant}. Use 'florestad', 'utreexod' or 'bitcoind'."
            )
    
        rpcserver["ports"] = ports
        node = Node(daemon, rpc=None, rpc_config=rpcserver, variant=variant)
        self._nodes.append(node)
        return node

    def get_node(self, index: int) -> Node:
        """
        Given an index, return a node configuration.
        If the node not exists, raise a IndexError exception.
        """
        if index < 0 or index >= len(self._nodes):
            raise IndexError(
                f"Node {index} not found. Please run it with add_node_settings"
            )
        return self._nodes[index]

    def run_node(self, node: Node, timeout: int = 180):
        """
        Run a node. Ports are already configured, so no need to scan logs.
        """
        # Start the daemon
        node.daemon.start()
        
        # Create the appropriate RPC client
        if node.variant == "florestad":
            node.rpc = FlorestaRPC(node.daemon.process, node.rpc_config)
        elif node.variant == "utreexod":
            node.rpc = UtreexoRPC(node.daemon.process, node.rpc_config)
        elif node.variant == "bitcoind":
            node.rpc = BitcoinRPC(node.daemon.process, node.rpc_config)

        # Wait for connections
        node.rpc.wait_for_connections(opened=True, timeout=timeout)
        self.log(f"Node '{node.variant}' started on ports: {node.rpc_config['ports']}")

    def stop_node(self, index: int):
        """
        Stop a node given an index on self._tests.
        """
        node = self.get_node(index)
        return node.stop()

    def stop(self):
        """
        Stop all nodes.
        """
        for i in range(len(self._nodes)):
            self.stop_node(i)

    def stop_all_nodes(self):
        """
        Stop all nodes (alias for stop).
        """
        self.stop()

    # pylint: disable=invalid-name
    def assertTrue(self, condition: bool):
        """
        Assert if the condition is True, otherwise
        all nodes will be stopped and an AssertionError will
        be raised.
        """
        if not condition:
            self.stop()
            raise AssertionError(f"Actual: {condition}\nExpected: True")

    def assertFalse(self, condition: bool):
        """
        Assert if the condition is False, otherwise
        all nodes will be stopped and an AssertionError will
        be raised.
        """
        if condition:
            self.stop()
            raise AssertionError(f"Actual: {condition}\nExpected: False")

    # pylint: disable=invalid-name
    def assertIsNone(self, thing: Any):
        """
        Assert if the condition is None, otherwise
        all nodes will be stopped and an AssertionError will
        be raised.
        """
        if thing is not None:
            self.stop()
            raise AssertionError(f"Actual: {thing}\nExpected: None")

    # pylint: disable=invalid-name
    def assertIsSome(self, thing: Any):
        """
        Assert if the condition is not None, otherwise
        all nodes will be stopped and an AssertionError will
        be raised.
        """
        if thing is None:
            self.stop()
            raise AssertionError(f"Actual: {thing}\nExpected: not None")

    # pylint: disable=invalid-name
    def assertEqual(self, condition: Any, expected: Any):
        """
        Assert if the condition is True, otherwise
        all nodes will be stopped and an AssertionError will
        be raised.
        """

        if not condition == expected:
            self.stop()
            raise AssertionError(f"Actual: {condition}\nExpected: {expected}")

    # pylint: disable=invalid-name
    def assertNotEqual(self, condition: Any, expected: Any):
        """
        Assert if the condition is True, otherwise
        all nodes will be stopped and an AssertionError will
        be raised.
        """

        if condition == expected:
            self.stop()
            raise AssertionError(f"Actual: {condition}\nExpected: !{expected}")

    # pylint: disable=invalid-name
    def assertIn(self, element: Any, listany: List[Any]):
        """
        Assert if the element is in listany , otherwise
        all nodes will be stopped and an AssertionError will
        be raised.
        """

        if element not in listany:
            self.stop()
            raise AssertionError(
                f"Actual: {element} not in {listany}\nExpected: {element} in {listany}"
            )

    # pylint: disable=invalid-name
    def assertMatch(self, actual: Any, pattern: Pattern):
        """
        Assert if the element fully matches a pattern, otherwise
        all nodes will be stopped and an AssertionError will
        be raised
        """

        if not re.fullmatch(pattern, actual):
            self.stop()
            raise AssertionError(
                f"Actual: {actual} !~ {pattern} \nExpected: {actual} ~ {pattern}"
            )

    def assertRaises(self, expected_exception):
        """Assert that the expected exception is raised."""
        return self._AssertRaisesContext(self, expected_exception)