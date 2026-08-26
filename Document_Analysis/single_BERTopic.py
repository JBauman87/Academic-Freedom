# Import packages
import pandas as pd
from bertopic import BERTopic
from sentence_transformers import SentenceTransformer
from hdbscan import HDBSCAN
from umap import UMAP
from bertopic.representation import KeyBERTInspired
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

# Read in Excel file
file_path = 'af_coding.xlsx'
df = pd.read_excel(file_path, sheet_name="Cases")

## Retrieve descriptions
descriptions = df["Description of case"]
descriptions = descriptions.to_list()
descriptions.pop(-1) #temporary (removing an empty description)

## Retrieve employee names (labels), kept aligned with descriptions above
employee_names = df["EMPLOYEE NAME"]
employee_names = employee_names.to_list()
employee_names.pop(-1) #temporary (removing the label for the empty description)

# Initialize Models
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

# Instantiate clustering model
hdbscan_model = HDBSCAN(
    min_cluster_size=3,
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
                           min_topic_size=3,
                           top_n_words=5)

# Run the topic model
topics, probs = topic_model.fit_transform(descriptions)

# Visualize Embeddings
fig = topic_model.visualize_documents(descriptions)
fig.show()

# Retrieve topic modelling results
topic_info = topic_model.get_topic_info()
doc_info = topic_model.get_document_info(descriptions, metadata={"Employee Name": employee_names})

# Save results to Excel file
with pd.ExcelWriter("BERTopic_results_3.xlsx") as writer:
    topic_info.to_excel(writer, sheet_name="Topics", index=False)
    doc_info.to_excel(writer, sheet_name="Documents", index=False)
