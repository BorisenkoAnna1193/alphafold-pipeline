#!/usr/bin/env python3
"""


get_afdb_response(uniprot_id)  — один запрос к API AlphaFold по accession,
                                 возвращает список записей (моделей)
collect_urls(entry)            — из записи собрать словарь «тип файла → ссылка»
download_files(url_dictionary) — скачать все ссылки из словаря по кусочкам
                                 в папку, файлы вида P12345_model.cif
"""

from __future__ import annotations

import csv
import os  # to work with the file system
import re
from pathlib import Path

import requests  # to make HTTP requests to the API
from flask import Flask, render_template, request
from requests.adapters import HTTPAdapter, Retry

from tracker import Tracker

AFDB_API = "https://alphafold.ebi.ac.uk/api/prediction/{acc}"
UNIPROT_API = "https://rest.uniprot.org"


BASE_DIR = Path(__file__).resolve().parent
REFS_DIR = BASE_DIR / "uniprot_dicts"
OUT_DIR = BASE_DIR / "downloads"

DB_PATH = OUT_DIR / "downloads.db"

UNIPROT_SEARCH = "https://rest.uniprot.org/uniprotkb/search"
AFDB_API_BASE_URL = "https://alphafold.ebi.ac.uk/api/prediction/"

FILTERS = {
    "protein_name": "Название белка",
    "gene": "Ген",
    "organism": "Организм (ID или латинское название)",
    "keyword": "Ключевое слово (только для человека)",
}

# Типы файлов для скачивания - из коллаба
FILE_TYPES = {
    "cif": ("cifUrl", "cif структура (кристаллографический формат)"),
    "pdb": ("pdbUrl", "PDB структура (текстовый формат)"),
    "pae_image": ("paeImageUrl", "PAE image (предсказанная ошибка выравнивания)"),
    "alphamissense_tsv": (
        "amAnnotationsUrl",
        "AlphaMissense annotations (только для человека)",
    ),
}

FORMATS = {key: (None, None, title) for key, (_, title) in FILE_TYPES.items()}
FORMATS["all"] = (
    None,
    None,
    "все доступные файлы ",
)  # такая возможность есть только в коллабе, в вебе не нужна ???

UNIPROT_COLUMNS = "accession,protein_name,gene_primary,organism_name,keyword,length"
LIMIT = 50


app = Flask(__name__)


#  Поиск ID через UniProt — uniprot.org/help/api_queries


def build_query(chosen: str, value: str) -> str:
    quoted = f'"{value}"' if " " in value else value
    if chosen == "protein_name":
        condition = f"protein_name:{quoted}"
    elif chosen == "gene":
        condition = f"gene:{quoted}"
    elif chosen == "organism":
        condition = (
            f"organism_id:{value}" if value.isdigit() else f"organism_name:{quoted}"
        )
    else:
        condition = (
            f"keyword:{value.upper()}"
            if re.fullmatch(r"(?i)KW-\d{4}", value)
            else f"keyword:{quoted}"
        )
    return f"(database:alphafolddb) AND ({condition})"


re_next_link = re.compile(r'<(.+)>; rel="next"')
retries = Retry(total=5, backoff_factor=0.25, status_forcelist=[500, 502, 503, 504])
session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=retries))


def get_next_link(headers):
    if "Link" in headers:
        match = re_next_link.match(headers["Link"])
        if match:
            return match.group(1)


def get_batch(batch_url):
    while batch_url:
        response = session.get(batch_url)
        response.raise_for_status()
        total = response.headers["x-total-results"]
        yield response, total
        batch_url = get_next_link(response.headers)


def search_url(query: str, size: int) -> str:
    return (
        requests.Request(
            "GET",
            UNIPROT_SEARCH,
            params={
                "fields": UNIPROT_COLUMNS,
                "format": "tsv",
                "query": query,
                "size": size,
            },
        )
        .prepare()
        .url
    )


def search_uniprot(query: str, limit: int) -> tuple[list[dict], int]:
    url = search_url(query, size=500)
    proteins: list[dict] = []
    header = None
    total = 0
    for batch, total in get_batch(url):
        lines = batch.text.splitlines()
        if header is None:
            header = lines[0].split("\t")
        for line in lines[1:]:
            proteins.append(dict(zip(header, line.split("\t"))))
            if len(proteins) >= limit:
                break
        print(f"{len(proteins)} / {total}")
        if len(proteins) >= limit:
            break
    return proteins, int(total)


