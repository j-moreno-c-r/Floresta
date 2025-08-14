import subprocess
import json
import time
import os
import tempfile
import shutil
from pathlib import Path


class BitcoindDirectTest:
    def __init__(self):
        self.bitcoind_process = None
        self.datadir = None
        self.rpc_port = 18443  
        self.rpc_user = "test"
        self.rpc_password = "test"
        
    def setup_datadir(self):
        self.datadir = tempfile.mkdtemp(prefix="bitcoind_test_")
        
        conf_content = f"""regtest=1
                    server=1
                    rpcuser={self.rpc_user}
                    rpcpassword={self.rpc_password}
                    rpcport={self.rpc_port}
                    rpcbind=127.0.0.1
                    rpcallowip=127.0.0.1
                    fallbackfee=0.0002
                    daemon=0
                    printtoconsole=1
                    """
                
        conf_path = Path(self.datadir) / "bitcoin.conf"
        with open(conf_path, 'w') as f:
            f.write(conf_content)
            
        print(f"Created datadir: {self.datadir}")
    
    def start_bitcoind(self):
        cmd = [
            "bitcoind",
            f"-datadir={self.datadir}",
            "-regtest",
            "-server",
            f"-rpcuser={self.rpc_user}",
            f"-rpcpassword={self.rpc_password}",
            f"-rpcport={self.rpc_port}",
            "-rpcbind=127.0.0.1",
            "-rpcallowip=127.0.0.1",
            "-fallbackfee=0.0002",
            "-daemon=0", 
            "-printtoconsole=1" 
        ]
        
        print("Starting bitcoind...")
        self.bitcoind_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        print("Waiting for bitcoind to initialize...")
        time.sleep(3)  
        
        self.wait_for_rpc()
        print("Bitcoind started successfully")
    
    def wait_for_rpc(self, timeout=60):  
        start_time = time.time()
        attempt = 0
        while time.time() - start_time < timeout:
            try:
                attempt += 1
                print(f"RPC connection attempt {attempt}...")
                
                result = self.rpc_call("getnetworkinfo", timeout=5)
                if result:
                    print("RPC server is responding")
                    return True
                    
            except Exception as e:
                print(f"RPC attempt {attempt} failed: {str(e)}")
                time.sleep(2)  
                continue
        
        if self.bitcoind_process and self.bitcoind_process.poll() is not None:
            stdout, stderr = self.bitcoind_process.communicate()
            print(f"Bitcoind process exited with code: {self.bitcoind_process.returncode}")
            print(f"stdout: {stdout}")
            print(f"stderr: {stderr}")
        
        raise Exception("RPC server failed to start within timeout")
    
    def rpc_call(self, method, params=None, timeout=30):
        if params is None:
            params = []
            
        cmd = [
            "bitcoin-cli",
            f"-datadir={self.datadir}",
            "-regtest",
            f"-rpcuser={self.rpc_user}",
            f"-rpcpassword={self.rpc_password}",
            f"-rpcport={self.rpc_port}",
            "-rpcconnect=127.0.0.1",
            f"-rpcclienttimeout={timeout}",
            method
        ] + [str(p) for p in params]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout + 5  
            )
            
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return result.stdout.strip()
                
        except subprocess.CalledProcessError as e:
            print(f"RPC call '{method}' failed: {e}")
            print(f"Command: {' '.join(cmd)}")
            print(f"stdout: {e.stdout}")
            print(f"stderr: {e.stderr}")
            raise
        except subprocess.TimeoutExpired as e:
            print(f"RPC call '{method}' timed out after {timeout}s")
            raise
    
    def stop_bitcoind(self):
        if self.bitcoind_process:
            print("Stopping bitcoind...")
            try:
                self.rpc_call("stop", timeout=10)
                print("Sent stop command, waiting for process to exit...")
                self.bitcoind_process.wait(timeout=15)
                print("Bitcoind stopped gracefully")
            except (subprocess.TimeoutExpired, Exception) as e:
                print(f"Graceful shutdown failed: {e}, terminating...")
                self.bitcoind_process.terminate()
                try:
                    self.bitcoind_process.wait(timeout=10)
                    print("Bitcoind terminated")
                except subprocess.TimeoutExpired:
                    print("Force killing bitcoind...")
                    self.bitcoind_process.kill()
                    self.bitcoind_process.wait()
                    print("Bitcoind killed")
            
            self.bitcoind_process = None
    
    def cleanup(self):
        self.stop_bitcoind()  
        if self.datadir and os.path.exists(self.datadir):
            shutil.rmtree(self.datadir)
            print(f"Cleaned up datadir: {self.datadir}")
    
    def test_blockchain_info(self):
        info = self.rpc_call("getblockchaininfo")
        print(f"Blockchain info: {json.dumps(info, indent=2)}")
        
        assert info["chain"] == self.expected_chain
        assert info["blocks"] == self.expected_height
        assert info["headers"] == self.expected_headers
        assert info["bestblockhash"] == self.expected_blockhash
        assert info["difficulty"] == self.expected_difficulty
        
        return info
    
    def run_test(self):
        try:
            self.setup_datadir()
            self.start_bitcoind()
            self.test_blockchain_info()
            print("All tests passed!")
        finally:
            self.cleanup()


if __name__ == "__main__":
    test = BitcoindDirectTest()
    test.run_test()