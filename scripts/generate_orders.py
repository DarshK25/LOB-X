"""
generate_orders.py — Generate a CSV of random orders for testing/replay.

Usage:
    python scripts/generate_orders.py --n 10000 --out data/orders.csv
"""
import argparse
import csv
import random
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n",    type=int, default=10_000)
    parser.add_argument("--mid",  type=int, default=10_000)
    parser.add_argument("--out",  type=str, default="data/orders.csv")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    import os; os.makedirs(os.path.dirname(args.out), exist_ok=True)

    with open(args.out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["order_id", "price", "qty", "is_buy", "tif"])
        for i in range(args.n):
            price  = args.mid + rng.randint(-50, 50)
            qty    = rng.randint(1, 20)
            is_buy = rng.random() < 0.5
            writer.writerow([i + 1, price, qty, int(is_buy), "GTC"])

    print(f"Generated {args.n} orders → {args.out}")


if __name__ == "__main__":
    main()
