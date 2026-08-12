# Import packages
import pandas as pd
import numpy as np
import pickle
from bertopic import BERTopic
from sentence_transformers import SentenceTransformer
from hdbscan import HDBSCAN
from umap import UMAP
from bertopic.representation import KeyBERTInspired
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

# Constants
seeds = [42,12,43,28,46,76,100,92,68,35,66]

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

# Embed descriptions
embeddings = embedding_model.encode(
    descriptions,
    show_progress_bar=True,
    normalize_embeddings=True
)

# ********** BERTopic Loop **********

def bertopic(descriptions: list[str], embeddings:np.ndarray, seed: int, min_cluster_size: int,  n_neighbors: int,n_components: int):
    #****** Topic Modelling ******

    # Instantiate clustering model
    hdbscan_model = HDBSCAN(
        min_cluster_size = min_cluster_size, #3
        min_samples = 2,
        metric='euclidean',
        cluster_selection_method='eom'
    )

    # Instantiate dimensionality reduction model
    umap_model = UMAP(
        n_neighbors= n_neighbors, #10
        n_components= n_components, #5
        min_dist=0.1,
        metric='cosine',
        random_state=seed
    )

    # Instantiate representation model. Extracts keywords
    representation_model = None #KeyBERTInspired()

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

    # Merge to form complete list of stopwords
    all_stopwords = list(ENGLISH_STOP_WORDS.union(custom_stopwords))

    # Instantiate vectorizer with all stopwords
    vectorizer_model = CountVectorizer(
        stop_words=all_stopwords,
        ngram_range=(1, 2),
        min_df=2 #word must appear in X documents, set manually
    )

    # Configure the topic model
    topic_model = BERTopic(embedding_model=None, # Done outside bootstrapping loop
                               hdbscan_model=hdbscan_model,
                               umap_model=umap_model,
                               vectorizer_model=vectorizer_model,
                               representation_model=representation_model,
                               top_n_words=5, #manually adjust
                               calculate_probabilities=False)

    # Run the topic model
    topics, probs = topic_model.fit_transform(descriptions, embeddings)

    #****** Results ******

    # Initialize dictionary of results
    run_results = {
        "seed": seed,
        "parameters": {
            "min_cluster_size": min_cluster_size,
            "n_neighbors": n_neighbors,
            "n_components": n_components,
        },
        "n_documents": len(descriptions),
        "topics": {},
    }

    # Retrieve topic info
    topic_info = topic_model.get_topic_info()
    topic_info = topic_info[topic_info["Topic"] != -1].reset_index(drop=True)

    # Collect number of topics and add to dictionary
    topic_indices = topic_info["Topic"]
    n_topics = len(topic_indices)
    run_results["n_topics"] = n_topics

    # Save topic document assignments to dictionary
    run_results["document_topics"] = [int(t) for t in topics]

    # Collect number of outliers and add to dictionary
    n_outliers = int(np.sum(np.asarray(topics) == -1))
    run_results["n_outliers"] = n_outliers

    # Prepare lists for iteration

    ## Collect topic counts (number of docs in topic)
    topic_counts = topic_info["Count"]
    topic_words = topic_info["Representation"]

    # Iterate over each topic
    for topic_id in range(n_topics):
        # Number of docs in topic
        count = topic_counts[topic_id].item() # convert from np.int64 to int
        # Representative words for topic
        words = topic_words[topic_id]
        # Indices of docs in topic
        topic_docs = []
        for t in range(len(topics)):
            if topics[t] == topic_id:
                topic_docs.append(t)
        # Save info to dictionary entry
        run_results["topics"][str(topic_id)] = {
            "doc_count": count,
            "representative_words": words,
            "doc_indices": topic_docs
        }

    return run_results


print(bertopic(descriptions, embeddings, 42,3,10,5))

