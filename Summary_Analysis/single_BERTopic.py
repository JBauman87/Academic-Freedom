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
folder = Path("""/Users/jordanbauman/Library/CloudStorage/OneDrive-UniversityofWaterloo/Academic Freedom RA/Code/Academic-Freedom/Summary_Analysis/AI Summaries""")

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

# Initialize Embeddings
EMBEDDINGS = embedding_model.encode(
    DOCUMENTS,
    show_progress_bar=True,
    normalize_embeddings=True
)

# Instantiate clustering model
hdbscan_model = HDBSCAN(
    min_cluster_size=3, #CHANGE HERE
    min_samples=2,
    metric='euclidean',
    cluster_selection_method='eom'
)

# Instantiate dimensionality reduction model
umap_model = UMAP(
    n_neighbors=10, #CHANGE HERE
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
    "did",
    "2018",
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
    "position",
    "said",
    "freedom",
    "york",
    "ubc",
    "st",
    "said",
    "duval",
    "lieutenant",
    "duval-lieutenant",
    "noble",
    "dalhousie",
    "Amir",
    "Attaran",
    "Andrew",
    "Potter",
    "Anthony",
    "Hall",
    "Carlton",
    "David",
    "Lesbarreres",
    "Derek",
    "Pyne",
    "Donald",
    "Welsh",
    "Dougal",
    "MacDonald",
    "Ana",
    "Isla",
    "Chad",
    "Thompson",
    "Cheryl",
    "Gosselin",
    "Healy",
    "Noble",
    "Frances",
    "Widdowson",
    "Gabrielle",
    "Horne",
    "George",
    "Nader",
    "Harry",
    "Crowe",
    "Joe",
    "Arvai",
    "Kathleen",
    "Lowry",
    "Mary",
    "Bryson",
    "Michael",
    "Persinger",
    "Nancy",
    "Olivieri",
    "Norman",
    "Strax",
    "Philippe",
    "Tortell",
    "Rick",
    "Mehta",
    "Robert",
    "Buckingham",
    "Stéphane",
    "McLachlan",
    "Ian",
    "Mauro",
    "Steven",
    "Lukits",
    "Valentina",
    "Azarova",
    "Anne",
    "Duffy",
    "Paul",
    "Grof",
    "Collette",
    "Parent",
    "Christine",
    "Bruckert",
    "Francis",
    "Christian",
    "Gábor",
    "Lukács",
    "John",
    "Sherman",
    "Marlene",
    "Webber",
    "McMaster",
    "Patrick",
    "Provost",
    "Paul",
    "Finlayson",
    "Ricardo",
    "Duchesne",
    "Rick",
    "Coupland",
    "Rima",
    "Azar",
    "Simon",
    "Fraser",
    "Stephane",
    "Serafin",
    "Verushka",
    "Lieutenant",
    "Duval",
    "Lieutenant-Duval"
}

all_stopwords = list(ENGLISH_STOP_WORDS.union(custom_stopwords))

# Instantiate vectorizer with all stopwords
vectorizer_model = CountVectorizer(
    stop_words=all_stopwords,
    ngram_range=(1, 2),
    min_df=2
)

# Configure the topic model
topic_model = BERTopic(embedding_model=None,
                           hdbscan_model=hdbscan_model,
                           umap_model=umap_model,
                           vectorizer_model=vectorizer_model,
                           representation_model=representation_model,
                           top_n_words=10)

# Run the topic model
topics, probs = topic_model.fit_transform(
    DOCUMENTS,
    EMBEDDINGS
)

# Visualize Embeddings
fig = topic_model.visualize_documents(
    DOCUMENTS,
    embeddings=EMBEDDINGS
)
fig.show()

# Retrieve topic modelling results
topic_info = topic_model.get_topic_info()
doc_info = topic_model.get_document_info(DOCUMENTS, metadata={"Employee Name": FILENAMES})

# Save results to Excel file
with pd.ExcelWriter("single_BERTopic_results_AI_summary.xlsx") as writer:
    topic_info.to_excel(writer, sheet_name="Topics", index=False)
    doc_info.to_excel(writer, sheet_name="Documents", index=False)
