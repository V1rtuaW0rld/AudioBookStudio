import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def find_book_file(book_dir):
    # Priority to chunked file
    for f in os.listdir(book_dir):
        if f.lower().endswith("_chunked.txt"):
            return os.path.join(book_dir, f)

    # Otherwise raw txt
    for f in os.listdir(book_dir):
        if f.lower().endswith(".txt") and not f.lower().startswith("seg"):
            return os.path.join(book_dir, f)

    return None


def split_book(project_name):
    project_dir = os.path.join(BASE_DIR, "Projects", project_name)
    book_dir = os.path.join(project_dir, "book")
    tts_dir = os.path.join(project_dir, "tts")

    os.makedirs(tts_dir, exist_ok=True)

    input_file = find_book_file(book_dir)
    if not input_file:
        print("ERROR: no txt file found in /book")
        sys.exit(1)

    print("[INFO] File detected:", input_file)

    # Clean old segments
    for f in os.listdir(tts_dir):
        if f.startswith("seg") and f.endswith(".json"):
            os.remove(os.path.join(tts_dir, f))

    print("[INFO] Splitting line by line...")

    seg_index = 1

    with open(input_file, "r", encoding="utf-8") as f:
        for raw in f.readlines():
            line = raw.strip()

            if not line:
                continue

            seg_data = {
                "profile_id": "Narrator",
                "text": line
            }

            seg_name = f"seg{str(seg_index).zfill(5)}.json"
            seg_path = os.path.join(tts_dir, seg_name)

            with open(seg_path, "w", encoding="utf-8") as out:
                json.dump(seg_data, out, ensure_ascii=False, indent=2)

            seg_index += 1

    print("[OK]", seg_index - 1, "segments generated in:", tts_dir)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python split_book.py \"Project Name\"")
        sys.exit(1)

    split_book(sys.argv[1])
