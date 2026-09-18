"""Bedrock Titan embedding wrapper (batch-friendly)."""
from __future__ import annotations

import json

import boto3


class Embedder:
    def __init__(self, model_id: str = "amazon.titan-embed-text-v2", region: str = "us-east-1"):
        self.model_id = model_id
        self.client = boto3.client("bedrock-runtime", region_name=region)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for t in texts:
            body = json.dumps({"inputText": t})
            resp = self.client.invoke_model(modelId=self.model_id, body=body)
            payload = json.loads(resp["body"].read())
            vectors.append(payload["embedding"])
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]
