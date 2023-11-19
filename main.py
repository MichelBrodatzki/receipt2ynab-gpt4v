import os
import requests
import base64
import datetime

from fastapi import FastAPI, Header, Response, status
from io import BytesIO
from typing import Annotated, Union
from pydantic import BaseModel
from PIL import Image
from openai import OpenAI

from dotenv import load_dotenv

class Picture(BaseModel):
    encoded_file: str

load_dotenv()

# This server's secret has to be set or else this server would be publicly accessible
if os.environ.get("SERVER_SECRET") is None:
    raise EnvironmentError("Environmental variable SERVER_SECRET is not set. Server won't start in insecure state.")

# Check if YNAB API key works and if budget id as well as account id is valid
r = requests.get("https://api.ynab.com/v1/user", headers={"Authorization": f"Bearer {os.environ.get('YNAB_API_KEY', '')}"})
if r.status_code == 200:
    print ("YNAB API key is valid")
else:
    raise ValueError(f"YNAB API key isn't valid ({r.status_code})")

r = requests.get(f"https://api.ynab.com/v1/budgets/{os.environ.get('YNAB_BUDGET_ID', '')}/settings", headers={"Authorization": f"Bearer {os.environ.get('YNAB_API_KEY', '')}"})
if r.status_code == 200:
    print ("YNAB budget id is valid")
else:
    raise ValueError(f"YNAB budget id isn't valid ({r.status_code})")

r = requests.get(f"https://api.ynab.com/v1/budgets/{os.environ.get('YNAB_BUDGET_ID', '')}/accounts/{os.environ.get('YNAB_ACCOUNT_ID', '')}", headers={"Authorization": f"Bearer {os.environ.get('YNAB_API_KEY', '')}"})
if r.status_code == 200:
    print ("YNAB account id is valid")
else:
    raise ValueError(f"YNAB account id isn't valid ({r.status_code})")

# Check if category id was set and if it's valid
if os.environ.get('YNAB_CATEGORY_ID') is not None:
    r = requests.get(f"https://api.ynab.com/v1/budgets/{os.environ.get('YNAB_BUDGET_ID', '')}/categories/{os.environ.get('YNAB_CATEGORY_ID', '')}", headers={"Authorization": f"Bearer {os.environ.get('YNAB_API_KEY', '')}"})
    if r.status_code == 200:
        print ("YNAB category id is valid")
    else:
        raise ValueError(f"YNAB category id isn't valid ({r.status_code})")


openai_client = OpenAI()
app = FastAPI()

@app.post("/receipt")
def new_receipt(picture: Picture, authorization: Annotated[Union[str, None], Header()], response: Response):
    if authorization == f"Bearer {os.environ.get('SERVER_SECRET')}":
        image = Image.open(BytesIO(base64.b64decode(picture.encoded_file)))
        if image.width > 1024 or image.height > 1024:
            response.status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            return {"result": "image too large"}
        
        vision_result = openai_client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What's the total price on this receipt? Respond only with the numeric value without a decimal seperator or ```ERROR``` if you couldn't find the total price."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/{image.format};base64,{picture.encoded_file}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=300
        )

        amount = vision_result.choices[0].message.content

        if amount is None or amount == "ERROR":
            response.status_code = status.HTTP_204_NO_CONTENT
            return {"result": "no_amount"}

        try:
            milliamount = int(amount) * 10
        except ValueError as verr:
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"result": "amount_conversion_error", "amount": amount}
        except Exception as err:
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"result": "error", "details": str(err)}

        transaction_request = requests.post(f"https://api.ynab.com/v1/budgets/{os.environ.get('YNAB_BUDGET_ID', '')}/transactions", json={
            "transaction": {
                "account_id": os.environ.get('YNAB_ACCOUNT_ID', ''),
                "category_id": os.environ.get('YNAB_CATEGORY_ID'),
                "date": datetime.date.today().isoformat(),
                "amount": -milliamount,
                "memo": "AI assisted",
                "cleared": "uncleared",
                "approved": False,
            }
        }, headers={"Authorization": f"Bearer {os.environ.get('YNAB_API_KEY', '')}"})

        if transaction_request.status_code == 201:
            return {"result": "success", "milliamount": milliamount}
        else:
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"result": "ynab_api_error", "milliamount": milliamount, "error_code": transaction_request.status_code}
    else:
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return {"result": "error"}