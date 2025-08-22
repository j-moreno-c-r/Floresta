import argparse
import os
import sys
import time
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO



INFO_EMOJI = "ℹ️"
SUCCESS_EMOJI = "✅"
FAILURE_EMOJI = "❌"
ALLDONE_EMOJI = "🎉"
WARNING_EMOJI = "⚠️"
RUNNING_EMOJI = "🏃"


"""BASE_TEST_SUITE = [
    ("floresta-cli", "addnode-v2"),✅
    ("floresta-cli", "addnode-v1"),✅
    ("florestad", "reorg-chain"),❌
    ("floresta-cli", "getbestblockhash"),❌
    ("floresta-cli", "getblockcount"),❌
    ("floresta-cli", "uptime"),✅
    ("florestad", "restart"),✅
    ("florestad", "connect"),❌
    ("floresta-cli", "stop"),✅
    ("floresta-cli", "ping"),✅
    ("floresta-cli", "getrpcinfo"),✅
    ("example", "integration"),❌
    ("example", "functional"),✅
    ("example", "electrum"),✅
    ("floresta-cli", "getblockhash"),✅
    ("florestad", "tls"),✅
    ("floresta-cli", "getroots"),✅
    ("floresta-cli", "getblock"),✅
    ("floresta-cli", "getmemoryinfo"),✅
    ("floresta-cli", "getblockheader"),✅
    ("floresta-cli", "getpeerinfo"),✅
    ("florestad", "tls-fail"),✅
    ("floresta-cli", "getblockchaininfo"),✅
    ("example", "bitcoin"),✅
    ("example", "utreexod"),❌
]
"""


from test_framework import FlorestaTestFramework
from example.utreexod import UtreexodTest
from example.bitcoin import BitcoindTest
from example.electrum import ElectrumTest
from example.functional import FunctionalTest
from example.integration import IntegrationTest
from floresta_cli.addnode_v1 import AddnodeTestV1
from floresta_cli.addnode_v2 import AddnodeTestV2
from floresta_cli.getbestblockhash import GetBestblockhashTest
from floresta_cli.getblockcount import GetBlockCountTest
from floresta_cli.uptime import UptimeTest
from floresta_cli.stop import StopTest
from floresta_cli.getrpcinfo import GetRpcInfoTest
from floresta_cli.ping import PingTest
from floresta_cli.getblockcount import GetBlockCountTest
from floresta_cli.getblockhash import GetBlockhashTest
from floresta_cli.getroots import GetRootsIDBLenZeroTest
from floresta_cli.getblock import GetBlockTest
from floresta_cli.getmemoryinfo import GetMemoryInfoTest
from floresta_cli.getblockheader import GetBlockheaderHeightZeroTest
from floresta_cli.getpeerinfo import GetPeerInfoTest
from floresta_cli.getblockchaininfo import GetBlockchaininfoTest
from florestad.connect import CliConnectTest
from florestad.restart import TestRestart
from florestad.tls import TestSslInitialization
from florestad.reorg_chain import ChainReorgTest
from florestad.tls_fail import TestSslFailInitialization    





# Simple test registry - add more tests here
TEST_REGISTRY = {
    #"uptime": UptimeTest,
    #"stop": StopTest,
    #"ping" : PingTest, 
    #"getrpcinfo": GetRpcInfoTest,
    #"integration": IntegrationTest,
    #"getbestblockhash": GetBestblockhashTest,
    #"getblockhash": GetBlockhashTest,
    #"getblockcount": GetBlockCountTest,
    #"getroots": GetRootsIDBLenZeroTest,
    #"getblock":GetBlockTest,
    #"getmemoryinfo": GetMemoryInfoTest,
    #"getblockheader": GetBlockheaderHeightZeroTest,
    #"getpeerinfo":GetPeerInfoTest,
    #"getblockchaininfo": GetBlockchaininfoTest,
    #"addnodev2": AddnodeTestV2,
    #"addnodev1": AddnodeTestV1,
    #"functional": FunctionalTest,
    #"connect": CliConnectTest,
    #"tls": TestSslInitialization,
    #"tls_fail":TestSslFailInitialization,
    #"reorg_chain":ChainReorgTest,
    #"restart": TestRestart,
    #"utreexod": UtreexodTest,
    "bitcoin": BitcoindTest,
    "electrum": ElectrumTest,
}

