#!/usr/bin/env python3
"""
Hyperliquid Latency Test Script

This script measures latency to Hyperliquid's REST API and WebSocket endpoints
for both testnet and mainnet environments.

Usage:
    python latency_test.py [--testnet] [--mainnet] [--iterations N] [--timeout T]
"""

import asyncio
import time
import statistics
import json
import argparse
import websockets
import requests
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime
import sys

# Hyperliquid API endpoints
TESTNET_INFO_URL = "https://api.hyperliquid-testnet.xyz/info"
MAINNET_INFO_URL = "https://api.hyperliquid.xyz/info"
TESTNET_WS_URL = "wss://api.hyperliquid-testnet.xyz/ws"
MAINNET_WS_URL = "wss://api.hyperliquid.xyz/ws"

@dataclass
class LatencyResult:
    """Container for latency test results"""
    endpoint: str
    method: str
    latency_ms: float
    success: bool
    error: Optional[str] = None
    timestamp: float = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()

@dataclass
class EndpointStats:
    """Statistics for an endpoint"""
    endpoint: str
    method: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    min_latency_ms: float
    max_latency_ms: float
    mean_latency_ms: float
    median_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    std_dev_ms: float
    success_rate: float

class LatencyTester:
    """Comprehensive latency tester for Hyperliquid APIs"""
    
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Hyperliquid-Latency-Test/1.0',
            'Content-Type': 'application/json'
        })
    
    async def test_rest_endpoint(self, url: str, method: str = "GET", 
                                data: dict = None, iterations: int = 10) -> List[LatencyResult]:
        """Test REST API endpoint latency"""
        results = []
        
        for i in range(iterations):
            start_time = time.time()
            success = False
            error = None
            
            try:
                if method.upper() == "GET":
                    response = self.session.get(url, timeout=self.timeout)
                elif method.upper() == "POST":
                    response = self.session.post(url, json=data, timeout=self.timeout)
                else:
                    raise ValueError(f"Unsupported method: {method}")
                
                response.raise_for_status()
                success = True
                
            except requests.exceptions.Timeout:
                error = "Timeout"
            except requests.exceptions.ConnectionError:
                error = "Connection Error"
            except requests.exceptions.RequestException as e:
                error = f"Request Error: {str(e)}"
            except Exception as e:
                error = f"Unexpected Error: {str(e)}"
            
            end_time = time.time()
            latency_ms = (end_time - start_time) * 1000
            
            result = LatencyResult(
                endpoint=url,
                method=method,
                latency_ms=latency_ms,
                success=success,
                error=error
            )
            results.append(result)
            
            # Small delay between requests to avoid overwhelming the server
            await asyncio.sleep(0.1)
        
        return results
    
    async def test_websocket_endpoint(self, ws_url: str, iterations: int = 10) -> List[LatencyResult]:
        """Test WebSocket endpoint latency"""
        results = []
        
        for i in range(iterations):
            start_time = time.time()
            success = False
            error = None
            
            try:
                # Test WebSocket connection and ping
                async with websockets.connect(ws_url, ping_interval=None, ping_timeout=self.timeout) as websocket:
                    # Send a simple ping message
                    ping_message = {"method": "ping"}
                    await websocket.send(json.dumps(ping_message))
                    
                    # Wait for response (with timeout)
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=self.timeout)
                        success = True
                    except asyncio.TimeoutError:
                        error = "WebSocket response timeout"
                    
            except websockets.exceptions.InvalidURI:
                error = "Invalid WebSocket URI"
            except websockets.exceptions.ConnectionClosed:
                error = "WebSocket connection closed"
            except websockets.exceptions.InvalidMessage:
                error = "Invalid WebSocket message"
            except Exception as e:
                error = f"WebSocket Error: {str(e)}"
            
            end_time = time.time()
            latency_ms = (end_time - start_time) * 1000
            
            result = LatencyResult(
                endpoint=ws_url,
                method="WEBSOCKET",
                latency_ms=latency_ms,
                success=success,
                error=error
            )
            results.append(result)
            
            # Small delay between tests
            await asyncio.sleep(0.2)
        
        return results
    
    def calculate_stats(self, results: List[LatencyResult]) -> EndpointStats:
        """Calculate statistics from latency results"""
        successful_results = [r for r in results if r.success]
        failed_results = [r for r in results if not r.success]
        
        if not successful_results:
            return EndpointStats(
                endpoint=results[0].endpoint if results else "unknown",
                method=results[0].method if results else "unknown",
                total_requests=len(results),
                successful_requests=0,
                failed_requests=len(results),
                min_latency_ms=0.0,
                max_latency_ms=0.0,
                mean_latency_ms=0.0,
                median_latency_ms=0.0,
                p95_latency_ms=0.0,
                p99_latency_ms=0.0,
                std_dev_ms=0.0,
                success_rate=0.0
            )
        
        latencies = [r.latency_ms for r in successful_results]
        latencies.sort()
        
        return EndpointStats(
            endpoint=results[0].endpoint,
            method=results[0].method,
            total_requests=len(results),
            successful_requests=len(successful_results),
            failed_requests=len(failed_results),
            min_latency_ms=min(latencies),
            max_latency_ms=max(latencies),
            mean_latency_ms=statistics.mean(latencies),
            median_latency_ms=statistics.median(latencies),
            p95_latency_ms=latencies[int(len(latencies) * 0.95)] if len(latencies) > 0 else 0.0,
            p99_latency_ms=latencies[int(len(latencies) * 0.99)] if len(latencies) > 0 else 0.0,
            std_dev_ms=statistics.stdev(latencies) if len(latencies) > 1 else 0.0,
            success_rate=len(successful_results) / len(results)
        )
    
    def print_stats(self, stats: EndpointStats):
        """Print formatted statistics"""
        print(f"\n{'='*60}")
        print(f"Endpoint: {stats.endpoint}")
        print(f"Method: {stats.method}")
        print(f"{'='*60}")
        print(f"Total Requests: {stats.total_requests}")
        print(f"Successful: {stats.successful_requests}")
        print(f"Failed: {stats.failed_requests}")
        print(f"Success Rate: {stats.success_rate:.2%}")
        print(f"{'='*60}")
        print(f"Latency Statistics (ms):")
        print(f"  Min:     {stats.min_latency_ms:8.2f}")
        print(f"  Max:     {stats.max_latency_ms:8.2f}")
        print(f"  Mean:    {stats.mean_latency_ms:8.2f}")
        print(f"  Median:  {stats.median_latency_ms:8.2f}")
        print(f"  95th %:  {stats.p95_latency_ms:8.2f}")
        print(f"  99th %:  {stats.p99_latency_ms:8.2f}")
        print(f"  Std Dev: {stats.std_dev_ms:8.2f}")
        print(f"{'='*60}")
    
    def print_summary(self, all_stats: List[EndpointStats]):
        """Print summary comparison of all endpoints"""
        print(f"\n{'='*80}")
        print(f"LATENCY SUMMARY COMPARISON")
        print(f"{'='*80}")
        print(f"{'Endpoint':<35} {'Method':<12} {'Mean (ms)':<10} {'P95 (ms)':<10} {'Success %':<10}")
        print(f"{'-'*80}")
        
        for stats in all_stats:
            endpoint_short = stats.endpoint.split('/')[-1] if '/' in stats.endpoint else stats.endpoint
            print(f"{endpoint_short:<35} {stats.method:<12} {stats.mean_latency_ms:<10.2f} "
                  f"{stats.p95_latency_ms:<10.2f} {stats.success_rate:<10.2%}")
        
        print(f"{'='*80}")

