import boto3
import datetime
import json
import os
from fastapi import FastAPI
from mangum import Mangum
from pydantic import BaseModel

TABLE_NAME = os.environ['TABLE_NAME']
QUEUE_NAME = os.environ['QUEUE_NAME']

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)
sqs = boto3.resource('sqs')
queue = sqs.get_queue_by_name(QueueName=QUEUE_NAME)

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

app = FastAPI()

@app.post("/request")
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

@app.get("/request")
def get_request(userId: str):
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

lambda_handler = Mangum(app, lifespan="off")