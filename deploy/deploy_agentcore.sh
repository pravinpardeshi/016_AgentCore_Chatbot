#!/usr/bin/env bash
# Build + push to ECR, then create the AgentCore Runtime.
# Usage: ./deploy/deploy_agentcore.sh [agent-name] [region]
set -euo pipefail

AGENT_NAME="${1:-jira-knowledge-agent}"
REGION="${2:-${AWS_REGION:-us-east-1}}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${AGENT_NAME}"
ROLE_ARN="${AGENTCORE_ROLE_ARN:?Set AGENTCORE_ROLE_ARN to your AgentCore execution role ARN}"

aws ecr describe-repositories --repository-names "$AGENT_NAME" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$AGENT_NAME" --region "$REGION" >/dev/null

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

docker build -t "${AGENT_NAME}:latest" .
docker tag "${AGENT_NAME}:latest" "${REPO}:latest"
docker push "${REPO}:latest"

python deploy/deploy_agentcore.py --image-uri "${REPO}:latest" --role-arn "$ROLE_ARN" \
  --agent-name "$AGENT_NAME" --region "$REGION"
