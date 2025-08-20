import argparse
import os
import sys
import time
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO

from test_framework import FlorestaTestFramework
from example.bitcoin import BitcoindTest

INFO_EMOJI = "ℹ️"
SUCCESS_EMOJI = "✅"
FAILURE_EMOJI = "❌"
ALLDONE_EMOJI = "🎉"

# Simple test registry
TEST_REGISTRY = {
    "bitcoin": BitcoindTest,
}

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
    
    print(f"{INFO_EMOJI} Running test: {test_name}")
    
    start_time = time.time()
    success = False
    output_capture = StringIO()
    
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
            # Write the exit error to log
            with open(log_path, "a") as log_file:
                log_file.write(f"\nTest exited with code: {e.code}\n")
    except Exception as e:
        # Don't print to screen here, write to log instead
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
    
    return success, duration, output

def main():
    """Simple direct test runner"""
    
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

    # Simple argument parser
    parser = argparse.ArgumentParser(prog="simple_direct_runner")
    parser.add_argument(
        "-t", "--test",
        default="bitcoin",
        choices=list(TEST_REGISTRY.keys()),
        help="Test to run (default: bitcoin)"
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

    args = parser.parse_args()
    
    # Setup test
    test_name = args.test
    test_class = TEST_REGISTRY[test_name]
    log_path = os.path.join(log_dir, f"{test_name}.log")
    
    print(f"{INFO_EMOJI} Running {test_name} test")
    print(f"{INFO_EMOJI} Log file: {log_path}")
    
    # Run the test
    start_time = time.time()
    success, duration, output = run_test_direct(
        test_name, test_class, log_path, args.log_buffer, args.verbose
    )
    end_time = time.time()
    
    # Show results (minimal screen output)
    if success:
        print(f"{SUCCESS_EMOJI} {test_name} PASSED in {duration:.2f}s")
        print(f"{ALLDONE_EMOJI} Test completed successfully!")
        print(f"Full log: {log_path}")
    else:
        print(f"{FAILURE_EMOJI} {test_name} FAILED in {duration:.2f}s")
        print(f"Check log file: {log_path}")
        
        if args.verbose:
            print(f"\nLast part of log:")
            print("-" * 50)
            try:
                with open(log_path, 'r') as f:
                    lines = f.readlines()
                    # Show last 20 lines
                    for line in lines[-20:]:
                        print(line.rstrip())
            except Exception as e:
                print(f"Could not read log file: {e}")
            print("-" * 50)
        
        sys.exit(1)
    
    print(f"Total runtime: {end_time - start_time:.2f}s")

if __name__ == "__main__":
    main()