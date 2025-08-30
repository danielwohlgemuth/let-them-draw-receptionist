import boto3
import datetime
import json
import os
from typing import Dict, Any

TABLE_NAME = os.environ['TABLE_NAME']
QUEUE_NAME = os.environ['QUEUE_NAME']

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)
sqs = boto3.resource('sqs')
queue = sqs.get_queue_by_name(QueueName=QUEUE_NAME)

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    item = {
        'userId': event['userId'],
        'requestId': event['requestId'],
        'status': 'new',
        'requestDate': datetime.datetime.now().isoformat(),
        'requirements': {
            'shape': event['requirements']['shape'],
            'color': event['requirements']['color'],
        },
    }
    table.put_item(Item=json.dumps(item))

    body = {
        'userId': event['userId'],
        'requestId': event['requestId'],
    }
    response = queue.send_message(MessageBody=json.dumps(body))

    return {
        "statusCode": 200,
        "body": "Success! " + response.get('MessageId')
    }