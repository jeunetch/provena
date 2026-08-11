"""
Fetch the current LIDO Event Type Vocabulary from the LIDO Terminology
SPARQL endpoint.

Purpose: confirm which transaction/event terms (e.g. "Purchase", "Gift",
"Restitution") exist as native LIDO Event Type concepts, so the
transaction_type mapping in the LIDO adapter (schema.py) stays correct if
the vocabulary is revised upstream. As of this writing, LIDO has no native
term for "Forced sale" or "Confiscation" — see README for how the adapter
handles this via the change_of_legal_title fallback plus free-text
duress-keyword matching.

Usage:
    pip install requests
    python fetch_lido_event_types.py

Confirmed working endpoint: the human-facing query editor lives at
https://terminology.lido-schema.org/sparql.php, but that page only submits
queries to the actual proxy at /ws/tsproxy.php — use that endpoint directly.
"""

import requests

ENDPOINT = "https://terminology.lido-schema.org/ws/tsproxy.php"

QUERY = """
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT ?concept ?label WHERE {
  ?concept skos:inScheme <http://terminology.lido-schema.org/eventType> .
  ?concept skos:prefLabel ?label .
  FILTER(lang(?label) = "en")
}
ORDER BY ?label
"""


def main():
    resp = requests.get(
        ENDPOINT,
        params={"query": QUERY, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    bindings = data.get("results", {}).get("bindings", [])
    print(f"{len(bindings)} Event Type terms found:\n")
    for row in bindings:
        uri = row.get("concept", {}).get("value", "")
        label = row.get("label", {}).get("value", "")
        local_id = uri.rstrip("/").split("/")[-1]
        print(f"{local_id}\t{label}")


if __name__ == "__main__":
    main()
