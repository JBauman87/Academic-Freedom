# Import packages
import pandas as pd
import numpy as np
from bertopic import BERTopic
from sentence_transformers import SentenceTransformer
from hdbscan import HDBSCAN
from umap import UMAP
from bertopic.representation import KeyBERTInspired
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
from itertools import product
from gensim.models import CoherenceModel
from gensim.corpora import Dictionary
import argparse
from pathlib import Path


# Usage
## For optimizing
# python BERTopic_seed_analysis.py optimize
## For seed stability analysis
# python BERTopic_seed_analysis.py stability

# Constants
SEEDS = [42,12,43,28,46,76,100,92,68,35,66] #reference seed first

# Custom stopwords for exclusion (in most documents, not salient)
CUSTOM_STOPWORDS = {
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

# Merge to form complete list of stopwords
ALL_STOPWORDS = list(ENGLISH_STOP_WORDS.union(CUSTOM_STOPWORDS))

# ********** BERTopic **********

# A function for one BERTopic run
def bertopic(documents: list[str], embeddings:np.ndarray, seed: int, min_cluster_size: int,  n_neighbors: int,n_components: int):
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

    # Instantiate vectorizer with all stopwords
    vectorizer_model = CountVectorizer(
        stop_words=ALL_STOPWORDS,
        ngram_range=(1, 2),
        min_df=2 #word must appear in X documents, set manually
    )

    # Configure the topic model
    topic_model = BERTopic(embedding_model=None, # Done outside bootstrapping loop
                               hdbscan_model=hdbscan_model,
                               umap_model=umap_model,
                               vectorizer_model=vectorizer_model,
                               representation_model=representation_model,
                               top_n_words=10, #manually adjust
                               calculate_probabilities=False)

    # Run the topic model
    topics, probs = topic_model.fit_transform(documents, embeddings)

    #****** Results ******

    # Initialize dictionary of results
    run_results = {
        "seed": seed,
        "parameters": {
            "min_cluster_size": min_cluster_size,
            "n_neighbors": n_neighbors,
            "n_components": n_components,
        },
        "n_documents": len(documents),
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
def run_seed_analysis(documents: list[str], embeddings:np.ndarray, seeds: list[int], min_cluster_size: int,  n_neighbors: int,n_components: int):
    # Instantiate a dictionary for the results of each seed run
    results = {}
    # Iterate over the seeds
    for seed in seeds:
        # Run BERTopic for the current seed
        run_result = bertopic(documents, embeddings, seed, min_cluster_size, n_neighbors, n_components)
        # Save the results in the dictionary
        results[seed] = run_result
    
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
    
    # sort df so that reference topics appear in order and most stable words appear first
    word_df = word_df.sort_values(
        ["reference_topic", "seed_proportion"], ascending=[True, False]
    ).reset_index(drop=True)

    return word_results, word_df


# ********* Parameter Optimization **********

# a helper function to collect all the valid tokens in the all the documents
def coherence_tokens(documents, stopwords):

    # Instantiate vectorizer
    vectorizer = CountVectorizer(
        stop_words=stopwords,
        ngram_range=(1, 2),
        min_df=2
    )

    # Fit the vectorizer to the documents
    vectorizer.fit(documents)

    # instantiate an analyzer from the vectorizer
    analyzer = vectorizer.build_analyzer()

    # create valid vocabulary set
    vocabulary = set(
        vectorizer.get_feature_names_out()
    )

    # instantiate list for the valid tokens in the documents
    tokens = []

    # iterate over the documents
    for doc in documents:

        # collect all the tokens in the current doc
        doc_tokens = analyzer(doc)

        # pull out the valid tokens from the token list
        valid_tokens = [
            token
            for token in doc_tokens
            if token in vocabulary
        ]

        # add the valid tokens to the overall valid tokens list
        tokens.append(valid_tokens)

    return tokens

# a function to sample outputs with one seed and various combinations of min_cluster_sizes and n_neighbors_values
def optimize_parameters(
    documents,
    embeddings,
    min_cluster_sizes,
    n_neighbors_values,
    n_components=5,
    seed=42
):

    # tokenize documents for input to the coherence calculation
    tokenized_docs = coherence_tokens(
        documents,
        ALL_STOPWORDS
    )

    # instantiate a dictionary of the tokens
    dictionary = Dictionary(tokenized_docs)

    # instantiate an iterator over the grid of all possible hyperparameter combinations
    parameter_grid = product(
        min_cluster_sizes,
        n_neighbors_values
    )

    # instantiate a list to hold the results of each run for each hyperparameter combination
    optimization_results = []

    # iterate over the hyperparameter combinations
    for min_size, neighbors in parameter_grid:

        print(
            f"Testing cluster={min_size}, "
            f"neighbors={neighbors}"
        )

        # collect BERTopic result
        run_result = bertopic(
            documents,
            embeddings,
            seed,
            min_size,
            neighbors,
            n_components
        )

        # Instantiate a list to hold valid representative topic words
        topic_words = []

        # Iterate over topics produced by the BERTopic run
        for topic in run_result["topics"]:

            # collect representative words for the current topic
            words = run_result["topics"][topic]["representative_words"]

            # find valid words for the current topic by matching against tokenized words
            valid_words = [
                word for word in words
                if word in dictionary.token2id
            ]

            # If there are enough valid words in the topic (>=2 for coherence) add them to the list of topic words
            if len(valid_words) >= 2:
                topic_words.append(valid_words)

        # run the coherence model
        coherence_model = CoherenceModel(
            topics=topic_words,
            texts=tokenized_docs,
            dictionary=dictionary,
            coherence="c_v",
            processes=1
        )

        # retrieve results of the coherence model
        coherence = coherence_model.get_coherence()

        # Calculate the proportion of outliers in the BERTopic run
        outlier_prop = (run_result["n_outliers"]/run_result["n_documents"])

        # append the hyperparameters and results to the output list
        optimization_results.append({
            "min_cluster_size": min_size,
            "n_neighbors": neighbors,
            "n_components": n_components,
            "n_topics": run_result["n_topics"],
            "n_outliers": run_result["n_outliers"],
            "outlier_proportion": outlier_prop,
            "coherence_cv": coherence
        })

    # convert output list to a df
    optimization_df = pd.DataFrame(
        optimization_results
    )

    # sort the output df by coherence
    optimization_df = optimization_df.sort_values(
        "coherence_cv",
        ascending=False
    ).reset_index(drop=True)

    return optimization_df


# ******** Execution *********

if __name__ == "__main__":

    # instantiate argument parser for command line options
    parser = argparse.ArgumentParser()

    # specify command line arguments
    parser.add_argument(
        "mode",
        choices=["optimize", "stability"],
        help="Choose which analysis to run",
    )

    args = parser.parse_args()

    # Import input .txt files
    
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

    # Embed documents
    EMBEDDINGS = embedding_model.encode(
        DOCUMENTS, show_progress_bar=True, normalize_embeddings=True
    )

    # ************ Optimizing hyperparameters **********
    if args.mode == "optimize":
        print("Starting parameter optimization...")

        OPTIMIZATION_DF = optimize_parameters(
            DOCUMENTS,
            EMBEDDINGS,
            min_cluster_sizes=[8, 12, 16, 20], #CHANGE HERE
            n_neighbors_values=[10, 15], #CHANGE HERE
            n_components=5,
            seed=42,
        )

        OPTIMIZATION_DF.to_excel("parameter_optimization.xlsx", index=False)

        print("Parameter optimization complete.")
    
    # *************** Seed sensitivity analysis ***************
    elif args.mode == "stability":

        print("Starting seed sensitivity analysis...")

        # Run BERTopic across seeds
        RESULTS = run_seed_analysis(
            DOCUMENTS,
            EMBEDDINGS,
            SEEDS,
            min_cluster_size=12, #CHANGE HERE
            n_neighbors=10, #CHANGE HERE
            n_components=5 #CHANGE HERE
        )

        print("Matching topics across runs...")

        # Match topics across seeds
        ALL_COMPARISONS, COMPARISON_DF = topics_compare(
            RESULTS,
            SEEDS
        )

        print("Calculating representative word stability...")

        # Calculate stable representative words
        WORD_RESULTS, WORD_DF = stable_rep_words(
            RESULTS,
            ALL_COMPARISONS,
            SEEDS
        )

        print("Analysis complete.")

        with pd.ExcelWriter(
            "seed_stability_analysis.xlsx",
            engine="openpyxl"
        ) as writer:

            COMPARISON_DF.to_excel(
                writer,
                sheet_name="Topic Comparisons",
                index=False
            )

            WORD_DF.to_excel(
                writer,
                sheet_name="Word Stability",
                index=False
            )

        print("Results saved.")
