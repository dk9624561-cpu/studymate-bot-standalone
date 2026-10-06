from bot.api import fetch_batches

def generate_txt():
    batches = fetch_batches()
    print(f"Total batches fetched: {len(batches)}")

    lines = [
        "=" * 90,
        " " * 26 + "KGS IAS BATCHES: NAME, DATE & BATCH ID",
        "=" * 90,
        f"TOTAL BATCHES: {len(batches)}",
        "-" * 90,
        f"{'BATCH ID':<12} | {'START DATE':<14} | {'BATCH NAME'}",
        "-" * 90
    ]

    for b in batches:
        b_id = str(b.get("id", "N/A"))
        start_date = str(b.get("start_at", "N/A") or "N/A")
        title = str(b.get("title", "N/A") or "N/A").strip()
        lines.append(f"{b_id:<12} | {start_date:<14} | {title}")

    lines.append("-" * 90)

    output_filename = "batches_info.txt"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Successfully generated {output_filename} with {len(batches)} batches.")

if __name__ == "__main__":
    generate_txt()
