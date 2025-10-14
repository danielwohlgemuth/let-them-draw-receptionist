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
STRIPE_WEBHOOK_SECRET = os.environ['STRIPE_WEBHOOK_SECRET']
WEBSITE_URL = os.environ['WEBSITE_URL']
USER_POOL_ID = os.environ['USER_POOL_ID']
PRE_SIGNED_URL_EXPIRATION = 3600  # 60 seconds * 60 minutes = 1 hour

cognito = boto3.client('cognito-idp')
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

class Request2(BaseModel):
    requestId: str
    requirements: Requirements

class RequestList(BaseModel):
    requestId: str
    status: str
    requestDate: str
    requirements: Requirements
    artworkUrl: str | None = None

class Shape(BaseModel):
    shape: str
    priceId: str
    price: str

class RequestResponse(BaseModel):
    url: str

app = FastAPI(openapi_url="/api/openapi.json", docs_url='/api/docs')

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

def get_user_email(user_id):
    """Retrieve user email from Cognito."""
    user_response = cognito.admin_get_user(
        UserPoolId=USER_POOL_ID,
        Username=user_id
    )

    for attr in user_response['UserAttributes']:
        if attr['Name'] == 'email':
            return attr['Value']

    raise ValueError("User email not found")

@app.post("/api/request")
async def create_request(
    request: Request2,
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

    response = shapes_table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key('shapeName').eq(request.requirements.shape)
    )
    items = response.get('Items', [])
    if not items:
        raise HTTPException(status_code=404, detail="Shape not found")

    price = items[0].get('price', '')

    if price == '0':
        body = {
            'userId': user_id,
            'requestId': request.requestId,
        }
        response = queue.send_message(MessageBody=json.dumps(body))
        return RequestResponse(url=f"{WEBSITE_URL}/artwork/{request.requestId}")

    price_id = items[0].get('priceId', '')
    checkout_session = stripe.checkout.Session.create(
        line_items=[{'price': price_id, 'quantity': 1}],
        mode='payment',
        customer_email=get_user_email(user_id),
        success_url=f"{WEBSITE_URL}/artwork/{request.requestId}",
        cancel_url=f"{WEBSITE_URL}/request",
    )

    table.update_item(
        Key={
            'userId': user_id,
            'requestId': request.requestId
        },
        UpdateExpression='set checkoutSessionId = :checkoutSessionId',
        ExpressionAttributeValues={
            ':checkoutSessionId': checkout_session.id
        }
    )
    return RequestResponse(url=checkout_session.url)

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
        list_request = RequestList(
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

    return RequestList(
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
    shapes = [Shape(shape=item.get('shapeName', ''), price=item.get('price', ''), priceId='') for item in items]
    return shapes

@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    event = stripe.Webhook.construct_event(
        payload,
        request.headers.get('Stripe-Signature'),
        STRIPE_WEBHOOK_SECRET
    )

    if event['type'] == 'checkout.session.completed' or event['type'] == 'checkout.session.async_payment_succeeded':
        session = event['data']['object']
        if session['payment_status'] == 'paid':
            checkout_session_id = session['id']
            response = table.query(
                IndexName='CheckoutSessionIdIndex',
                KeyConditionExpression=boto3.dynamodb.conditions.Key('checkoutSessionId').eq(checkout_session_id)
            )
            items = response.get('Items', [])
            if not items:
                raise HTTPException(status_code=404, detail="Checkout session not found")
            request_id = items[0]['requestId']
            user_id = items[0]['userId']
            table.update_item(
                Key={
                    'userId': user_id,
                    'requestId': request_id
                },
                UpdateExpression='set #status = :status',
                ExpressionAttributeNames={
                    '#status': 'status'
                },
                ExpressionAttributeValues={
                    ':status': 'paid'
                }
            )
            body = {
                'userId': user_id,
                'requestId': request_id,
            }
            response = queue.send_message(MessageBody=json.dumps(body))

    elif event['type'] == 'checkout.session.async_payment_failed' or event['type'] == 'checkout.session.expired':
        session = event['data']['object']
        checkout_session_id = session['id']
        response = table.query(
            IndexName='CheckoutSessionIdIndex',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('checkoutSessionId').eq(checkout_session_id)
        )
        items = response.get('Items', [])
        if not items:
            raise HTTPException(status_code=404, detail="Checkout session not found")
        request_id = items[0]['requestId']
        user_id = items[0]['userId']
        table.update_item(
            Key={
                'userId': user_id,
                'requestId': request_id
            },
            UpdateExpression='set #status = :status',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':status': 'failed'
            }
        )
    return {'status': 'success'}

lambda_handler = Mangum(app, lifespan="off")