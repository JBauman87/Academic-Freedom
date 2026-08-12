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
        min_cluster_size= min_cluster_size, #3
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
                               min_topic_size= 3, #manually adjust
                               top_n_words=15, #manually adjust
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
    # Topic counts (number of docs in topic)
    topic_counts = topic_info["Count"]

    # Find unique topic indices
    unique_topics = list(set(topics))
    topic_nums = topic_info["Topic"]
    print("are they equal?", unique_topics, topic_nums)
    # remove -1 topic
    unique_topics.pop(0)

    # Collect info for each topic
    for topic_id in unique_topics:
        # *** Find Info ***
        # Topic count
        count = topic_counts[topic_id]
        # Topic words and weights
        words_weights = topic_model.get_topic(topic_id)
        words = []
        weights = []
        for word, weight in words_weights.items():
            words.append(word)
            weights.append(weight)
        # Indices of the documents in the topic
        indices = np.where(np.array(topics) == topic_id)[0]
        # Find the centroid of the topic
        centroid = embeddings[indices].mean(axis=0)
        centroid = centroid / np.linalg.norm(centroid) # normalize

        # *** Save Results ***
        # Organize topic results into a dictionary
        run_results['topics'][topic_id] = {
                                            "words": words,
                                            "weights": weights,
                                            "centroid": centroid,
                                            "count": count,
                                            "doc_indices": indices
                                            }

    # Save results in a .pkl file
    filename = "run"+str(counter)
    with open(r"C:/Users/jordanbauman/Library/CloudStorage/OneDrive-UniversityofWaterloo/'Academic Freedom RA'/Code/"
              r"Academic-Freedom/Short_Descriptions_Analysis/topic_results"+filename+".pkl", "wb") as f:
        pickle.dump(run_results, f)

