"""Deploy this container to Bedrock AgentCore Runtime.

Prereqs:
  pip install boto3
  aws configure  (or env AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
  An ECR repo + execution role (see README / deploy_agentcore.sh for bootstrap)

Flow:
  1. Build + push Docker image to ECR (done by deploy_agentcore.sh, or do it manually)
  2. This script calls bedrock-agentcore-control CreateAgentRuntime

Usage:
  python deploy/deploy_agentcore.py --image-uri 123456789012.dkr.ecr.us-east-1.amazonaws.com/jira-agents:latest \
      --role-arn arn:aws:iam::123456789012:role/AgentCoreExecutionRole \
      --agent-name jira-knowledge-agent
"""
from __future__ import annotations

import argparse
import json

import boto3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-uri", required=True)
    ap.add_argument("--role-arn", required=True)
    ap.add_argument("--agent-name", default="jira-knowledge-agent")
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)
    resp = client.create_agent_runtime(
        agentRuntimeName=args.agent_name,
        agentRuntimeArtifact={"containerConfiguration": {"containerUri": args.image_uri}},
        networkConfiguration={"networkMode": "PUBLIC"},
        roleArn=args.role_arn,
    )
    print(json.dumps(resp, indent=2, default=str))
    print("\nInvoke with:")
    print(
        f"  aws bedrock-agentcore invoke-agent-runtime "
        f"--agent-runtime-id {resp.get('agentRuntimeId')} "
        f"--payload '{{\"agent\": \"knowledge\", \"message\": \"How was PROJ-123 fixed?\"}}' "
        f"--region {args.region} out.json"
    )


if __name__ == "__main__":
    main()
