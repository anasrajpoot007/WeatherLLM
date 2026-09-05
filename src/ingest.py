import requests
import json
from datetime import datetime

apis = {
    "teams": ("https://www.thesportsdb.com/api/v1/json/123/searchteams.php", {"t": "Arsenal"}),
    "players": ("https://www.thesportsdb.com/api/v1/json/123/lookup_all_players.php", {"id": "133604"}),
    "events": ("https://www.thesportsdb.com/api/v1/json/123/eventsnext.php", {"id": "133604"})
}

for name, (url, params) in apis.items():

    response = requests.get(url, params=params, timeout=10)

    print(name, "Status:", response.status_code)

    data = response.json()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"data/raw/{name}_{timestamp}.json"

    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)

    print("Saved:", filename)