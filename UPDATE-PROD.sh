#!/usr/bin/env bash
ssh docker-server 'cd containers/gco-llm-pkm && ./UPDATE-PROD.sh && timeout 30 docker compose logs -f' 
