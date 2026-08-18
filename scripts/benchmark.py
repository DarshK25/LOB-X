"""
benchmark.py — Run all benchmarks and print a summary table.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from benchmarks import matching_speed, latency, throughput, memory

if __name__ == "__main__":
    print("=" * 60)
    print("LOB-X Benchmark Suite")
    print("=" * 60)
    matching_speed.run()
    latency.run()
    throughput.run()
    memory.run()
    print("=" * 60)
