"""Runs the full ProcessDataQuality pipeline end to end."""
from simulate_batch_records import main as simulate_main
from validate_batch_records import main as validate_main


def main():
    print("=== Step 1: Simulating batch records + audit trail ===")
    simulate_main()
    print()
    print("=== Step 2: Validating batch records / GMP compliance ===")
    validate_main()


if __name__ == "__main__":
    main()
