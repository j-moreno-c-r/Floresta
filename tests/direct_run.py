import argparse
import os
import sys
import traceback
import time
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import subprocess
import threading
import signal
from queue import Queue, Empty
from example.bitcoin import BitcoindTest
from test_framework import FlorestaTestFramework

INFO_EMOJI = "ℹ️"
SUCCESS_EMOJI = "✅"
FAILURE_EMOJI = "❌"
ALLDONE_EMOJI = "🎉"

# Map test names to their classes
TEST_REGISTRY = {
    "bitcoin": BitcoindTest,
}

class TeeOutput:
    """Tee output to both a file and capture buffer"""
    def __init__(self, log_file, capture_buffer):
        self.log_file = log_file
        self.capture_buffer = capture_buffer
    
    def write(self, text):
        # Write to both the log file (for port detection) and capture buffer
        self.log_file.write(text)
        self.log_file.flush()  # Ensure immediate write for port detection
        self.capture_buffer.write(text)
        return len(text)
    
    def flush(self):
        self.log_file.flush()
        self.capture_buffer.flush()

def get_integration_test_structure():
    """Get the proper integration test directory structure"""
    try:
        # Use the framework's method to get the temp dir
        temp_dir = FlorestaTestFramework.get_integration_test_dir()
        return {
            'base': temp_dir,
            'logs': os.path.join(temp_dir, 'logs'),
            'binaries': os.path.join(temp_dir, 'binaries'),
            'data': os.path.join(temp_dir, 'data')
        }
    except RuntimeError as e:
        print(f"{FAILURE_EMOJI} Environment setup error: {e}")
        print("Make sure FLORESTA_TEMP_DIR is set to something like:")
        print("/tmp/floresta-integration-tests.$(git rev-parse HEAD)")
        sys.exit(1)

def setup_test_environment(test_name, structure):
    """Setup the test environment with proper directory structure"""
    
    # Ensure all required directories exist
    for dir_name, dir_path in structure.items():
        os.makedirs(dir_path, exist_ok=True)
    
    # Setup the specific test log file
    log_path = os.path.join(structure['logs'], f"{test_name}.log")
    
    # Create/truncate the log file
    with open(log_path, 'w') as f:
        f.write("")  # Clear the file
    
    return log_path


class PortDetector:
    """Helper class to detect ports from process output in real-time"""
    def __init__(self, pattern=None):
        self.pattern = pattern or r"Listening on.*?port.*?(\d+)"
        self.detected_ports = []
        self.lock = threading.Lock()
    
    def detect_from_stream(self, stream):
        """Read from a stream and detect ports"""
        while True:
            line = stream.readline()
            if not line:
                break
            # Check for port patterns
            matches = re.findall(self.pattern, line.decode('utf-8', errors='ignore'))
            with self.lock:
                self.detected_ports.extend(matches)
    
    def get_ports(self):
        """Get all detected ports"""
        with self.lock:
            return self.detected_ports.copy()

def run_test(test_name, test_class, structure, log_buffer, verbose=False, timeout=300):
    """Run a single test with proper port detection"""
    success = False
    log_path = setup_test_environment(test_name, structure)
    
    # Build the command to run the test
    test_file = os.path.join(os.path.dirname(__file__), "example", f"{test_name}.py")
    cmd = [sys.executable, test_file]
    
    print(f"{INFO_EMOJI} Running test: {test_name}")
    if verbose:
        print(f"{INFO_EMOJI} Command: {' '.join(cmd)}")
    
    start_time = time.time()
    
    try:
        # Open log file
        with open(log_path, "wb", buffering=log_buffer) as log_file:
            # Start the process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=os.path.dirname(__file__),
                env={**os.environ, "FLORESTA_TEMP_DIR": structure['base']}
            )
            
            # Create a port detector
            port_detector = PortDetector()
            
            # Start a thread to read stdout and detect ports
            def read_output():
                port_detector.detect_from_stream(process.stdout)
            
            reader_thread = threading.Thread(target=read_output)
            reader_thread.daemon = True
            reader_thread.start()
            
            # Wait for process to complete or timeout
            try:
                return_code = process.wait(timeout=timeout)
                success = (return_code == 0)
            except subprocess.TimeoutExpired:
                print(f"{FAILURE_EMOJI} Test {test_name} timed out after {timeout} seconds")
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                success = False
            
            # Get detected ports
            detected_ports = port_detector.get_ports()
            if detected_ports and verbose:
                print(f"{INFO_EMOJI} Detected ports: {detected_ports}")
                
    except Exception as e:
        print(f"{FAILURE_EMOJI} Error running test {test_name}: {e}")
        success = False
    
    end_time = time.time()
    duration = end_time - start_time
    
    return success, duration, log_path

