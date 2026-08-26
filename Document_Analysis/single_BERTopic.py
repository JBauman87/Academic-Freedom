# Import packages
import pandas as pd
from bertopic import BERTopic
from sentence_transformers import SentenceTransformer
from hdbscan import HDBSCAN
from umap import UMAP
from bertopic.representation import KeyBERTInspired
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
from pathlib import Path

# Source folder
folder = Path("""/Users/jordanbauman/Library/CloudStorage/OneDrive-UniversityofWaterloo/Academic Freedom RA/Code/Academic-Freedom/PDF_Extractor/output_text""")

# list holding documents
DOCUMENTS = []
FILENAMES = []

# iterate over .txt files in source folder
for file in sorted(folder.glob("*.txt")):
    # read in current file
    with open(file, "r", encoding="utf-8") as f:
        text = f.read()
    # append document to the list of documents
    DOCUMENTS.append(text)
    FILENAMES.append(file.name)

print(f"Loaded {len(DOCUMENTS)} documents.")

# Initialize Models
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

# Instantiate clustering model
hdbscan_model = HDBSCAN(
    min_cluster_size=12,
    min_samples=2,
    metric='euclidean',
    cluster_selection_method='eom'
)

# Instantiate dimensionality reduction model
umap_model = UMAP(
    n_neighbors=10,
    n_components=5,
    min_dist=0.1,
    metric='cosine',
    random_state=42
)

# Instantiate representation model. Extracts keywords
representation_model= None #KeyBERTInspired()

# Define custom stopwords for exclusion (in most documents, not salient)
custom_stopwords = {
    "professor",
    "professors",
    "dr",
    "university",
    "universities",
    "faculty",
    "academic",
    "student",
    "students",
    "department",
    "college",
    "campus",
    "research",
    "case",
    "made",
    "comments",
    "regarding",
    "following",
    "position"
}

all_stopwords = list(ENGLISH_STOP_WORDS.union(custom_stopwords))

# Instantiate vectorizer with all stopwords
vectorizer_model = CountVectorizer(
    stop_words=all_stopwords,
    ngram_range=(1, 2),
    min_df=2
)

# Configure the topic model
topic_model = BERTopic(embedding_model=embedding_model,
                           hdbscan_model=hdbscan_model,
                           umap_model=umap_model,
                           vectorizer_model=vectorizer_model,
                           representation_model=representation_model,
                           top_n_words=10)

# Run the topic model
topics, probs = topic_model.fit_transform(DOCUMENTS)

# Visualize Embeddings
fig = topic_model.visualize_documents(DOCUMENTS)
fig.show()

# Retrieve topic modelling results
topic_info = topic_model.get_topic_info()
doc_info = topic_model.get_document_info(DOCUMENTS, metadata={"Employee Name": FILENAMES})

# Save results to Excel file
with pd.ExcelWriter("single_BERTopic_results.xlsx") as writer:
    topic_info.to_excel(writer, sheet_name="Topics", index=False)
    doc_info.to_excel(writer, sheet_name="Documents", index=False)