class TestResult:
    """Container for test execution results"""
    def __init__(self, name, success=False, duration=0.0, error_msg=None):
        self.name = name
        self.success = success
        self.duration = duration
        self.error_msg = error_msg

class TeeOutput:
    """Tee output to both a file and capture buffer"""
    def __init__(self, log_file, capture_buffer):
        self.log_file = log_file
        self.capture_buffer = capture_buffer
    
    def write(self, text):
        self.log_file.write(text)
        self.log_file.flush()
        self.capture_buffer.write(text)
        return len(text)
    
    def flush(self):
        self.log_file.flush()
        self.capture_buffer.flush()

def run_test_direct(test_name, test_class, log_path, log_buffer, verbose=False):
    """Run a single test by directly calling main()"""
    
    start_time = time.time()
    success = False
    output_capture = StringIO()
    error_msg = None
    
    # Clear/create the log file first
    with open(log_path, "w") as f:
        f.write(f"Starting {test_name} test at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 50 + "\n")
    
    try:
        with open(log_path, "a", buffering=log_buffer) as log_file:
            tee = TeeOutput(log_file, output_capture)
            
            with redirect_stdout(tee), redirect_stderr(tee):
                # Create and run the test using main() method
                test_instance = test_class()
                test_instance.main()
                
                success = True
                
    except SystemExit as e:
        # Test framework uses sys.exit(0) for success, non-zero for failure
        success = (e.code == 0)
        if not success:
            error_msg = f"Test exited with code: {e.code}"
            # Write the exit error to log
            with open(log_path, "a") as log_file:
                log_file.write(f"\n{error_msg}\n")
    except Exception as e:
        # Capture the exception for reporting
        error_msg = f"Error running test {test_name}: {e}"
        with open(log_path, "a") as log_file:
            log_file.write(f"\n{error_msg}\n")
            if verbose:
                import traceback
                log_file.write(traceback.format_exc())
        success = False
    
    end_time = time.time()
    duration = end_time - start_time
    output = output_capture.getvalue()
    
    # Write completion info to log
    with open(log_path, "a") as log_file:
        log_file.write(f"\n" + "=" * 50 + "\n")
        log_file.write(f"Test completed: {'SUCCESS' if success else 'FAILED'}\n")
        log_file.write(f"Duration: {duration:.2f}s\n")
    
    return TestResult(test_name, success, duration, error_msg)

def run_all_tests(test_registry, log_dir, log_buffer, verbose=False, continue_on_failure=True):
    """Run all tests in the registry"""
    
    print(f"{RUNNING_EMOJI} Running {len(test_registry)} tests...")
    print("=" * 60)
    
    results = []
    overall_start_time = time.time()
    
    for test_name, test_class in test_registry.items():
        log_path = os.path.join(log_dir, f"{test_name}.log")
        
        print(f"{INFO_EMOJI} Running test: {test_name}")
        
        # Run the test
        result = run_test_direct(
            test_name, test_class, log_path, log_buffer, verbose
        )
        results.append(result)
        
        # Show immediate result
        if result.success:
            print(f"{SUCCESS_EMOJI} {test_name} PASSED in {result.duration:.2f}s")
        else:
            print(f"{FAILURE_EMOJI} {test_name} FAILED in {result.duration:.2f}s")
            if result.error_msg:
                print(f"    Error: {result.error_msg}")
            
            if not continue_on_failure:
                print(f"{WARNING_EMOJI} Stopping test execution due to failure")
                break
        
        print(f"    Log: {log_path}")
        print()
    
    overall_end_time = time.time()
    overall_duration = overall_end_time - overall_start_time
    
    return results, overall_duration

def print_summary(results, overall_duration, verbose=False, log_dir=None):
    """Print a summary of all test results"""
    
    passed = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    
    print("=" * 60)
    print(f"TEST SUMMARY")
    print("=" * 60)
    
    # Overall stats
    print(f"Total tests: {len(results)}")
    print(f"Passed: {len(passed)} {SUCCESS_EMOJI}")
    print(f"Failed: {len(failed)} {FAILURE_EMOJI}")
    print(f"Overall time: {overall_duration:.2f}s")
    print()
    
    # Passed tests
    if passed:
        print(f"{SUCCESS_EMOJI} PASSED TESTS:")
        for result in passed:
            print(f"  • {result.name:<15} ({result.duration:.2f}s)")
        print()
    
    # Failed tests
    if failed:
        print(f"{FAILURE_EMOJI} FAILED TESTS:")
        for result in failed:
            print(f"  • {result.name:<15} ({result.duration:.2f}s)")
            if result.error_msg:
                print(f"    └─ {result.error_msg}")
        print()
        
        if verbose and log_dir:
            print("For detailed error information, check the log files:")
            for result in failed:
                log_path = os.path.join(log_dir, f"{result.name}.log")
                print(f"  • {result.name}: {log_path}")
            print()
    
    # Final result
    if len(failed) == 0:
        print(f"{ALLDONE_EMOJI} All tests PASSED!")
        return True
    else:
        print(f"{FAILURE_EMOJI} {len(failed)} test(s) FAILED")
        return False

def main():
    """Multi-test runner"""
    
    # Get integration test directory
    try:
        temp_dir = FlorestaTestFramework.get_integration_test_dir()
        log_dir = os.path.join(temp_dir, 'logs')
    except RuntimeError as e:
        print(f"{FAILURE_EMOJI} Environment setup error: {e}")
        print("Make sure FLORESTA_TEMP_DIR is set")
        sys.exit(1)
    
    # Make sure log directory exists
    os.makedirs(log_dir, exist_ok=True)
    
    print(f"{INFO_EMOJI} Using log directory: {log_dir}")
    print()

    # Argument parser
    parser = argparse.ArgumentParser(
        prog="multi_test_runner",
        description="Run Floresta integration tests"
    )
    parser.add_argument(
        "-t", "--test",
        choices=list(TEST_REGISTRY.keys()) + ["all"],
        default="all",
        help="Test to run (default: all)"
    )
    parser.add_argument(
        "-b", "--log-buffer",
        type=int,
        default=1024,
        help="Log buffer size (default: 1024)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show verbose output on failure"
    )
    parser.add_argument(
        "--stop-on-failure",
        action="store_true",
        help="Stop running tests after first failure"
    )
    parser.add_argument(
        "--list-tests",
        action="store_true",
        help="List available tests and exit"
    )

    args = parser.parse_args()
    
    # Handle --list-tests
    if args.list_tests:
        print(f"{INFO_EMOJI} Available tests:")
        for test_name in sorted(TEST_REGISTRY.keys()):
            print(f"  • {test_name}")
        print(f"\nTotal: {len(TEST_REGISTRY)} tests")
        return
    
    # Determine which tests to run
    if args.test == "all":
        tests_to_run = TEST_REGISTRY
        print(f"{INFO_EMOJI} Running ALL tests ({len(tests_to_run)} total)")
    else:
        tests_to_run = {args.test: TEST_REGISTRY[args.test]}
        print(f"{INFO_EMOJI} Running single test: {args.test}")
    
    print(f"{INFO_EMOJI} Log directory: {log_dir}")
    
    # Run the tests
    overall_start_time = time.time()
    
    if len(tests_to_run) == 1:
        # Single test mode - use original behavior
        test_name = list(tests_to_run.keys())[0]
        test_class = list(tests_to_run.values())[0]
        log_path = os.path.join(log_dir, f"{test_name}.log")
        
        result = run_test_direct(
            test_name, test_class, log_path, args.log_buffer, args.verbose
        )
        
        if result.success:
            print(f"{SUCCESS_EMOJI} {test_name} PASSED in {result.duration:.2f}s")
            print(f"{ALLDONE_EMOJI} Test completed successfully!")
        else:
            print(f"{FAILURE_EMOJI} {test_name} FAILED in {result.duration:.2f}s")
            if args.verbose and result.error_msg:
                print(f"Error: {result.error_msg}")
        
        print(f"Full log: {log_path}")
        
        if not result.success:
            sys.exit(1)
    else:
        # Multi-test mode
        results, overall_duration = run_all_tests(
            tests_to_run, 
            log_dir, 
            args.log_buffer, 
            args.verbose,
            continue_on_failure=not args.stop_on_failure
        )
        
        # Print summary
        all_passed = print_summary(results, overall_duration, args.verbose, log_dir)
        
        if not all_passed:
            sys.exit(1)
    
    overall_end_time = time.time()
    print(f"Total runtime: {overall_end_time - overall_start_time:.2f}s")

if __name__ == "__main__":
    main()