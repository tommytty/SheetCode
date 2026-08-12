# 1. Load the .env file
# 2. Read the two variables from environment
# 3. Build credentials from service account JSON
# 4. Build a sheets service object
# 5. Read a range and print results


import os
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
# Load the dotenv file
load_dotenv()

sheet_id = os.getenv("SHEET_ID")

if not sheet_id:
    raise RuntimeError("SHEET_ID is not set")

google_credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH")

if not google_credentials_path:
    raise RuntimeError("Credentials not set")

scopes = ["https://www.googleapis.com/auth/spreadsheets"]

credentials = service_account.Credentials.from_service_account_file(
    google_credentials_path, scopes=scopes
)

sheet_object = build("sheets", "v4", credentials=credentials)
res = sheet_object.spreadsheets().values().get(spreadsheetId=sheet_id, range="Sheet1!A7:G7").execute()
print(res)