# --------------------------------------------------------------------------- #
#  1. Get response from API endpoint
#  This section defines a function to retrieve protein structure prediction data from the AlphaFold Database
#   using its public API.
# --------------------------------------------------------------------------- #
def get_afdb_response(uniprot_id: str):
    """Fetches prediction data from the AlphaFold Database API for a given UniProt ID.

    Args:
      uniprot_id (str): The UniProt accession ID for the protein of interest. Example: "P12345"

    Returns:
      dict or str: A dictionary containing the API response (usually a list of prediction entries) if the request is successful (HTTP status code 200).
      Returns an error message string if the request fails."""
    uniprot_id = uniprot_id.upper()  # this will ensure that the UniProt ID is uppercase
    request_url = (
        AFDB_API_BASE_URL + uniprot_id
    )  # construct the full URL by appending the base URL and the UniProt ID
    print(f"➡️ Sending request to: {request_url}")  # to see the URL

    response = requests.get(request_url, timeout=10)  # query the URL
    if response.status_code == 200:  # if the request was successful (200 means OK)
        print(f"✅ Success! Received data for {uniprot_id}")
        return response.json()

    else:
        error_message = f"❌ API request failed for {uniprot_id} with status: {response.status_code} - {response.reason}"
        print(error_message)
        if response.status_code == 404 or response.status_code == 400:
            print(
                f"ℹ️  Hint: A status code of 404/400 often means the UniProt ID '{uniprot_id}' is not available in the AlphaFold Database or the ID is incorrect."
            )
        return None


# --------------------------------------------------------------------------- #
#  2. Programmatic file download
#  A significant advantage of utilizing API endpoints is the ability to automate data retrieval,
#  thereby eliminating the need for manual downloads.
#  The following code cells will demonstrate how to programmatically download structure files and associated data
#  (such as pLDDT values, Predicted Aligned Error (PAE) images, and AlphaMissense annotations, where available) directly
#  to a specified folder within your Google Drive.
# --------------------------------------------------------------------------- #
def collect_urls(entry: dict, file_type: str) -> dict:
    """Из одной записи API собрать словарь «тип файла → ссылка».
    file_type = "all" — все доступные, иначе только один тип."""
    urls_to_download = {}
    for ftype, (
        field,
        _,
    ) in FILE_TYPES.items():  # check it was successful and that we have a list
        if ftype != "all" and ftype != file_type:
            continue
        if entry.get(field):  # #check it was successful and that we have a list
            urls_to_download[ftype] = entry[field]
            print(f"🔗 {ftype}: {entry[field]}")
        elif ftype == "alphamissense_tsv":
            print(
                "⚠️ No AlphaMissense annotations available for this entry. Is it human?"
            )
    if not urls_to_download:
        print("No downloable URLs found in the API response.")
    return urls_to_download


def download_files(
    url_dictionary: dict, destination_path: Path, tracker: Tracker, entry_id: str
) -> dict:

    if not destination_path.exists():  # if the folder doesn't exist
        os.makedirs(destination_path)

    downloaded = 0
    skipped = 0
    failed = 0
    for file_type, url in url_dictionary.items():
        if not url:  #  Skip if a URL is empty or None
            print(f"🚫 Skipping {file_type} as URL is missing.")
            continue
        original_filename = url.split("/")[-1]
        file_extension = (
            "." + original_filename.split(".")[-1] if "." in original_filename else ""
        )

        if file_type == "cif":
            filename = f"{entry_id}_model{file_extension or '.cif'}"
        elif file_type == "pdb":
            filename = f"{entry_id}_model{file_extension or '.pdb'}"
        elif file_type == "pae_image":
            filename = f"{entry_id}_pae{file_extension or '.png'}"
        elif file_type == "alphamissense_tsv":  # Assuming you used this key earlier
            filename = f"{entry_id}_alphamissense{file_extension or '.tsv'}"  # Or .csv if it's a csv
        else:
            filename = f"{entry_id}_{file_type}{file_extension}"

        full_file_path = destination_path / filename
        if not tracker.should_download(entry_id, file_type, full_file_path):
            print(f"Already have {filename}, skipping")
            skipped += 1
            continue
        try:
            file_response = requests.get(url, stream=True, timeout=30)
            if file_response.status_code == 200:
                tmp_path = full_file_path.with_name(full_file_path.name + ".part")
                with open(tmp_path, "wb") as f:
                    for chunk in file_response.iter_content(
                        chunk_size=8192
                    ):  # Download in 8KB chunks
                        f.write(chunk)
                tmp_path.rename(
                    full_file_path
                )  # Rename to final filename after download
                #   print(f"  ✅ Successfully downloaded: {filename}")
                size = full_file_path.stat().st_size
                tracker.record(
                    entry_id,
                    file_type,
                    "ok",
                    filename=filename,
                    url=url,
                    http_status=200,
                    size_bytes=size,
                )
                downloaded += 1
            elif file_response.status_code == 404:
                print(f"Not found on server: {file_type}")
                tracker.record(
                    entry_id,
                    file_type,
                    "not_found",
                    filename=filename,
                    url=url,
                    http_status=404,
                    error_message=file_response.reason,
                )
                failed += 1
            else:
                print(
                    f"  ❌ Failed to download {file_type}. Status: {file_response.status_code} - {file_response.reason}"
                )
                tracker.record(
                    entry_id,
                    file_type,
                    "failed",
                    filename=filename,
                    url=url,
                    http_status=file_response.status_code,
                    error_message=file_response.reason,
                )
                failed += 1

        except requests.exceptions.RequestException as e:
            print(f"  🚫 Error downloading {file_type} from {url}: {e}")
        except Exception as e:
            print(f"  🛑 An unexpected error occurred with {file_type}: {e}")
            print("-" * 20)  # Separator for each file download attempt

    tracker.record(
        entry_id, file_type, "failed", filename=filename, url=url, error_message=str(e)
    )
    failed += 1
    print("=" * 30 + f"\n🏁 All attempted downloads for {entry_id} complete.")
    return {"downloaded": downloaded, "skipped": skipped, "failed": failed}


