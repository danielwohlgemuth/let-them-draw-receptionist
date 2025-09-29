import base64
import boto3
import datetime
import json
import logging
import os
import stripe
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from mangum import Mangum
from pydantic import BaseModel

logger = logging.getLogger()
logger.setLevel(logging.INFO)


TABLE_NAME = os.environ['TABLE_NAME']
SHAPES_TABLE_NAME = os.environ['SHAPES_TABLE_NAME']
QUEUE_NAME = os.environ['QUEUE_NAME']
BUCKET_NAME = os.environ['BUCKET_NAME']
STRIPE_API_KEY = os.environ['STRIPE_API_KEY']
WEBSITE_URL = os.environ['WEBSITE_URL']
PRE_SIGNED_URL_EXPIRATION = 3600  # 60 seconds * 60 minutes = 1 hour

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)
shapes_table = dynamodb.Table(SHAPES_TABLE_NAME)
sqs = boto3.resource('sqs')
queue = sqs.get_queue_by_name(QueueName=QUEUE_NAME)
s3 = boto3.resource('s3')
bucket = s3.Bucket(BUCKET_NAME)
stripe.api_key = STRIPE_API_KEY

class Requirements(BaseModel):
    shape: str
    color: str

class Request(BaseModel):
    requestId: str
    requirements: Requirements

class ListRequest(BaseModel):
    requestId: str
    status: str
    requestDate: str
    requirements: Requirements
    artworkUrl: str | None = None

class Shape(BaseModel):
    shape: str
    priceId: str
    price: str

class Checkout(BaseModel):
    url: str

app = FastAPI()

security = HTTPBearer()

def get_jwt_payload(token: str) -> dict:
    try:
        _, payload_encoded, _ = token.split('.')
        payload_encoded += '=' * (-len(payload_encoded) % 4)
        payload_decoded = base64.urlsafe_b64decode(payload_encoded)
        return json.loads(payload_decoded)
    except Exception as e:
        logger.warning(f"Failed to decode JWT token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def get_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = get_jwt_payload(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No 'sub' claim in token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user_id
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Failed to process token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not process token",
            headers={"WWW-Authenticate": "Bearer"},
        )

@app.post("/api/request")
async def create_request(
    request: Request,
    user_id: str = Depends(get_user_id)
):
    item = {
        'userId': user_id,
        'requestId': request.requestId,
        'status': 'new',
        'requestDate': datetime.datetime.now().isoformat(),
        'requirements': {
            'shape': request.requirements.shape,
            'color': request.requirements.color,
        },
    }
    table.put_item(Item=item)

    body = {
        'userId': user_id,
        'requestId': request.requestId,
    }
    response = queue.send_message(MessageBody=json.dumps(body))

    return {
        "statusCode": 200,
        "body": "Success! " + response.get('MessageId')
    }

@app.get("/api/request")
async def get_requests(user_id: str = Depends(get_user_id)):
    response = table.query(
        IndexName='UserIdIndex',
        KeyConditionExpression=boto3.dynamodb.conditions.Key('userId').eq(user_id),
        ScanIndexForward=False
    )
    items = response.get('Items', [])
    result = []
    for item in items:
        requirements = item.get('requirements', {})
        list_request = ListRequest(
            requestId=item.get('requestId', ''),
            status=item.get('status', ''),
            requestDate=item.get('requestDate', ''),
            requirements=Requirements(
                shape=requirements.get('shape', ''),
                color=requirements.get('color', '')
            )
        )
        result.append(list_request)
    return result

@app.get("/api/request/{requestId}")
async def get_request_details(
    requestId: str,
    user_id: str = Depends(get_user_id)
):
    response = table.get_item(
        Key={
            'userId': user_id,
            'requestId': requestId
        }
    )

    if 'Item' not in response:
        raise HTTPException(status_code=404, detail="Request not found")

    item = response['Item']
    requirements = item.get('requirements', {})
    status = item.get('status', '')

    artwork_url = None
    if status == 'done':
        object_key = f'artwork/{user_id}/{requestId}.png'
        artwork_url = s3.meta.client.generate_presigned_url(
            'get_object',
            Params={'Bucket': BUCKET_NAME, 'Key': object_key},
            ExpiresIn=PRE_SIGNED_URL_EXPIRATION
        )

    return ListRequest(
        requestId=item.get('requestId', ''),
        status=status,
        requestDate=item.get('requestDate', ''),
        requirements=Requirements(
            shape=requirements.get('shape', ''),
            color=requirements.get('color', '')
        ),
        artworkUrl=artwork_url
    )

@app.get("/api/shapes")
async def get_shapes(user_id: str = Depends(get_user_id)):
    response = shapes_table.scan()
    items = response.get('Items', [])
    shapes = [Shape(shape=item.get('shapeName', ''), price=item.get('price', '')) for item in items]
    return shapes

@app.post("/api/checkout")
async def checkout(shape: str, user_id: str = Depends(get_user_id)):
    response = shapes_table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key('shapeName').eq(shape)
    )
    items = response.get('Items', [])
    if not items:
        raise HTTPException(status_code=404, detail="Shape not found")
    price_id = items[0].get('priceId', '')
    checkout_session = stripe.checkout.Session.create(
        line_items=[
            {
                'price': price_id,
                'quantity': 1,
            },
        ],
        mode='payment',
        success_url=WEBSITE_URL + f'/artwork/{requestId}',
        cancel_url=WEBSITE_URL + '/request',
    )
    return Checkout(url=checkout_session.url)

lambda_handler = Mangum(app, lifespan="off")