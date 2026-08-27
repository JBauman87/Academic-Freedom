# Import packages
from openai import OpenAI
from pathlib import Path
import re
import time
import os
from dotenv import load_dotenv

# Load variables from the .env file
load_dotenv()

# Access the masked key
groq_API_key = os.getenv('groq_API_key')
google_API_key = os.getenv('google_API_key')

# Base URLs
groq_url = "https://api.groq.com/openai/v1"
google_url = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Rate limits
#Groq
groq_target_rpm = 30
# Calculate reciprocal delay (60 seconds / 30 requests = 2 seconds per request)
groq_request_interval = 60.0 / groq_target_rpm
#Google AI Studio
google_target_rpm = 2
# Calculate reciprocal delay (60 seconds / 30 requests = 2 seconds per request)
google_request_interval = 60.0 / google_target_rpm

# Models
llama = "llama-3.1-8b-instant"
gemini = "gemini-3-flash-preview"

# Model Dictionary
models = {
      # "llama":
      #         {"name": llama,
      #          "url": groq_url,
      #          "key": groq_API_key,
      #          "request_interval": groq_request_interval,
      #          },
          "gemini":
              {"name": gemini,
               "url": google_url,
               "key": google_API_key,
               "request_interval": google_request_interval,
               }
          }

print("Script started")

root = Path(
    """/Users/jordanbauman/Library/CloudStorage/OneDrive-UniversityofWaterloo/Academic Freedom RA/Code/Academic-Freedom/PDF_Extractor/output_text"""
)
target_dir = Path(
    """/Users/jordanbauman/Library/CloudStorage/OneDrive-UniversityofWaterloo/Academic Freedom RA/Code/Academic-Freedom/Summary_Analysis/AI Summaries"""
)

# Prompt
prompt = ("""You are a document summarizer that reads through several documents and produces a unified summary of them.
You will be given several documents that describe the same set of events from different angles and to varying degrees. 
Your job is to piece together what is going on and to produce a summary that efficiently describes the events contained
in the documents with one narrative. Do not try to interpret the events yourself. Just state the narrative. Include 
implications and interpretation that is found in the documents themselves – not your own interpretations and judgments.
Your summary must be no less than 500 words and no more than 1500 words. Those are strict limits.""")

# Assemble dictionary of .txt files

text_dict = {}

# iterate over text files in the directory
for txt_file in sorted(root.rglob("*.txt")):

    # Path relative to root
    rel_path = txt_file.relative_to(root)

    # First folder underneath "Case Documents"
    top_folder = rel_path.parts[0]

    # Remove ending of .txt file name to make generic
    name = re.split(r"_\d+.txt" ,top_folder)[0]

    # Add name of case if not already in dictionary
    if name not in text_dict.keys():
        text_dict[name] = {}

    # read current text file and add to dictionary
    with open(txt_file, "r", encoding='utf-8') as f:
        lines = f.read()
        text_dict[name][top_folder] = lines


# AI call and write

# iterate over cases in the dictionary
for case in text_dict.keys():

    filename = target_dir / f"{case}.txt"

    # Writing to a .txt file
    with open(filename, "w", encoding="utf-8") as f:

        # Iterate over AI models (just using Gemini)
        for model_id, (model_name, model_details) in enumerate(models.items()):
            
            # record start time
            start_time = time.time()

            # Load Inference Client
            client = OpenAI(
                api_key=model_details["key"],
                base_url=model_details["url"],
            )
            
            # Give system prompt
            messages = [{"role": "system",
                         "content": prompt}]

            content_lst = []

            for doc_name, doc_text in text_dict[case].items():
                content_lst.append({
                    "type": "text",
                    "text": doc_text,
                })

            # Give user input
            messages.append({
                "role": "user",
                "content": content_lst
            })

            #Extract response
            completion = client.chat.completions.create(
                model=model_details["name"],
                messages=messages,
                temperature=1.0,  # set as desired
            )

            answer = completion.choices[0].message.content

            print("answer given")

            # Write to file
            f.write(answer)

            # Calculate how long the API call took and sleep for the remaining window
            elapsed = time.time() - start_time
            print(elapsed)
            sleep_time = max(0.0, model_details["request_interval"] - elapsed)
            time.sleep(sleep_time)
