from evaluator import evaluate_bid
from excel_io import load_tender, load_bids, write_results

def run_excel_evaluation(path: str):

    print("Loading tender...")
    tender = load_tender(path)

    print("Loading bids...")
    bids = load_bids(path)

    if not bids:
        raise ValueError("No bids found in Excel file.")

    evaluations = []

    for bid in bids:
        print(f"Evaluating {bid.company_name}...")
        ev = evaluate_bid(tender, bid)
        evaluations.append(ev)

    write_results(path, evaluations)

    print("\n✓ Evaluation complete.")
    print("✓ Results written to 'Results' sheet.")


if __name__ == "__main__":
    file_path = input("Enter Excel file path: ").strip()
    run_excel_evaluation(file_path)