def download(query: str, wanted: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    tracker = Tracker(DB_PATH)
    proteins, total = search_uniprot(query, LIMIT)
    stats = {
        "total": total,
        "processed": len(proteins),
        "ok": 0,
        "no model": 0,
        "models": 0,
        "files": 0,
        "skipped": 0,
        "failed": 0,
    }
    rows = []
    try:
        for protein in proteins:
            acc = protein["Entry"]
            api_response = get_afdb_response(acc)
            if not (isinstance(api_response, list) and api_response):
                stats["no model"] += 1
                rows.append({**protein, "model": "", "status": "no model", "plddt": ""})
                continue
            stats["ok"] += 1
            for entry in api_response:
                urls_to_download = collect_urls(entry, wanted)
                res = download_files(urls_to_download, out, tracker, entry["entryId"])
                stats["files"] += res["downloaded"]
                stats["skipped"] += res["skipped"]
                stats["models"] += 1
                rows.append(
                    {
                        **protein,
                        "model": entry["entryId"],
                        "status": "ok",
                        "plddt": entry.get("globalMetricValue", ""),
                    }
                )
    finally:
        tracker.close()

    with open(out / "metadata.tsv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=list(rows[0]) if rows else ["Entry"], delimiter="\t"
        )
        writer.writeheader()
        writer.writerows(rows)
    return stats


def read_csv(name: str) -> list[dict]:
    path = REFS_DIR / name
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


@app.route("/", methods=["GET", "POST"])
def index():
    message = query = error = None
    form = request.form if request.method == "POST" else {}

    if request.method == "POST":
        chosen = form.get("filter", "gene")
        value = (form.get("value") or "").strip()
        file_type = form.get("format", "cif")

        if chosen not in FILTERS or file_type not in FILE_TYPES:
            error = "Неизвестный фильтр или тип файла."
        elif not value:
            error = "Введите значение фильтра."
        else:
            query = build_query(chosen, value)
            try:
                stats = download(query, file_type, OUT_DIR)
                message = (
                    f"Найдено белков: {stats['total']}, обработано: {stats['processed']} "
                    f"(с моделью {stats['ok']}, без модели {stats['no model']}), "
                    f"скачано файлов: {stats['files']}, пропущено (уже были): {stats['skipped']}, "
                    f"ошибок: {stats['failed']}. Папка: {OUT_DIR}"
                )
            except requests.HTTPError as e:
                error = f"Сервер ответил ошибкой: {e}"
            except Exception as e:  # noqa: BLE001
                error = f"Ошибка: {e}"

    return render_template(
        "base.html",
        filters=FILTERS,
        file_types=FILE_TYPES,
        limit=LIMIT,
        organisms=read_csv("organisms.csv"),
        keywords=read_csv("keywords.csv"),
        form=form,
        message=message,
        query=query,
        error=error,
        tab=form.get("tab", "download"),
    )


if __name__ == "__main__":
    app.run(port=5002, debug=True)
