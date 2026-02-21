import urllib.request
import json
req = urllib.request.Request("http://127.0.0.1:8000/copilotkit/info")
try:
    response = urllib.request.urlopen(req)
    print(response.read().decode('utf-8'))
except Exception as e:
    print("Error:", e)
