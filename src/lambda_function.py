from typing import Dict, Any
      
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    return {
        "statusCode": 200,
        "message": "Success"
    }