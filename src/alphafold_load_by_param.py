import requests

# type 1
r1 = requests.get("https://alphafold.ebi.ac.uk/api/prediction/P04637")

# type 2
r2 = requests.get("https://alphafold.ebi.ac.uk/api/uniprot/summary/Q5VSL9.json")


# print(response) # 200 - good

if r1.status_code == 200:
    print(len(r1.json()))
print(len(r2.json()))

print(dict(r1.headers))
print(r1.text)
