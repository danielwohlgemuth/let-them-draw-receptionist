#!/bin/bash

PROFILE=${1:-default}
export AWS_PROFILE=$PROFILE

FUNCTION_NAME=$(aws cloudformation describe-stacks --stack-name ReceptionistStack --query "Stacks[0].Outputs[?OutputKey=='FunctionName'].OutputValue | [0]" --output text --profile ${AWS_PROFILE})
FUNCTION_URL=$(aws lambda get-function --function-name $FUNCTION_NAME --query "Code.Location" --output text --profile ${AWS_PROFILE})
curl ${FUNCTION_URL} --output lambda.zip
unzip -d src lambda.zip
rm lambda.zip