async def main():
    """Main function to run latency tests"""
    parser = argparse.ArgumentParser(description="Test Hyperliquid API latency")
    parser.add_argument("--testnet", action="store_true", help="Test testnet endpoints")
    parser.add_argument("--mainnet", action="store_true", help="Test mainnet endpoints")
    parser.add_argument("--iterations", type=int, default=20, help="Number of test iterations (default: 20)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds (default: 10)")
    
    args = parser.parse_args()
    
    # Default to testnet if no environment specified
    if not args.testnet and not args.mainnet:
        args.testnet = True
    
    print(f"Hyperliquid Latency Test")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Iterations: {args.iterations}")
    print(f"Timeout: {args.timeout}s")
    print(f"Environments: {'Testnet' if args.testnet else ''} {'Mainnet' if args.mainnet else ''}")
    
    tester = LatencyTester(timeout=args.timeout)
    all_stats = []
    
    # Define endpoints to test
    endpoints = []
    
    if args.testnet:
        endpoints.extend([
            (TESTNET_INFO_URL, "POST", "REST", {"type": "meta"}),
            (TESTNET_INFO_URL, "POST", "REST", {"type": "l2Book", "coin": "BTC"}),
            (TESTNET_WS_URL, None, "WEBSOCKET", None)
        ])
    
    if args.mainnet:
        endpoints.extend([
            (MAINNET_INFO_URL, "POST", "REST", {"type": "meta"}),
            (MAINNET_INFO_URL, "POST", "REST", {"type": "l2Book", "coin": "BTC"}),
            (MAINNET_WS_URL, None, "WEBSOCKET", None)
        ])
    

    
    # Test each endpoint
    for url, method, endpoint_type, data in endpoints:
        endpoint_desc = f"{endpoint_type} endpoint: {url}"
        if data:
            endpoint_desc += f" (data: {data})"
        print(f"\nTesting {endpoint_desc}")
        
        if endpoint_type == "REST":
            results = await tester.test_rest_endpoint(url, method, data, iterations=args.iterations)
        else:  # WEBSOCKET
            results = await tester.test_websocket_endpoint(url, iterations=args.iterations)
        
        stats = tester.calculate_stats(results)
        all_stats.append(stats)
        tester.print_stats(stats)
        
        # Print any errors
        failed_results = [r for r in results if not r.success]
        if failed_results:
            print(f"\nFailed requests:")
            for result in failed_results[:5]:  # Show first 5 errors
                print(f"  - {result.error}")
            if len(failed_results) > 5:
                print(f"  ... and {len(failed_results) - 5} more")
    
    # Print summary
    tester.print_summary(all_stats)
    
    # Save results to file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"latency_test_results_{timestamp}.json"
    
    results_data = {
        "timestamp": datetime.now().isoformat(),
        "parameters": {
            "iterations": args.iterations,
            "timeout": args.timeout,
            "testnet": args.testnet,
            "mainnet": args.mainnet
        },
        "endpoints": [
            {
                "endpoint": stats.endpoint,
                "method": stats.method,
                "total_requests": stats.total_requests,
                "successful_requests": stats.successful_requests,
                "failed_requests": stats.failed_requests,
                "success_rate": stats.success_rate,
                "min_latency_ms": stats.min_latency_ms,
                "max_latency_ms": stats.max_latency_ms,
                "mean_latency_ms": stats.mean_latency_ms,
                "median_latency_ms": stats.median_latency_ms,
                "p95_latency_ms": stats.p95_latency_ms,
                "p99_latency_ms": stats.p99_latency_ms,
                "std_dev_ms": stats.std_dev_ms
            }
            for stats in all_stats
        ]
    }
    
    with open(filename, 'w') as f:
        json.dump(results_data, f, indent=2)
    
    print(f"\nResults saved to: {filename}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
