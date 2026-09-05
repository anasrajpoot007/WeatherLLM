import requests

url = "https://www.thesportsdb.com/api/v1/json/123/eventsnext.php"

params = {
    "id": "133604"
}

response = requests.get(url, params=params)

print("Status:", response.status_code)

data = response.json()

print(data)