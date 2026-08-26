from pathlib import Path
import shutil
from collections import defaultdict

root = Path(
    "/Academic-Freedom/Case Documents"
)

output = Path(
    "/Academic-Freedom/PDF_Extractor/input_pdfs"
)

# Counter for each top-level folder
folder_counts = defaultdict(int)

for pdf_file in sorted(root.rglob("*.pdf")):

    # Path relative to root
    rel_path = pdf_file.relative_to(root)

    # First folder underneath "Case Documents"
    top_folder = rel_path.parts[0]

    # Increment that folder's counter
    folder_counts[top_folder] += 1

    # Create new filename
    new_name = f"{top_folder}_{folder_counts[top_folder]}.pdf"

    destination = output / new_name

    shutil.copy2(pdf_file, destination)

    print(f"{pdf_file} -> {new_name}")