def main():
    """
    Sequential functional test runner for Floresta.
    
    Options:
        -h, --help            Show this help message and exit
        -L, --log-dir DIR     Directory for test logs (uses integration test structure by default)
        -t, --test TEST       Specific test(s) to run (can be used multiple times)
        -k, --test-name NAME  Test name pattern to filter by (can be used multiple times)
        -l, --list-tests      List all available tests
        -b, --log-buffer      Log buffer size
        -v, --verbose         Show test output in console as well as logs
    """
    
    # Get the proper integration test directory structure
    structure = get_integration_test_structure()
    
    print(f"{INFO_EMOJI} Using integration test directory: {structure['base']}")
    print(f"{INFO_EMOJI} Logs will be in: {structure['logs']}")
    print(f"{INFO_EMOJI} Binaries expected in: {structure['binaries']}")
    print(f"{INFO_EMOJI} Data will be in: {structure['data']}")

    # Configure CLI
    parser = argparse.ArgumentParser(prog="direct_run")
    parser.add_argument(
        "-L", "--log-dir",
        default=None,  # Will use integration test structure by default
        help="Directory for test logs (overrides integration test structure)"
    )
    parser.add_argument(
        "-t", "--test",
        action="append",
        help="Test to execute (can be used multiple times). If not specified, all tests will run."
    )
    parser.add_argument(
        "-k", "--test-name",
        action="append", 
        default=[],
        help="Test name pattern to filter by (can be used multiple times)"
    )
    parser.add_argument(
        "-l", "--list-tests",
        action="store_true",
        default=False,
        help="List all available tests"
    )
    parser.add_argument(
        "-b", "--log-buffer",
        type=int,
        default=1024,
        help="Log buffer size"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Show test output in console as well as logs"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds for each test (default: 300)"
    )
    
    args = parser.parse_args()
    args = parser.parse_args()

    # Handle list tests option
    if args.list_tests:
        print(f"\n{INFO_EMOJI} Available tests:")
        for test_name in sorted(TEST_REGISTRY.keys()):
            print(f"  {test_name}")
        return

    # Override log directory if specified
    if args.log_dir:
        structure['logs'] = os.path.abspath(args.log_dir)
        print(f"{INFO_EMOJI} Overriding log directory to: {structure['logs']}")
    
    # Ensure all directories exist
    for dir_name, dir_path in structure.items():
        os.makedirs(dir_path, exist_ok=True)

    # Check if binaries directory exists and has expected binaries
    binaries_dir = structure['binaries']
    expected_binaries = ['bitcoind']  # Add more as needed
    missing_binaries = []
    
    for binary in expected_binaries:
        binary_path = os.path.join(binaries_dir, binary)
        if not os.path.exists(binary_path):
            missing_binaries.append(binary)
    
    if missing_binaries:
        print(f"{FAILURE_EMOJI} Warning: Missing binaries in {binaries_dir}:")
        for binary in missing_binaries:
            print(f"  - {binary}")
        print("Tests may fail if these binaries are required.")

    # Determine which tests to run
    if args.test:
        # Validate specified tests
        tests_to_run = []
        for test_name in args.test:
            if test_name not in TEST_REGISTRY:
                print(f"{FAILURE_EMOJI} Invalid test: {test_name}")
                print("Available tests:")
                for name in TEST_REGISTRY:
                    print(f"  {name}")
                sys.exit(1)
            tests_to_run.append(test_name)
    else:
        # Run all tests if no specific test specified
        tests_to_run = list(TEST_REGISTRY.keys())
    
    # Apply test name filters if provided
    if args.test_name:
        filtered_tests = []
        for test_name in tests_to_run:
            if any(pattern in test_name for pattern in args.test_name):
                filtered_tests.append(test_name)
        tests_to_run = filtered_tests

    if not tests_to_run:
        print(f"{INFO_EMOJI} No tests match the specified criteria.")
        return

    print(f"\n{INFO_EMOJI} Running {len(tests_to_run)} test(s): {', '.join(tests_to_run)}")
    
    # Track results
    results = {}
    total_start = time.time()
    
    # Run tests
    for test_name in tests_to_run:
        success, duration, log_path = run_test(
            test_name, 
            TEST_REGISTRY[test_name], 
            structure, 
            args.log_buffer,
            args.verbose,
            args.timeout
        )
        
        results[test_name] = {
            'success': success,
            'duration': duration,
            'log_path': log_path
        }
        
        if success:
            print(f"{SUCCESS_EMOJI} {test_name} passed in {duration:.2f}s")
        else:
            print(f"{FAILURE_EMOJI} {test_name} failed in {duration:.2f}s")
            if args.verbose:
                # Show the tail of the log file
                try:
                    with open(log_path, 'r') as f:
                        lines = f.readlines()
                        print("Last 20 lines of log:")
                        for line in lines[-20:]:
                            print(f"  {line.rstrip()}")
                except Exception as e:
                    print(f"Could not read log file: {e}")
    total_end = time.time()
    
    # Show final summary
    print("\n" + "="*50)
    print("FINAL TEST SUMMARY")
    print("="*50)
    
    passed_tests = []
    failed_tests = []
    
    for test_name, result in results.items():
        if result['success']:
            passed_tests.append(test_name)
            print(f"{SUCCESS_EMOJI} {test_name}: PASSED ({result['duration']:.2f}s)")
        else:
            failed_tests.append(test_name)
            print(f"{FAILURE_EMOJI} {test_name}: FAILED ({result['duration']:.2f}s)")
            print(f"    Log: {result['log_path']}")
    
    print(f"\nTotal runtime: {total_end - total_start:.2f}s")
    print(f"Tests passed: {len(passed_tests)}/{len(tests_to_run)}")
    
    if failed_tests:
        print(f"\nFailed tests: {', '.join(failed_tests)}")
        print("Check the log files for detailed error information.")
        if args.verbose:
            print("\nFailed test outputs:")
            for test_name in failed_tests:
                result = results[test_name]
                print(f"\n--- {test_name} ---")
                print(result['output'])
        sys.exit(1)
    else:
        print(f"\n{ALLDONE_EMOJI} All tests passed!")


if __name__ == "__main__":
    main()