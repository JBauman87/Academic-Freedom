# Import packages
import pandas as pd
import pickle as pkl
import numpy as np
from bertopic import BERTopic
from sentence_transformers import SentenceTransformer
from hdbscan import HDBSCAN
from umap import UMAP
from bertopic.representation import KeyBERTInspired
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

# Constants
seeds = [42,12,43,28,46,76,100,92,68,35,66] #reference seed first

## Read in Excel file
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

# ********** BERTopic **********

# A function for one BERTopic run
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
        run_results["topics"][topic_id] = {
            "doc_count": count,
            "representative_words": words,
            "doc_indices": topic_docs
        }

    return run_results

# A function for running BERTopic by iterating over the seeds
def run_seed_analysis(descriptions: list[str], embeddings:np.ndarray, seeds: list[int], min_cluster_size: int,  n_neighbors: int,n_components: int):
    # Instantiate a dictionary for the results of each seed run
    results = {}
    # Iterate over the seeds
    for seed in seeds:
        # Run BERTopic for the current seed
        run_result = bertopic(descriptions, embeddings, seed, min_cluster_size, n_neighbors, n_components)
        # Save the results in the dictionary
        results[seed] = run_result

    with open("seed_stability_results.pkl", "wb") as f:
        pkl.dump(results, f)
    
    return results

# define jaccard similarity helper function
def jaccard(words1: list, words2: list):

    words1 = set(words1)
    words2 = set(words2)

    intersection = words1.intersection(words2)
    union = words1.union(words2)

    return len(intersection) / len(union)

# A function for comparing all the topics of one seed against one topic of a reference seed
def docs_compare(reference_seed: int, comparison_seed: int, reference_topic: int, results: dict):
    # collect the indices of the docs in the reference topic of the reference seed
    reference_docs = set(results[reference_seed]["topics"][reference_topic]["doc_indices"])
    # initialize a dictionary to hold the number of common docs for each comparison
    comparisons = {}
    # loop over the topics in the comparison seed
    for topic in results[comparison_seed]["topics"]:
        # retrieve the doc indices for the current comparison topic
        comparison_docs = set(results[comparison_seed]["topics"][topic]["doc_indices"])
        # find the common docs between the topics
        common_docs = comparison_docs.intersection(reference_docs)
        # add the number of common docs to the output dictionary
        comparisons[topic] = len(common_docs)

    # find the topic that shares the most docs with the reference topic
    best_match = max(comparisons, key=comparisons.get)
    # extract shared doc count
    best_overlap = comparisons[best_match]
    # Calculate preservation ratio = # overlapping docs / # docs in reference topic
    preservation = best_overlap/(results[reference_seed]["topics"][reference_topic]["doc_count"])
    # retrieve representative words for the reference topic
    reference_words = results[reference_seed]["topics"][reference_topic]["representative_words"]
    # retrieve representative words for the best identified comparison topic
    comparison_words = results[comparison_seed]["topics"][best_match]["representative_words"]
    # Calculate jaccard similarity between representative words
    word_jaccard = jaccard(reference_words, comparison_words)

    # initialize results dictionary
    comparison = {
        "best_match": best_match, # best matching topic in the comparison seed
        "best_overlap": best_overlap, # number of overlapping topics
        "preservation": preservation,
        "word_jaccard": word_jaccard
    }
    return comparison

# a function that compares each seed run to the reference seed run and finds the best topic similarities for
# each seed comparison
def topics_compare(results: dict, seeds: list[int]):
    # define reference seed
    reference_seed = seeds[0]
    # retrieve the topic indices for the reference seed
    reference_topics = results[reference_seed]["topics"]

    # initialize dict for all topic comparisons
    all_comparisons = {}

    # iterate over comparison seeds
    for seed in seeds[1:]:
        # initialize dictionary entry for seed
        all_comparisons[seed] = {}
        # iterate over reference seed topics
        for topic in reference_topics:
            # run a comparison between the reference and comparison seeds for the current topic
            single_comparison = docs_compare(reference_seed, seed, topic, results)
            # append results to dict
            all_comparisons[seed][topic] = single_comparison

    # convert all_comparisons dictionary to a df
    comparison_df = pd.concat(
        {
            seed: pd.DataFrame.from_dict(topics, orient="index")
            for seed, topics in all_comparisons.items()
        },
        names=["comparison_seed", "reference_topic"],
    ).reset_index()

    return all_comparisons, comparison_df

# a function that compiles a dictionary of words and their frequencies that appears under each reference topic
def stable_rep_words(results: dict, all_comparisons: dict, seeds: list[int]):
    # retrieve reference seed
    reference_seed = seeds[0]
    # retrieve refence topics
    reference_topics = results[reference_seed]["topics"]

    # initialize a dictionary to contain the representative words for each refence topic
    word_results = {}

    # iterate over all the seeds
    for seed in results.keys():
        # iterate over the reference seed topics
        for topic in reference_topics.keys():
            # create entry for the current topic if necessary
            if topic not in word_results:
                word_results[topic] = {}
            # find the representative words of best matching topic if on a comparison seed
            if seed != reference_seed:
                # best matching topics
                comparison_topic = all_comparisons[seed][topic]["best_match"]
                # representative words
                words = results[seed]["topics"][comparison_topic]["representative_words"]
            # collect representative words if on reference seed
            else:
                words = results[seed]["topics"][topic]["representative_words"]
            # iterate over representative words
            for word in words:
                # increase word count if the word is recorded under the current topic
                if word in word_results[topic].keys():
                    word_results[topic][word] += 1
                # add word to dict if the word is not recorded under the current topic
                else:
                    word_results[topic][word] = 1

    # iterate over topics in results dictionary
    for topic in word_results.keys():
        # iterate over words in the current topic
        for word in word_results[topic].keys():
            # grab word count
            count = word_results[topic][word]
            # transform each entry into a list containing the word count and proportion of seeds it appear under
            word_results[topic][word] = [count, count/len(seeds)]

    # save word_results as a df
    word_df = pd.concat(
        {
            topic: pd.DataFrame.from_dict(
                words, orient="index", columns=["seed_count", "seed_proportion"]
            )
            for topic, words in word_results.items()
        },
        names=["reference_topic", "word"],
    ).reset_index()
    
    # sort df so that most stable result appear first
    word_df = word_df.sort_values(
        ["reference_topic", "seed_proportion"], ascending=[True, False]
    ).reset_index(drop=True)

    return word_results, word_df
