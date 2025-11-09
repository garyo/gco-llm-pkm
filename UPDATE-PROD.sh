#!/usr/bin/env bash
ssh docker-server 'cd containers/gco-llm-pkm && git pull && docker compose up -d --build && timeout 30 docker compose logs -f' 
