import argparse


def main():
    parser = argparse.ArgumentParser(description="RollForge worker")
    parser.add_argument("--check", action="store_true", help="Validate scaffold imports and exit")
    args = parser.parse_args()
    if args.check:
        print("rollforge_worker: scaffold ready; execution is not implemented")
        return
    parser.error("Execution is not implemented. Complete the S0 compatibility spike first.")


if __name__ == "__main__":
    main()
