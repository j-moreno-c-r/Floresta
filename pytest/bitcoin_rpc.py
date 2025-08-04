import subprocess
import json
import time
import os
import tempfile
import shutil
from pathlib import Path



class BitcoindDirectTest:
    expected_chain = "regtest"
    expected_height = 0
    expected_headers = 0
    expected_blockhash = "0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206"
    expected_difficulty = 1
    
    def __init__(self):
        self.bitcoind_process = None
        self.datadir = None
        self.rpc_port = 18443  
        self.rpc_user = "test"
        self.rpc_password = "test"
        
    def setup_datadir(self):
        self.datadir = tempfile.mkdtemp(prefix="bitcoind_test_")
        
        conf_content = f"""
        regtest=1
        server=1
        rpcuser={self.rpc_user}
        rpcpassword={self.rpc_password}
        rpcport={self.rpc_port}
        rpcbind=127.0.0.1
        rpcallowip=127.0.0.1
        fallbackfee=0.0002
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
            "-fallbackfee=0.0002"
        ]
        
        print("Starting bitcoind...")
        self.bitcoind_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        self.wait_for_rpc()
        print("Bitcoind started successfully")
    
    def wait_for_rpc(self, timeout=30):
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                result = self.rpc_call("getblockchaininfo")
                if result:
                    return True
            except Exception:
                time.sleep(1)
                continue
        
        raise Exception("RPC server failed to start within timeout")
    
    def rpc_call(self, method, params=None):
        if params is None:
            params = []
            
        payload = {
            "jsonrpc": "1.0",
            "id": "test",
            "method": method,
            "params": params
        }
        
        cmd = [
            "bitcoin-cli",
            f"-datadir={self.datadir}",
            "-regtest",
            f"-rpcuser={self.rpc_user}",
            f"-rpcpassword={self.rpc_password}",
            f"-rpcport={self.rpc_port}",
            method
        ] + [str(p) for p in params]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return result.stdout.strip()
                
        except subprocess.CalledProcessError as e:
            print(f"RPC call failed: {e}")
            print(f"stderr: {e.stderr}")
            raise
    
    def stop_bitcoind(self):
        if self.bitcoind_process:
            print("Stopping bitcoind...")
            try:
                self.rpc_call("stop")
                self.bitcoind_process.wait(timeout=10)
            except (subprocess.TimeoutExpired, Exception):
                self.bitcoind_process.terminate()
                try:
                    self.bitcoind_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.bitcoind_process.kill()
                    self.bitcoind_process.wait()
            
            self.bitcoind_process = None
            print("Bitcoind stopped")
    
    def cleanup(self):
        if self.datadir and os.path.exists(self.datadir):
            shutil.rmtree(self.datadir)
            print(f"Cleaned up datadir: {self.datadir}")
    
    


