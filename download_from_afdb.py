#!/usr/bin/env python3
import requests #to make HTTP requests to the API
import json #to work with the JSON data returned by the API
import os #to work with the file system

import csv
from pathlib import Path

from flask import Flask, render_template, request

def get_afdb_response(uniprot_id):
  """
  Fetches prediction data from the AlphaFold Database API for a given UniProt ID.

  Args:
    uniprot_id (str): The UniProt accession ID for the protein of interest. Example: "P12345"

  Returns:
    dict or str: A dictionary containing the API response (usually a list of prediction entries) if the request is successful (HTTP status code 200).
    Returns an error message string if the request fails.
  """

  AFDB_API_BASE_URL = "https://alphafold.ebi.ac.uk/api/prediction/"
  uniprot_id = uniprot_id.upper() # this will ensure that the UniProt ID is uppercase

  request_url = AFDB_API_BASE_URL + uniprot_id #construct the full URL by appending the base URL and the UniProt ID
  print(f"➡️ Sending request to: {request_url}") #to see the URL

  response = requests.get(request_url, timeout =10) #query the URL
  if response.status_code == 200: # if the request was successful (200 means OK)
    result = response.json()
    print(f"✅ Success! Received data for {uniprot_id}")
    return result

  else:
    error_message = f"❌ API request failed for {uniprot_id} with status: {response.status_code} - {response.reason}"
    print(error_message)
    if response.status_code == 404 or response.status_code == 400:
      print(f"ℹ️  Hint: A status code of 404/400 often means the UniProt ID '{uniprot_id}' is not available in the AlphaFold Database or the ID is incorrect.")
import re



def download_files(url_dictionary):
  destination_path = "/content/drive/MyDrive/AFDB_API_course_downloads"

  if not os.path.exists(destination_path): #if the folder doesn't exist
    os.makedirs(destination_path)
    print(f"✅ Directory created: {destination_path}")
  else:
    print(f"ℹ️ Directory already exists: {destination_path}")


  for file_type, url in url_dictionary.items():
    if not url: # Skip if a URL is empty or None
        print(f"🚫 Skipping {file_type} as URL is missing.")
        continue
    try:
      original_filename = url.split('/')[-1]
      file_extension = ""
      match = re.search(r'AF-([A-Z0-9]+)-F\d+', url)
      if match:
          uniprot_id = match.group(1)

      if '.' in original_filename:
          file_extension = "." + original_filename.split('.')[-1]

      if file_type == "cif":
          filename = f"{uniprot_id}_model{file_extension or '.cif'}"
      elif file_type == "pdb":
          filename = f"{uniprot_id}_model{file_extension or '.pdb'}"
      elif file_type == "pae_image":
          filename = f"{uniprot_id}_pae{file_extension or '.png'}"
      elif file_type == "alphamissense_tsv": # Assuming you used this key earlier
          filename = f"{uniprot_id}_alphamissense{file_extension or '.tsv'}" # Or .csv if it's a csv
      else:
          # Fallback for other types: use the file_type and original extension
          filename = f"{uniprot_id}_{file_type}{file_extension}"

      full_file_path = os.path.join(destination_path, filename)

      file_response = requests.get(url, stream=True, timeout=30)

      if file_response.status_code == 200:
        with open(full_file_path, 'wb') as f:
          for chunk in file_response.iter_content(chunk_size=8192): # Download in 8KB chunks
            f.write(chunk)
        print(f"  ✅ Successfully downloaded: {filename}")
      else:
        print(f"  ❌ Failed to download {file_type}. Status: {file_response.status_code} - {file_response.reason}")

    except requests.exceptions.RequestException as e:
      print(f"  🚫 Error downloading {file_type} from {url}: {e}")
    except Exception as e:
      print(f"  🛑 An unexpected error occurred with {file_type}: {e}")
    print("-" * 20) # Separator for each file download attempt

  print("="*30 + f"\n🏁 All attempted downloads for {uniprot_id} complete.")