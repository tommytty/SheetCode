import os
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from pydantic import BaseModel

if TYPE_CHECKING:
    from googleapiclient._apis.sheets.v4 import SheetsResource

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


load_dotenv()


class SheetConfig(BaseModel):
    sheet_id: str
    google_credentials_path: Path


def load_config() -> SheetConfig:
    """Grabs the .env variables and loads them up. Runs a quick check and returns
    a base model object SheetConfig"""

    sheet_id = os.getenv("SHEET_ID")
    google_credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH")

    if not sheet_id:
        raise RuntimeError("SHEET_ID is not set")

    if not google_credentials_path:
        raise RuntimeError("GOOGLE_CREDENTIALS_PATH not set")

    config = SheetConfig(
        sheet_id=sheet_id, google_credentials_path=Path(google_credentials_path)
    )
    return config


def _get_credentials(config: SheetConfig) -> Credentials:
    credentials = Credentials.from_service_account_file(
        config.google_credentials_path, scopes=SCOPES
    )
    return credentials


def get_service(config: SheetConfig) -> "SheetsResource":
    """Creates the service_object required for the client to use"""

    credentials = _get_credentials(config)
    sheet_object = build("sheets", "v4", credentials=credentials)

    return sheet_object
