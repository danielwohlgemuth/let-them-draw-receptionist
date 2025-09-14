import boto3
import datetime
import json
import os
from fastapi import FastAPI
from mangum import Mangum
from pydantic import BaseModel

TABLE_NAME = os.environ['TABLE_NAME']
QUEUE_NAME = os.environ['QUEUE_NAME']
BUCKET_NAME = os.environ['BUCKET_NAME']
PRE_SIGNED_URL_EXPIRATION = 3600 # 60 seconds * 60 minutes = 1 hour

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)
sqs = boto3.resource('sqs')
queue = sqs.get_queue_by_name(QueueName=QUEUE_NAME)
s3 = boto3.resource('s3')
bucket = s3.Bucket(BUCKET_NAME)

class Requirements(BaseModel):
    shape: str
    color: str

class Request(BaseModel):
    userId: str
    requestId: str
    requirements: Requirements

class ListRequest(BaseModel):
    userId: str
    requestId: str
    status: str
    requestDate: str
    requirements: Requirements
    artworkUrl: str

app = FastAPI()

@app.post("/api/request")
def create_request(request: Request):
    item = {
        'userId': request.userId,
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
        'userId': request.userId,
        'requestId': request.requestId,
    }
    response = queue.send_message(MessageBody=json.dumps(body))

    return {
        "statusCode": 200,
        "body": "Success! " + response.get('MessageId')
    }

@app.get("/api/request/{userId}")
def get_requests(userId: str):
    response = table.query(
        IndexName='UserIdIndex',
        KeyConditionExpression=boto3.dynamodb.conditions.Key('userId').eq(userId)
    )
    items = response.get('Items', [])
    result = []
    for item in items:
        requirements = item.get('requirements', {})
        list_request = ListRequest(
            userId=item.get('userId', ''),
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

@app.get("/api/request/{userId}/{requestId}")
def get_request_details(userId: str, requestId: str):
    """
    Retrieve the details of a specific request including artwork URL based on userId and requestId.
    """
    response = table.get_item(
        Key={
            'userId': userId,
            'requestId': requestId
        }
    )

    if 'Item' not in response:
        return {
            "statusCode": 400,
            "body": "Request not found"
        }

    item = response['Item']
    requirements = item.get('requirements', {})

    object_key = f'artwork/{userId}/{requestId}.png'
    presigned_url = s3.meta.client.generate_presigned_url(
        'get_object',
        Params={'Bucket': BUCKET_NAME, 'Key': object_key},
        ExpiresIn=PRE_SIGNED_URL_EXPIRATION
    )

    return ListRequest(
        userId=item.get('userId', ''),
        requestId=item.get('requestId', ''),
        status=item.get('status', ''),
        requestDate=item.get('requestDate', ''),
        requirements=Requirements(
            shape=requirements.get('shape', ''),
            color=requirements.get('color', '')
        ),
        artworkUrl=presigned_url
    )

lambda_handler = Mangum(app, lifespan="off")